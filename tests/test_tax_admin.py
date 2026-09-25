import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Dispatcher
from aiogram.types import InlineKeyboardMarkup
import tax_admin as ui


class TaxAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.legacy=SimpleNamespace(dp=Dispatcher(),ADMING_ID=1,safe_answer=AsyncMock(),
            admin_actions_keyboard=lambda:InlineKeyboardMarkup(inline_keyboard=[]))
        ui.install_telegram_tax_accounting(SimpleNamespace(legacy=self.legacy))
        self.callbacks={h.callback.__name__:h.callback for h in self.legacy.dp.callback_query.handlers}
        self.save=self.legacy.dp.message.handlers[0].callback
        self.call=SimpleNamespace(from_user=SimpleNamespace(id=1),data='admin_tax',
                 message=SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()))
        self.state=SimpleNamespace(clear=AsyncMock(),set_state=AsyncMock(),get_state=AsyncMock())

    async def test_every_callback_and_input_reject_nonadmin(self):
        self.call.from_user.id=999
        with patch.object(ui.accounting,'sync_sources',AsyncMock()) as sync:
            for name,callback in self.callbacks.items():
                if name in {'menu','prompt','no_deductions'}:await callback(self.call,self.state)
                else:await callback(self.call)
            message=SimpleNamespace(from_user=SimpleNamespace(id=999),answer=AsyncMock())
            await self.save(message,self.state)
            sync.assert_not_awaited()
            self.state.get_state.assert_not_awaited()
            self.call.message.edit_text.assert_not_awaited()

    async def test_back_clears_state_and_installer_is_idempotent(self):
        profile=dict(rate=Decimal(6),start_date=date(2026,8,7),contributions_reviewed=False)
        with patch.object(ui.accounting,'sync_sources',AsyncMock()), \
             patch.object(ui.accounting,'get_profile',AsyncMock(return_value=profile)), \
             patch.object(ui.accounting,'unresolved',AsyncMock(return_value=(0,[]))):
            await self.callbacks['menu'](self.call,self.state)
        self.state.clear.assert_awaited_once()
        ui.install_telegram_tax_accounting(SimpleNamespace(legacy=self.legacy))
        buttons=[b.callback_data for row in self.legacy.admin_actions_keyboard().inline_keyboard for b in row]
        self.assertEqual(buttons.count('admin_tax'),1)
        self.assertIn('tax_import',str(self.call.message.edit_text.await_args))

    async def test_document_without_filename_is_validation_error(self):
        self.state.get_state.return_value=ui.TaxStates.import_csv.state
        message=SimpleNamespace(from_user=SimpleNamespace(id=1),text=None,
                 document=SimpleNamespace(file_name=None,file_size=1),answer=AsyncMock())
        await self.save(message,self.state)
        self.assertIn('.csv',message.answer.await_args.args[0])
        self.state.clear.assert_not_awaited()
