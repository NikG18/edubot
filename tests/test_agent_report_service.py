import asyncio
import hashlib
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import agent_report_service as service


class Context:
    def __init__(self, value=None):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, *args):
        return False


def report():
    data = b'%PDF-test'
    return dict(id=1, tutor_id=7, report_key='AR-202601-P2-T7', period_start=date(2026, 1, 16),
                period_end=date(2026, 1, 31), lessons_count=1, gross_amount_kop=100000,
                commission_amount_kop=20000, tutor_amount_kop=80000,
                pdf_bytes=data, pdf_sha256=hashlib.sha256(data).hexdigest(),
                archive_sent_at=None, tutor_sent_at=None)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_report_never_recalculates_first_payment(self):
        conn = SimpleNamespace(execute=AsyncMock(), fetchrow=AsyncMock(return_value=report()),
                               transaction=lambda: Context())
        pool = SimpleNamespace(acquire=lambda: Context(conn))
        row = {'id': 9, 'date': '20.01.2026', 'time_slot': '18:00-19:00', 'user_id': 20,
               'username': 'Тест', 'subject': 'Химия', 'amount': 100000,
               'commission_percent': 25, 'status': 'completed'}
        with patch.object(service, 'ensure_schema', AsyncMock()), \
             patch.object(service.db._legacy, 'pool', pool), \
             patch.object(service.db, 'get_all_tutors', AsyncMock(return_value={7: {'commission_mode': 'auto'}})), \
             patch.object(service.payments, 'is_operator_tutor', return_value=False), \
             patch.object(service, '_existing', AsyncMock(return_value=None)) as existing, \
             patch.object(service, '_period_rows', AsyncMock(return_value=[row])), \
             patch.object(service, 'calculate_auto_commission', AsyncMock(return_value=(20, 21))) as rate, \
             patch.object(service, 'build_report_pdf', return_value=b'%PDF-test') as pdf:
            _, items, created = await service.create_snapshot(7, 2026, 1, 2)
        self.assertTrue(created)
        self.assertEqual(items[0]['commission_kop'], 20000)
        self.assertEqual(pdf.call_args.args[0]['commission_adjustment_kop'], 0)
        self.assertEqual(pdf.call_args.args[0]['tutor_kop'], 80000)
        rate.assert_awaited_once_with(7, 2026, 1, 2, conn=conn)
        self.assertEqual(existing.await_count, 1)

    async def test_existing_snapshot_is_not_rebuilt(self):
        saved = report()
        conn = SimpleNamespace(execute=AsyncMock(), transaction=lambda: Context())
        with patch.object(service, 'ensure_schema', AsyncMock()), \
             patch.object(service.db._legacy, 'pool', SimpleNamespace(acquire=lambda: Context(conn))), \
             patch.object(service.db, 'get_all_tutors', AsyncMock(return_value={7: {"name": "Тест"}})), \
             patch.object(service.payments, 'is_operator_tutor', return_value=False), \
             patch.object(service, '_existing', AsyncMock(return_value=saved)), \
             patch.object(service, '_items', AsyncMock(return_value=[])), \
             patch.object(service, '_period_rows', AsyncMock()) as rows:
            result = await service.create_snapshot(7, 2026, 1, 2)
        self.assertEqual(result, (saved, [], False))
        rows.assert_not_awaited()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = report()
        self.lock = asyncio.Lock()
        async def execute(query, *args):
            if 'pg_advisory_unlock' in query:
                self.lock.release()
            elif 'pg_advisory_lock' in query:
                await self.lock.acquire()
            elif 'archive_sent_at=NOW()' in query:
                self.saved['archive_sent_at'] = True
            elif 'tutor_sent_at=NOW()' in query:
                self.saved['tutor_sent_at'] = True
        self.conn = SimpleNamespace(execute=execute, fetchrow=AsyncMock(side_effect=lambda *a: dict(self.saved)))
        self.patchers = [
            patch.object(service, 'ensure_schema', AsyncMock()),
            patch.object(service.db._legacy, 'pool', SimpleNamespace(acquire=lambda: Context(self.conn))),
            patch.object(service.db, 'get_all_tutors', AsyncMock(return_value={7: {'telegram_id': 77, 'name': 'A < B'}})),
            patch.object(service, 'reports_channel_id', return_value=-100),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_retry_after_tutor_failure_does_not_repeat_archive(self):
        bot = SimpleNamespace(send_document=AsyncMock(side_effect=[None, RuntimeError('offline'), None]))
        stale = dict(self.saved)
        with self.assertRaises(RuntimeError):
            await service.send_report(bot, stale)
        result = await service.send_report(bot, stale)
        self.assertEqual([c.args[0] for c in bot.send_document.await_args_list], [-100, 77, 77])
        self.assertEqual(result, {'archive_sent': True, 'tutor_sent': True})
        self.assertFalse(self.lock.locked())
        self.assertIsNone(bot.send_document.await_args.kwargs['parse_mode'])

    async def test_concurrent_stale_clicks_send_once_per_recipient(self):
        bot = SimpleNamespace(send_document=AsyncMock())
        stale = dict(self.saved)
        await asyncio.gather(service.send_report(bot, stale), service.send_report(bot, stale))
        self.assertEqual(bot.send_document.await_count, 2)

    async def test_corrupt_pdf_is_not_sent(self):
        self.saved['pdf_bytes'] = b'bad'
        bot = SimpleNamespace(send_document=AsyncMock())
        with self.assertRaisesRegex(ValueError, 'checksum'):
            await service.send_report(bot, self.saved)
        bot.send_document.assert_not_awaited()
        self.assertFalse(self.lock.locked())
