"""Drive the Telegram entrypoint with synthetic updates and no network or live data."""
import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update

import tax_admin as tax

TEST_ENV = {
    'DATABASE_URL': 'postgresql://unused',
    'ADMING_ID': '1',
    'BOT_TOKEN': '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi',
    'VK_BOT_TOKEN': 'vk1.a.test_token_for_import_only',
    'TINKOFF_TERMINAL_KEY': 'test-terminal',
    'TINKOFF_SECRET_KEY': 'test-secret',
    'TINKOFF_WEBHOOK_URL': '',
}


class OfflineSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError('Unexpected file download')
        yield

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in {'AnswerCallbackQuery', 'DeleteMessage'}:
            return True
        if name in {'SendMessage', 'EditMessageText', 'SendDocument'}:
            return Message.model_validate({
                'message_id': len(self.calls), 'date': 1770000000,
                'chat': {'id': int(method.chat_id), 'type': 'private'},
                'text': getattr(method, 'text', ''),
            }, context={'bot': bot})
        raise AssertionError(f'Unexpected Telegram method {name}')


class TelegramScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, TEST_ENV):
            import Bot_test_legal as entry
        self.app = entry.app
        self.legacy = entry.app.legacy
        self.session = OfflineSession()
        self.bot = Bot(TEST_ENV['BOT_TOKEN'], session=self.session)
        self.counter = 0
        self.admin = self.legacy.ADMING_ID
        self.state = self.legacy.dp.fsm.get_context(self.bot, chat_id=self.admin, user_id=self.admin)
        await self.state.clear()
        self.addAsyncCleanup(self.state.clear)

    async def feed(self, user=None, *, text=None, callback=None, document=None):
        user = user or self.admin
        self.counter += 1
        author = {'id': user, 'is_bot': False, 'first_name': 'Test'}
        message = {'message_id': self.counter, 'date': 1770000000,
                   'chat': {'id': user, 'type': 'private'}, 'from_user': author}
        if callback is not None:
            message['text'] = 'Экран'
            payload = {'callback_query': {'id': str(self.counter), 'from_user': author,
                       'chat_instance': 'test', 'message': message, 'data': callback}}
        else:
            if document:
                message['document'] = document
            else:
                message['text'] = text
            payload = {'message': message}
        await self.legacy.dp.feed_update(self.bot, Update.model_validate(dict(update_id=self.counter, **payload)))

    def screen(self):
        return next(m for m in reversed(self.session.calls)
                    if type(m).__name__ in {'SendMessage', 'EditMessageText'})

    def callbacks(self):
        return {b.callback_data for row in self.screen().reply_markup.inline_keyboard for b in row}

    async def test_real_csv_reaches_accounting_through_text_guard(self):
        csv_data = tax.import_template() + b'a;10.09.2026;Receipt;income;2500;;;Test;2026\n'

        async def download(_, destination):
            destination.write(csv_data)

        with patch.object(self.bot, 'download', side_effect=download), \
             patch.object(tax.accounting, 'import_entries', AsyncMock(return_value={'inserted': 1, 'duplicates': 0})) as store:
            await self.feed(callback='tax_import')
            self.assertIn('tax_template', self.callbacks())
            await self.feed(callback='tax_template')
            self.assertEqual(type(self.session.calls[-1]).__name__, 'SendDocument')
            await self.feed(document={'file_id': 'csv', 'file_unique_id': 'u', 'file_name': 'income.csv', 'file_size': len(csv_data)})
            self.assertEqual(store.await_args.args[0][0]['amount_kop'], 250_000)
            self.assertIn('Добавлено: 1', self.screen().text)
            self.assertIsNone(await self.state.get_state())

    async def test_guided_source_preview_is_required_and_stale_button_cannot_post(self):
        ref = 'a' * 32
        source = dict(source_kind='payment', role='own', source_key='payment:100',
                      description='Оплата занятия №7', gross_kop=250_000, posted_operation=None)
        with patch.object(tax.accounting, 'review_source', AsyncMock(return_value=source)), \
             patch.object(tax.accounting, 'import_entries', AsyncMock(return_value={'inserted': 1, 'duplicates': 0})) as store:
            await self.feed(callback=f'tax_src_{ref}')
            self.assertIn(f'tax_accept_{ref}', self.callbacks())
            await self.feed(callback=f'tax_accept_{ref}')
            await self.feed(text='10.09.2026;Выписка №1')
            store.assert_not_awaited()
            token = next(c for c in self.callbacks() if c.startswith('tax_save_'))
            self.assertIn('payment:100', self.screen().text)
            await self.feed(callback=token)
            rows, actor = store.await_args.args
            self.assertEqual(actor, self.admin)
            self.assertEqual((rows[0]['source_key'], rows[0]['tax_date'], rows[0]['amount_kop']),
                             ('payment:100', date(2026, 9, 10), 250_000))
            await self.feed(callback=token)
            store.assert_awaited_once()
            self.assertIn('устарело', self.screen().text)

    async def test_accounting_menu_actions_route_and_return(self):
        profile = dict(rate=Decimal(6), start_date=date(2026, 8, 7), contributions_reviewed=False,
                       bank_details='Test bank', reconciled_through=None)
        with patch.multiple(tax.accounting, sync_sources=AsyncMock(),
                            get_profile=AsyncMock(return_value=profile),
                            unresolved=AsyncMock(return_value=(0, [])),
                            entries_page=AsyncMock(return_value=(0, 0, [])),
                            data_for_year=AsyncMock(return_value=(profile, [], []))), \
             patch.object(tax, 'export_bundle', AsyncMock(return_value=(b'zip', False, 0))):
            await self.feed(callback='admin_tax')
            actions = self.callbacks()
            self.assertIn('tax_entries_0', actions)
            for command in sorted(actions - {'admin_panel_open'}):
                self.session.calls.clear()
                await self.feed(callback=command)
                self.assertTrue(any(type(m).__name__ in {'EditMessageText','SendDocument'}
                                    for m in self.session.calls), command)
                if command == 'tax_bank':
                    self.assertIn('Test bank', self.screen().text)
            await self.feed(callback='admin_tax')
            self.assertIsNone(await self.state.get_state())

    async def test_multiple_student_records_each_open_only_their_own_actions(self):
        records = {
            77: dict(tutor_id=7, status='pending', subject='Химия', date='12.11.2026',
                     time_slot='12:00-13:00', username='Test', user_id=20),
            78: dict(tutor_id=7, status='confirmed', subject='Химия', date='12.11.2026',
                     time_slot='13:00-14:00', username='Test', user_id=20),
        }
        with patch.multiple(self.app._db, get_bookings_for_account=AsyncMock(return_value=records),
                            get_booking=AsyncMock(side_effect=records.get),
                            account_owns_booking=AsyncMock(side_effect=lambda _, uid, row: uid == row['user_id'])), \
             patch.multiple(self.legacy, get_all_tutors=AsyncMock(return_value={7: {'name': 'Преподаватель'}}),
                            now_msk_naive=lambda: datetime(2026, 11, 7, 15)):
            await self.feed(20, text='📋 Мои записи')
            self.assertEqual(self.callbacks() & {'record_student_77_0', 'record_student_78_0'},
                             {'record_student_77_0', 'record_student_78_0'})
            self.assertFalse(any('cancel' in c for c in self.callbacks()))
            await self.feed(20, callback='record_student_78_0')
            self.assertIn('Занятие №78', self.screen().text)
            self.assertIn('13:00-14:00', self.screen().text)
            self.assertEqual(self.callbacks(), {'cancel_student_78', 'reschedule_student_78', 'record_student_page_0'})
            old_screen = self.screen()
            await self.feed(21, callback='record_student_78_0')
            self.assertIs(old_screen, self.screen())
            self.assertIn('Доступ запрещён', self.session.calls[-1].text)

    async def test_tutor_card_links_pending_actions_and_denies_other_tutor(self):
        booking = dict(tutor_id=7, status='pending', subject='Химия', date='12.11.2026',
                       time_slot='12:00-13:00', username='Ученица', user_id=20, user_platform='vk')
        with patch.object(self.app._db, 'get_booking', AsyncMock(return_value=booking)), \
             patch.multiple(self.legacy, get_all_bookings=AsyncMock(return_value={80: booking}),
                            get_tutor_by_telegram_id=AsyncMock(side_effect=lambda uid: 7 if uid == 30 else 8)):
            await self.feed(30, callback='tutor_students_7')
            self.assertIn('record_tutor_80_0', self.callbacks())
            await self.feed(30, callback='record_tutor_80_0')
            self.assertIn('Ученица', self.screen().text)
            self.assertIn('VK', self.screen().text)
            self.assertEqual(self.callbacks(), {'tutor_confirm_80', 'tutor_reject_80', 'record_tutor_page_7_0'})
            previous = self.screen()
            await self.feed(31, callback='record_tutor_80_0')
            self.assertIs(previous, self.screen())
