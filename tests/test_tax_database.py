"""Real PostgreSQL transaction tests in an isolated, disposable schema.

Enabled only with TAX_TEST_DATABASE_URL; never use the production DATABASE_URL.
"""
import asyncio
import hashlib
import io
import json
import os
import unittest
import uuid
import zipfile
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
import tax_accounting as tax
from tax_rules import parse_import, import_template
from tax_export import export_bundle


class Clock:
    @classmethod
    def now(cls, tz=None):
        return datetime(2027,4,20,tzinfo=timezone.utc)


def row(key,kind='income',amount='1000',when='10.08.2026',source='',original='',year=2026):
    return parse_import(import_template()+f'{key};{when};Документ №1;{kind};{amount};{source};{original};Тест;{year}\n'.encode())[0]


@unittest.skipUnless(os.getenv('TAX_TEST_DATABASE_URL'),'requires disposable PostgreSQL test database')
class TaxDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema='tax_test_'+uuid.uuid4().hex
        self.admin=await asyncpg.connect(os.environ['TAX_TEST_DATABASE_URL'])
        await self.admin.execute(f'CREATE SCHEMA {self.schema}')
        self.pool=await asyncpg.create_pool(os.environ['TAX_TEST_DATABASE_URL'],min_size=1,max_size=6,
                                          server_settings={'search_path':self.schema})
        self.patches=[patch.object(tax.db._legacy,'pool',self.pool),patch.object(tax.db,'_ensure_pool',AsyncMock()),
                      patch.object(tax,'_SCHEMA_READY',False),patch.object(tax,'datetime',Clock)]
        for p in self.patches:p.start()
        await tax.ensure_schema()
        async with self.pool.acquire() as c:
            await c.execute('''CREATE TABLE booking_events(id BIGSERIAL,booking_id INT,event_type TEXT,details JSONB,created_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE bookings(id INT,tinkoff_payment_id TEXT,amount BIGINT,booking_type TEXT,balance_credited BOOLEAN,status TEXT,refund_status TEXT);
                CREATE TABLE fiscal_receipts(payment_id TEXT,receipt_kind TEXT,supplier_inn TEXT,amount BIGINT);
                CREATE TABLE subscriptions(id INT,payment_id TEXT,total_price NUMERIC,supplier_inn TEXT,activated_at TIMESTAMPTZ);
                CREATE TABLE agent_reports(id INT,report_key TEXT,tutor_id INT,commission_amount_kop BIGINT,created_at TIMESTAMPTZ DEFAULT NOW());''')

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        await self.pool.close()
        await self.admin.execute(f'DROP SCHEMA {self.schema} CASCADE')
        await self.admin.close()

    async def seed_payment(self,pid='100',role='own',amount=100000):
        inn=tax.payments.OPERATOR_INN if role=='own' else '123456789012'
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO fiscal_receipts VALUES($1,$2,$3,$4)',pid,'prepayment',inn,amount)
            await c.execute('INSERT INTO booking_events(booking_id,event_type,details) VALUES(1,$1,$2::jsonb)',
                            'paid',json.dumps(dict(payment_id=pid,amount=amount)))

    async def test_sources_only_stage_and_package_counted_once(self):
        await self.seed_payment()
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO bookings VALUES(1,'100',100000,'paid',TRUE,'completed',NULL)")
            await c.execute("INSERT INTO fiscal_receipts VALUES('100','closing',$1,100000)",tax.payments.OPERATOR_INN)
            await c.execute("INSERT INTO subscriptions VALUES(1,'200',5000,$1,NOW())",tax.payments.OPERATOR_INN)
            await c.execute("INSERT INTO agent_reports(id,report_key,tutor_id,commission_amount_kop) VALUES(1,'AR-TEST',2,20000)")
        await tax.sync_sources();await tax.sync_sources()
        count,rows=await tax.unresolved()
        self.assertEqual(count,3)
        self.assertEqual({r['source_key']:r['gross_kop'] for r in rows},
                         {'payment:100':100000,'payment:200':500000,'report:1':20000})
        self.assertEqual((await tax.data_for_year(2026))[1],[])
        await tax.import_entries([row('package',amount='5000',source='payment:200')],1)
        self.assertEqual((await tax.summary(2026,3))[1]['income_kop'],500000)

    async def test_concurrent_import_idempotency_conflict_and_atomic_rollback(self):
        rows=[row('one')]
        results=await asyncio.gather(tax.import_entries(rows,1),tax.import_entries(rows,1))
        self.assertEqual(sum(r['inserted'] for r in results),1)
        with self.assertRaises(ValueError): await tax.import_entries([row('one',amount='2')],1)
        with self.assertRaises(ValueError): await tax.import_entries([row('two'),row('one',amount='2')],1)
        self.assertEqual(len((await tax.data_for_year(2026))[1]),1)

    async def test_supplier_role_and_gross_amount_guard(self):
        await self.seed_payment();await self.seed_payment('300','transit')
        with self.assertRaises(ValueError):await tax.import_entries([row('net',amount='970',source='payment:100')],1)
        with self.assertRaises(ValueError):await tax.import_entries([row('wrong',source='payment:300')],1)
        await tax.import_entries([row('own',source='payment:100'),row('transit','transit',source='payment:300')],1)
        self.assertEqual((await tax.summary(2026,3))[1]['income_kop'],100000)
        with self.assertRaises(ValueError):await tax.import_entries([row('again',source='payment:100')],1)

    async def test_refund_cap_dates_and_reversal(self):
        await tax.import_entries([row('own')],1)
        with self.assertRaises(ValueError):await tax.import_entries([row('early','refund','10','08.08.2026',original='own')],1)
        await tax.import_entries([row('refund','refund','600','01.09.2026',original='own')],1)
        with self.assertRaises(ValueError):await tax.import_entries([row('over','refund','401','02.09.2026',original='own')],1)
        with self.assertRaises(ValueError):await tax.import_entries([row('reverse','reverse',original='own')],1)
        await tax.import_entries([row('two'),row('rev','reverse',original='two')],1)
        self.assertEqual((await tax.summary(2026,3))[1]['income_kop'],40000)

    async def test_next_year_payment_and_reversal_keep_liability_year(self):
        await tax.import_entries([row('own'),row('payment','tax_payment','60','10.04.2027')],1)
        self.assertEqual((await tax.summary(2026,4))[1]['balance_kop'],0)
        await tax.import_entries([row('reversed','reverse','60','10.04.2027',original='payment')],1)
        self.assertEqual((await tax.summary(2026,4))[1]['balance_kop'],6000)
        with self.assertRaises(ValueError):await tax.import_entries([row('future',when='01.01.2028')],1)

    async def test_contribution_tranches_caps_and_history(self):
        args=dict(source_year=2026,kind='fixed',tax_year=2026,due_kop=300000,
                  used_elsewhere_kop=0,document='Расчёт ФНС',actor_id=1)
        await tax.allocate_contribution(**args,allocated_kop=100000,effective_date=date(2026,8,7))
        await tax.allocate_contribution(**args,allocated_kop=200000,effective_date=date(2026,10,1))
        with self.assertRaises(ValueError):await tax.allocate_contribution(**args,allocated_kop=1,effective_date=date(2026,11,1))
        self.assertEqual((await tax.summary(2026,3))[1]['eligible_deduction_kop'],100000)
        self.assertEqual((await tax.summary(2026,4))[1]['eligible_deduction_kop'],300000)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("SELECT COUNT(*) FROM tax_audit WHERE action='allocate_contribution'"),2)

    async def test_reconciliation_invalidated_and_export_frozen(self):
        await tax.import_entries([row('own')],1)
        await tax.update_profile(actor_id=1,bank_details='Счёт 123, банк Тест',no_deductions=True,
                                 reconciled_through=date(2026,12,31))
        self.assertTrue((await tax.summary(2026,4))[1]['complete'])
        with patch('tax_export.datetime',Clock):bundle,complete,count=await export_bundle(2026,1)
        self.assertTrue(complete);self.assertEqual(count,0)
        with zipfile.ZipFile(io.BytesIO(bundle)) as z:pdf=z.read('KUDIR_2026.pdf')
        await tax.import_entries([row('late')],1)
        self.assertFalse((await tax.summary(2026,4))[1]['complete'])
        async with self.pool.acquire() as c:
            saved=await c.fetchrow('SELECT * FROM tax_exports')
        self.assertEqual(saved['pdf_sha256'],hashlib.sha256(pdf).hexdigest())
        self.assertEqual(len(json.loads(saved['entries_snapshot'])['entries']),1)
