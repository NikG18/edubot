"""Exercise timezone navigation using real Telegram/VK keyboard serializers."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from vkbottle import Callback, Keyboard, KeyboardButtonColor, Text

from test_booking_session_reset import load_handler
from timezone_support import SUPPORTED_TIMEZONES, timezone_label


class TimezoneMenuTests(unittest.IsolatedAsyncioTestCase):
    def namespace(self, platform):
        return dict(
            SUPPORTED_TIMEZONES=SUPPORTED_TIMEZONES, timezone_label=timezone_label,
            get_student_timezone_offset=AsyncMock(return_value=180),
            set_student_timezone_offset=AsyncMock(), safe_answer=AsyncMock(),
            answer_event=AsyncMock(), edit_event_message=AsyncMock(),
            InlineKeyboardButton=InlineKeyboardButton, InlineKeyboardMarkup=InlineKeyboardMarkup,
            KeyboardButton=KeyboardButton, ReplyKeyboardMarkup=ReplyKeyboardMarkup,
            Keyboard=Keyboard, Callback=Callback, Text=Text, KeyboardButtonColor=KeyboardButtonColor,
            get_tutor_by_telegram_id=AsyncMock(return_value=None),
            get_tutor_by_vk_id=AsyncMock(return_value=None), ADMING_ID=999, ADMIN_VK_ID=999,
            state_dispenser=SimpleNamespace(delete=AsyncMock()),
        )

    async def test_student_main_menus_have_timezone_button(self):
        for platform, file in (("telegram", "Bot_test_legacy.py"), ("vk", "vk_bot_legacy.py")):
            ns = self.namespace(platform)
            keyboard = await load_handler(file, ns, "get_main_menu")(123)
            if platform == "telegram":
                labels = [b.text for row in keyboard.keyboard for b in row]
            else:
                rows = json.loads(keyboard)["buttons"]
                # The legal wrapper adds one more row for Documents.
                self.assertLessEqual(len(rows) + 1, 10)
                labels = [b["action"]["label"] for row in rows for b in row]
            self.assertIn("🌍 Часовой пояс", labels)

    async def test_menu_clears_abandoned_session_and_has_valid_options_and_back(self):
        for platform, file in (("telegram", "Bot_test_legacy.py"), ("vk", "vk_bot_legacy.py")):
            ns = self.namespace(platform)
            message = SimpleNamespace(from_user=SimpleNamespace(id=123), from_id=123, answer=AsyncMock())
            handler = load_handler(file, ns, "timezone_menu")
            if platform == "telegram":
                state = SimpleNamespace(clear=AsyncMock())
                await handler(message, state)
                state.clear.assert_awaited_once()
                rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
                commands = [b.callback_data for row in rows for b in row]
                self.assertIn("✅ МСК (UTC+3)", [b.text for row in rows for b in row])
                offsets = {int(c.split("_", 1)[1]) for c in commands if c.startswith("tzset_")}
            else:
                await handler(message)
                ns["state_dispenser"].delete.assert_awaited_once_with(123)
                rows = json.loads(message.answer.await_args.kwargs["keyboard"])["buttons"]
                self.assertLessEqual(len(rows), 6)
                self.assertTrue(all(len(row) <= 3 for row in rows))
                payloads = [b["action"]["payload"] for row in rows for b in row]
                commands = [(json.loads(p) if isinstance(p, str) else p)["cmd"] for p in payloads]
                offsets = {int(c.rsplit("_", 1)[1]) for c in commands if c.startswith("timezone_set_")}
            self.assertEqual(offsets, {offset for _, offset in SUPPORTED_TIMEZONES})
            self.assertIn("back_to_menu", commands)

    async def test_selection_saves_for_actor_and_returns_navigation(self):
        for platform, file, command in (("telegram", "Bot_test_legacy.py", "tzset_300"),
                                        ("vk", "vk_bot_legacy.py", "timezone_set_300")):
            ns = self.namespace(platform)
            event = SimpleNamespace(data=command, from_user=SimpleNamespace(id=123),
                                    user_id=123, payload={"cmd": command},
                                    message=SimpleNamespace(edit_text=AsyncMock()))
            await load_handler(file, ns, "timezone_set")(event)
            ns["set_student_timezone_offset"].assert_awaited_once_with(platform, 123, 300)
            edited = event.message.edit_text if platform == "telegram" else ns["edit_event_message"]
            self.assertIn("back_to_menu", str(edited.await_args))

    async def test_unsupported_callback_does_not_write_preference(self):
        for platform, file, command in (("telegram", "Bot_test_legacy.py", "tzset_99999"),
                                        ("vk", "vk_bot_legacy.py", "timezone_set_99999")):
            ns = self.namespace(platform)
            event = SimpleNamespace(data=command, from_user=SimpleNamespace(id=123),
                                    user_id=123, payload={"cmd": command},
                                    message=SimpleNamespace(edit_text=AsyncMock()))
            await load_handler(file, ns, "timezone_set")(event)
            ns["set_student_timezone_offset"].assert_not_awaited()
