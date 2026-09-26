"""Exercise the installed Telegram/VK entrypoints in an isolated interpreter."""
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class CommissionNavigationTests(unittest.TestCase):
    def test_runtime_toggle_editor_and_access_guards(self):
        env = {**os.environ, 'DATABASE_URL': 'postgresql://unused',
               'BOT_TOKEN': '123456:test', 'VK_BOT_TOKEN': 'test', 'ADMING_ID': '1',
               'ADMIN_VK_ID': '1', 'TINKOFF_TERMINAL_KEY': 'test', 'TINKOFF_SECRET_KEY': 'test'}
        program = textwrap.dedent('''
            import asyncio
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, patch
            import Bot_test_legal as tg
            import vk_bot_legal as vk

            async def check():
                legacy = tg.app.legacy
                tutor = {'name': 'Тест', 'commission_mode': 'manual'}
                state = SimpleNamespace(get_data=AsyncMock(return_value={'edit_tutor_id': 7}),
                                        set_state=AsyncMock())
                call = SimpleNamespace(from_user=SimpleNamespace(id=1), data='toggle_commission_mode',
                                       message=SimpleNamespace(edit_text=AsyncMock()))
                async def save(tid, **kwargs):
                    assert tid == 7
                    tutor.update(kwargs)
                with patch.object(legacy, 'get_all_tutors', AsyncMock(return_value={7: tutor})), \
                     patch.object(legacy, 'update_tutor', AsyncMock(side_effect=save)) as update, \
                     patch.object(legacy, 'safe_answer', AsyncMock()), \
                     patch.object(legacy, 'get_tutor_phone', AsyncMock(return_value=None)):
                    for mode, label in [('auto', 'авто'), ('manual', 'ручная')]:
                        await legacy.toggle_commission_mode(call, state)
                        assert tutor['commission_mode'] == mode
                        markup = call.message.edit_text.await_args.kwargs['reply_markup']
                        buttons = [b for row in markup.inline_keyboard for b in row]
                        button = next(b for b in buttons if b.callback_data == 'toggle_commission_mode')
                        assert label in button.text
                        assert any(b.callback_data == 'edit_name' for b in buttons)
                        state.set_state.assert_awaited_with(legacy.AdminStates.waiting_edit_choice)
                    assert update.await_count == 2
                    call.from_user.id = 999
                    await legacy.toggle_commission_mode(call, state)
                    assert update.await_count == 2
                    call.from_user.id = 1
                    state.get_data.return_value = {}
                    await legacy.toggle_commission_mode(call, state)
                    assert update.await_count == 2

                # VK intentionally permits statistics only; never bypass that guard.
                legacy_vk = vk.app.legacy
                with patch.object(legacy_vk, 'answer_event', AsyncMock()) as answer, \
                     patch.object(legacy_vk, 'update_tutor', AsyncMock()) as update:
                    await legacy_vk.toggle_commission_mode(SimpleNamespace(user_id=1))
                    update.assert_not_awaited()
                    assert 'только статистика' in answer.await_args.args[1]
            asyncio.run(check())
        ''')
        result = subprocess.run([sys.executable, '-c', program], env=env,
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
