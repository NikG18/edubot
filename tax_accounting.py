"""Auditable tax register; operational events are candidates, never bank dates."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

import database as db
import payments
from tax_rules import (RULES_YEAR, calculate_tax, money_kop, validate_allocation)

_SCHEMA_READY = False


async def ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-schema-v1',0))")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tax_profile (
                    id INT PRIMARY KEY CHECK(id=1), start_date DATE NOT NULL,
                    rate NUMERIC(5,2) NOT NULL CHECK(rate BETWEEN 0 AND 6),
                    has_workers BOOLEAN NOT NULL DEFAULT FALSE,
                    taxpayer_name TEXT NOT NULL, taxpayer_inn TEXT NOT NULL,
                    bank_details TEXT NOT NULL DEFAULT '',
                    contributions_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                    reconciled_through DATE
                );
                CREATE TABLE IF NOT EXISTS tax_sources (
                    source_key TEXT PRIMARY KEY, source_kind TEXT NOT NULL,
                    observed_at TIMESTAMPTZ, gross_kop BIGINT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('own','transit','fee','unknown')),
                    description TEXT NOT NULL, payload JSONB NOT NULL,
                    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS tax_entries (
                    id BIGSERIAL PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
                    tax_date DATE NOT NULL, document TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('income','transit','agent_fee','refund','tax_payment','reverse','exclude')),
                    gross_kop BIGINT NOT NULL CHECK(gross_kop>=0), income_kop BIGINT NOT NULL,
                    tax_paid_kop BIGINT NOT NULL DEFAULT 0, tax_year INT NOT NULL,
                    source_key TEXT UNIQUE REFERENCES tax_sources(source_key),
                    original_id BIGINT REFERENCES tax_entries(id),
                    description TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS tax_entries_dates ON tax_entries(tax_date,id);
                CREATE UNIQUE INDEX IF NOT EXISTS tax_one_reversal ON tax_entries(original_id) WHERE kind='reverse';
                CREATE TABLE IF NOT EXISTS tax_contribution_allocations (
                    source_year INT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('fixed','extra')),
                    tax_year INT NOT NULL, due_kop BIGINT NOT NULL CHECK(due_kop>=0),
                    allocated_kop BIGINT NOT NULL CHECK(allocated_kop>=0),
                    used_elsewhere_kop BIGINT NOT NULL DEFAULT 0 CHECK(used_elsewhere_kop>=0),
                    effective_date DATE NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(source_year,kind,tax_year,effective_date)
                );
                CREATE TABLE IF NOT EXISTS tax_audit (
                    id BIGSERIAL PRIMARY KEY, actor_id BIGINT NOT NULL,
                    action TEXT NOT NULL, details JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS tax_exports (
                    id BIGSERIAL PRIMARY KEY, tax_year INT NOT NULL,
                    pdf_bytes BYTEA NOT NULL, pdf_sha256 TEXT NOT NULL,
                    entries_snapshot JSONB NOT NULL, created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            ''')
            # Confirmed by the owner on 2026-09-25. Existing settings are never overwritten.
            await conn.execute('''INSERT INTO tax_profile(id,start_date,rate,has_workers,taxpayer_name,taxpayer_inn)
                                  VALUES(1,$1,6,FALSE,$2,$3) ON CONFLICT(id) DO NOTHING''',
                               date(2026,8,7), payments.OPERATOR_NAME, payments.OPERATOR_INN)
    _SCHEMA_READY = True


async def _audit(conn, actor_id, action, details):
    await conn.execute('INSERT INTO tax_audit(actor_id,action,details) VALUES($1,$2,$3::jsonb)',
                       int(actor_id), action, json.dumps(details, ensure_ascii=False, default=str))


def supplier_role(inn):
    if not str(inn or '').strip():
        return 'unknown'
    return 'own' if payments.is_operator_tutor(inn) else 'transit'


async def _source(conn, key, kind, observed, amount, role, description, payload):
    await conn.execute('''INSERT INTO tax_sources(source_key,source_kind,observed_at,gross_kop,role,description,payload)
                          VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(source_key) DO UPDATE
                          SET role=EXCLUDED.role,payload=tax_sources.payload || EXCLUDED.payload
                          WHERE tax_sources.role='unknown' AND EXCLUDED.role<>'unknown'
                            AND tax_sources.gross_kop=EXCLUDED.gross_kop
                            AND NOT EXISTS(SELECT 1 FROM tax_entries e WHERE e.source_key=tax_sources.source_key)''',
                       key,kind,observed,int(amount),role,description,
                       json.dumps(payload,ensure_ascii=False,default=str))


async def sync_sources():
    """Pull preserved payment/refund events, whole subscriptions and report amounts.

    No live supplier fallback, no revenue from held lessons or closing receipts.
    No outgoing network calls. Missing legacy evidence stays visible as unknown.
    """
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-sync',0))")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-post',0))")
            fiscal = await conn.fetchval("SELECT to_regclass('fiscal_receipts')")
            snapshots = {}
            if fiscal:
                snapshots = {r['payment_id']:dict(r) for r in await conn.fetch(
                    "SELECT payment_id,supplier_inn,amount FROM fiscal_receipts WHERE receipt_kind='prepayment'")}
            events = await conn.fetch("""SELECT e.id,e.booking_id,e.event_type,e.created_at,e.details
                                        FROM booking_events e WHERE event_type IN ('paid','refunded')
                                        ORDER BY e.id""")
            for r in events:
                details = r['details'] if isinstance(r['details'],dict) else json.loads(r['details'])
                pid = str(details.get('payment_id') or '')
                if not pid:
                    continue
                snap = snapshots.get(pid,{})
                refund = r['event_type']=='refunded'
                key = f'refund:{pid}:full' if refund else f'payment:{pid}'
                await _source(conn,key,'refund' if refund else 'payment',r['created_at'],
                              details.get('amount') or snap.get('amount') or 0,
                              supplier_role(snap.get('supplier_inn')),
                              f"{'Возврат' if refund else 'Оплата'} занятия #{r['booking_id']}; платёж {pid}",
                              {'payment_id':pid,'booking_id':r['booking_id'],'event_id':r['id'],
                               'original_source':f'payment:{pid}' if refund else None})
            # Older paid rows may lack a journal entry. They still need reconciliation,
            # but updated_at is explicitly not used as their bank receipt date.
            for r in await conn.fetch("""SELECT id,tinkoff_payment_id,amount FROM bookings
                                         WHERE booking_type<>'trial' AND tinkoff_payment_id IS NOT NULL
                                           AND (balance_credited=TRUE OR status IN ('paid','completed')
                                                OR refund_status='refunded')"""):
                pid=str(r['tinkoff_payment_id']);snap=snapshots.get(pid,{})
                await _source(conn,f'payment:{pid}','payment',None,r['amount'] or 0,
                              supplier_role(snap.get('supplier_inn')),f"Старая оплата занятия #{r['id']}; {pid}",
                              {'payment_id':pid,'booking_id':r['id'],'legacy':True})
            has_subs = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                                  WHERE table_schema=current_schema() AND table_name='subscriptions'
                                  AND column_name='payment_id')""")
            if has_subs:
                for r in await conn.fetch('SELECT * FROM subscriptions WHERE payment_id IS NOT NULL'):
                    pid=str(r['payment_id'])
                    await _source(conn,f'payment:{pid}','payment',r['activated_at'],
                                  money_kop(str(r['total_price'] or 0)),supplier_role(r['supplier_inn']),
                                  f"Оплата абонемента #{r['id']}; платёж {pid}",
                                  {'payment_id':pid,'subscription_id':r['id']})
            if await conn.fetchval("SELECT to_regclass('agent_reports')"):
                for r in await conn.fetch('SELECT id,report_key,tutor_id,commission_amount_kop,created_at FROM agent_reports'):
                    if int(r['commission_amount_kop']) <= 0:
                        continue
                    await _source(conn,f"report:{r['id']}",'agent_fee',r['created_at'],r['commission_amount_kop'],
                                  'fee',f"Вознаграждение по отчёту {r['report_key']}; требуется факт удержания",
                                  {'report_id':r['id'],'report_key':r['report_key']})


async def get_profile():
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        return dict(await conn.fetchrow('SELECT * FROM tax_profile WHERE id=1'))


async def unresolved(limit=20, offset=0):
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        count = await conn.fetchval('''SELECT COUNT(*) FROM tax_sources s WHERE NOT EXISTS
                                   (SELECT 1 FROM tax_entries e WHERE e.source_key=s.source_key)''')
        rows = await conn.fetch('''SELECT s.* FROM tax_sources s WHERE NOT EXISTS
                                  (SELECT 1 FROM tax_entries e WHERE e.source_key=s.source_key)
                                  ORDER BY s.observed_at NULLS LAST,s.source_key LIMIT $1 OFFSET $2''',
                                int(limit),max(0,int(offset)))
    return int(count),[dict(r) for r in rows]


def fingerprint(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


async def import_entries(rows, actor_id):
    """Atomic, idempotent verified settlement import. No partial CSV commit."""
    await sync_sources()
    inserted=duplicates=0
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-post',0))")
            profile=dict(await conn.fetchrow('SELECT * FROM tax_profile WHERE id=1 FOR UPDATE'))
            today=datetime.now(db.MSK).date()
            for row in rows:
                digest=fingerprint(row)
                old=await conn.fetchrow('SELECT * FROM tax_entries WHERE operation_id=$1',row['operation_id'])
                if old:
                    if old['fingerprint']!=digest:
                        raise ValueError(f"ID {row['operation_id']} уже существует с другими данными. Используйте исправление.")
                    duplicates+=1
                    continue
                when=row['tax_date'];kind=row['kind'];amount=int(row['amount_kop'])
                if when>today:
                    raise ValueError('Будущие даты денежных операций не допускаются.')
                tax_year=int(row.get('tax_year') or when.year)
                if tax_year!=RULES_YEAR or (when.year!=RULES_YEAR and kind not in {'tax_payment','reverse','exclude'}):
                    raise ValueError('Учёт этого года пока не поддерживается.')
                if kind not in {'tax_payment','reverse','exclude'} and tax_year!=when.year:
                    raise ValueError('Год дохода определяется датой поступления.')
                if kind=='tax_payment' and when.year<tax_year:
                    raise ValueError('Погашение УСН не может предшествовать налоговому году.')
                if when<profile['start_date'] and kind not in {'exclude','tax_payment'}:
                    raise ValueError('Операция раньше начала УСН: исключите её с документированным основанием.')
                source=None
                source_key=row.get('source_key') or None
                if source_key:
                    source=await conn.fetchrow('SELECT * FROM tax_sources WHERE source_key=$1',source_key)
                    if not source:
                        raise ValueError(f'Источник {source_key} не найден в боте.')
                    if await conn.fetchval('SELECT id FROM tax_entries WHERE source_key=$1',source_key):
                        raise ValueError(f'Источник {source_key} уже учтён другой операцией.')
                    if kind!='exclude':
                        if amount!=int(source['gross_kop']):
                            raise ValueError('Сумма не совпадает с исходной операцией (нужна полная сумма до комиссии банка).')
                        expected={'payment':{'own':'income','transit':'transit'},
                                  'refund':{'own':'refund','transit':'transit'},
                                  'agent_fee':{'fee':'agent_fee'}}
                        required=expected.get(source['source_kind'],{}).get(source['role'])
                        if not required:
                            raise ValueError('У источника нет достоверных реквизитов поставщика. Нужна ручная сверка.')
                        if kind!=required:
                            raise ValueError(f'Для этого источника допустим вид {required}.')
                if kind=='agent_fee' and not source_key:
                    raise ValueError('Вознаграждение из бота связывайте с report:ID; внешнее вознаграждение — income с документом.')
                if kind=='exclude' and (not source_key or not row['description'] or amount!=0):
                    raise ValueError('Для исключения нужны источник, нулевая сумма и причина.')
                original=None
                income=amount if kind in {'income','agent_fee'} else 0
                tax_paid=amount if kind=='tax_payment' else 0
                if kind in {'refund','reverse'}:
                    original=await conn.fetchrow('SELECT * FROM tax_entries WHERE operation_id=$1 FOR UPDATE',row.get('original_id'))
                    if not original or original['kind'] not in {'income','agent_fee','transit','tax_payment','refund'}:
                        raise ValueError('Нужен ID исходного поступления/налогового платежа.')
                    if tax_year!=original['tax_year']:
                        raise ValueError('Год исправления должен совпадать с исходной записью.')
                    if when<original['tax_date']:
                        raise ValueError('Возврат не может быть раньше исходного поступления.')
                    if kind=='reverse':
                        if when!=original['tax_date'] or amount!=int(original['gross_kop']):
                            raise ValueError('Исправление reverse сторнирует исходную запись полностью её датой.')
                        if await conn.fetchval('SELECT COUNT(*) FROM tax_entries WHERE original_id=$1',original['id']):
                            raise ValueError('У исходной записи уже есть возврат/исправление. Нужна отдельная сверка.')
                        income=-int(original['income_kop']);tax_paid=-int(original['tax_paid_kop'])
                    else:
                        if original['kind'] not in {'income','agent_fee'}:
                            raise ValueError('Уменьшать доход можно только по ранее учтённому доходу.')
                        returned=await conn.fetchval("""SELECT -COALESCE(SUM(income_kop),0) FROM tax_entries WHERE original_id=$1
                                OR (kind='reverse' AND original_id IN
                                  (SELECT id FROM tax_entries WHERE original_id=$1 AND kind='refund'))""",original['id'])
                        if amount+int(returned)>int(original['gross_kop']):
                            raise ValueError('Возврат превышает ранее учтённую сумму.')
                        if source:
                            payload=source['payload'] if isinstance(source['payload'],dict) else json.loads(source['payload'])
                            if original['source_key']!=payload.get('original_source'):
                                raise ValueError('Возврат относится к другому платежу.')
                        income=-amount
                elif row.get('original_id'):
                    raise ValueError('original_id нужен только для refund/reverse.')
                if kind=='tax_payment' and source_key:
                    raise ValueError('Платёж УСН вводится по данным ЕНС отдельно от клиентских оплат.')
                await conn.execute('''INSERT INTO tax_entries(operation_id,tax_date,document,kind,gross_kop,
                           income_kop,tax_paid_kop,tax_year,source_key,original_id,description,fingerprint,created_by)
                           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)''',
                           row['operation_id'],when,row['document'],kind,amount,income,tax_paid,tax_year,
                           source_key,original['id'] if original else None,row['description'],digest,int(actor_id))
                inserted+=1
                await _audit(conn,actor_id,'post_entry',row)
                # New/corrected historical data invalidates the previous completeness assertion.
                await conn.execute('UPDATE tax_profile SET reconciled_through=NULL WHERE id=1')
    return {'inserted':inserted,'duplicates':duplicates}


async def allocate_contribution(*, source_year, kind, tax_year, due_kop, allocated_kop,
                                used_elsewhere_kop, effective_date, document, actor_id):
    await ensure_schema()
    if used_elsewhere_kop < 0:
        raise ValueError('Ранее использованная сумма не может быть отрицательной.')
    if not document.strip() or len(document)>500:
        raise ValueError('Укажите документ-основание взноса (до 500 символов).')
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-post',0))")
            old=await conn.fetchrow('''SELECT * FROM tax_contribution_allocations
                                      WHERE source_year=$1 AND kind=$2 AND tax_year=$3 AND effective_date=$4''',
                                      source_year,kind,tax_year,effective_date)
            peers=[dict(r) for r in await conn.fetch(
                'SELECT * FROM tax_contribution_allocations WHERE source_year=$1 AND kind=$2',source_year,kind)]
            others=await conn.fetchval('''SELECT COALESCE(SUM(allocated_kop),0) FROM tax_contribution_allocations
                                        WHERE source_year=$1 AND kind=$2 AND NOT(tax_year=$3 AND effective_date=$4)''',
                                        source_year,kind,tax_year,effective_date)
            validate_allocation(kind=kind,source_year=source_year,tax_year=tax_year,due_kop=due_kop,
                                allocated_kop=allocated_kop,other_allocated_kop=int(others)+used_elsewhere_kop,
                                effective_date=effective_date)
            await conn.execute('''INSERT INTO tax_contribution_allocations
                  (source_year,kind,tax_year,due_kop,allocated_kop,used_elsewhere_kop,effective_date,document)
                  VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(source_year,kind,tax_year,effective_date) DO UPDATE
                  SET due_kop=EXCLUDED.due_kop,allocated_kop=EXCLUDED.allocated_kop,
                      used_elsewhere_kop=EXCLUDED.used_elsewhere_kop,effective_date=EXCLUDED.effective_date,
                      document=EXCLUDED.document''',source_year,kind,tax_year,due_kop,allocated_kop,
                      used_elsewhere_kop,effective_date,document)
            await conn.execute('''UPDATE tax_contribution_allocations SET due_kop=$3,used_elsewhere_kop=$4
                                  WHERE source_year=$1 AND kind=$2''',source_year,kind,due_kop,used_elsewhere_kop)
            await conn.execute('UPDATE tax_profile SET contributions_reviewed=TRUE,reconciled_through=NULL WHERE id=1')
            await _audit(conn,actor_id,'allocate_contribution',{'old':dict(old) if old else None,'previous_tranches':peers,
                           'source_year':source_year,'kind':kind,'tax_year':tax_year,'due_kop':due_kop,
                           'allocated_kop':allocated_kop,'used_elsewhere_kop':used_elsewhere_kop,
                           'effective_date':effective_date,'document':document})


async def read_snapshot(year):
    """One MVCC snapshot for figures, reconciliation and export contents."""
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            profile=dict(await conn.fetchrow('SELECT * FROM tax_profile WHERE id=1'))
            entries=[dict(r) for r in await conn.fetch('SELECT * FROM tax_entries WHERE tax_year=$1 ORDER BY tax_date,id',year)]
            allocations=[dict(r) for r in await conn.fetch('SELECT * FROM tax_contribution_allocations WHERE tax_year=$1',year)]
            pending=[dict(r) for r in await conn.fetch('''SELECT s.* FROM tax_sources s WHERE NOT EXISTS
                        (SELECT 1 FROM tax_entries e WHERE e.source_key=s.source_key)
                        ORDER BY s.source_key''')]
    return profile,entries,allocations,pending


async def data_for_year(year):
    profile,entries,allocations,_=await read_snapshot(year)
    return profile,entries,allocations


def is_reconciled(profile, cutoff, pending):
    return bool(profile['contributions_reviewed'] and profile['bank_details']
                and profile['reconciled_through'] and profile['reconciled_through']>=cutoff and not pending)


async def summary(year,quarter):
    await sync_sources()
    profile,entries,allocations,pending=await read_snapshot(year)
    result=calculate_tax(entries,allocations,year=year,quarter=quarter,rate=Decimal(profile['rate']),
                         start=profile['start_date'],as_of=datetime.now(db.MSK).date(),has_workers=profile['has_workers'])
    result['unresolved']=len(pending)
    result['complete']=is_reconciled(profile,result['cutoff'],pending)
    return profile,result


async def update_profile(*, actor_id, bank_details=None, reconciled_through=None,
                         no_deductions=False):
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('usn-tax-post',0))")
            old=dict(await conn.fetchrow('SELECT * FROM tax_profile WHERE id=1 FOR UPDATE'))
            if bank_details is not None:
                if not bank_details.strip() or len(bank_details)>500:
                    raise ValueError('Укажите реквизиты счёта и банка (до 500 символов).')
                await conn.execute('UPDATE tax_profile SET bank_details=$1 WHERE id=1',bank_details.strip())
            if no_deductions:
                if await conn.fetchval('SELECT COUNT(*) FROM tax_contribution_allocations'):
                    raise ValueError('Уже заданы взносы. Чтобы изменить уменьшение, измените их распределение.')
                await conn.execute('UPDATE tax_profile SET contributions_reviewed=TRUE WHERE id=1')
            if reconciled_through is not None:
                if not old['start_date']<=reconciled_through<=datetime.now(db.MSK).date():
                    raise ValueError('Дата сверки должна быть от начала УСН до сегодня.')
                pending=await conn.fetchval('''SELECT COUNT(*) FROM tax_sources s WHERE NOT EXISTS
                               (SELECT 1 FROM tax_entries e WHERE e.source_key=s.source_key)''')
                if pending:
                    raise ValueError('Сначала разберите все операции на сверке.')
                await conn.execute('UPDATE tax_profile SET reconciled_through=$1 WHERE id=1',reconciled_through)
            await _audit(conn,actor_id,'update_profile',{'before':old,'bank_details':bank_details,
                         'reconciled_through':reconciled_through,'no_deductions':no_deductions})
