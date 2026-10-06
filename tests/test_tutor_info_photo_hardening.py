from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import tutor_info_photo_hardening as photo_hardening


class _TelegramBadRequest(Exception):
    pass


class TutorInfoPhotoHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        photo_hardening._runtime_legacy = SimpleNamespace(
            TelegramBadRequest=_TelegramBadRequest,
        )

    async def test_photo_callback_message_is_replaced_with_text_message(self):
        message = SimpleNamespace(
            content_type="photo",
            delete=AsyncMock(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        call = SimpleNamespace(message=message)

        await photo_hardening._replace_with_text(call, "Карточка", "keyboard")

        message.edit_text.assert_not_awaited()
        message.delete.assert_awaited_once()
        message.answer.assert_awaited_once_with("Карточка", reply_markup="keyboard")

    async def test_text_callback_message_is_edited_in_place(self):
        message = SimpleNamespace(
            content_type="text",
            delete=AsyncMock(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        call = SimpleNamespace(message=message)

        await photo_hardening._replace_with_text(call, "Список", "keyboard")

        message.edit_text.assert_awaited_once_with("Список", reply_markup="keyboard")
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_own_profile_photo_has_no_caption_and_navigation_is_text(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), update_data=AsyncMock())
        message = SimpleNamespace(content_type="photo", chat=SimpleNamespace(id=123),
                                  delete=AsyncMock(), answer=AsyncMock(), edit_text=AsyncMock())
        bot = SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=42)))
        call = SimpleNamespace(data="tutor_profile_7", from_user=SimpleNamespace(id=123),
                               message=message, bot=bot)
        description = "Длинная анкета. " * 100
        legacy = SimpleNamespace(
            TelegramBadRequest=_TelegramBadRequest, safe_answer=AsyncMock(),
            get_tutor_by_telegram_id=AsyncMock(return_value=7),
            get_all_tutors=AsyncMock(return_value={7: dict(description=description,
                                                         photo="database-file-id", subjects={"Химия": 2500})}),
            dp=SimpleNamespace(fsm=SimpleNamespace(get_context=lambda *args, **kwargs: state)),
            InlineKeyboardMarkup=lambda **kwargs: kwargs,
            InlineKeyboardButton=lambda **kwargs: kwargs,
        )
        with patch.object(photo_hardening, "_runtime_legacy", legacy):
            await photo_hardening._hardened_show_tutor_own_profile(call)
        bot.send_photo.assert_awaited_once_with(chat_id=123, photo="database-file-id")
        message.edit_text.assert_not_awaited()
        self.assertIn(description, message.answer.await_args.args[0])
        button = message.answer.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["callback_data"], "back_to_tutor_panel_7")
        state.update_data.assert_awaited_once_with(tutor_info_photo_message_id=42)

    async def test_own_profile_rejects_another_tutors_button(self):
        legacy = SimpleNamespace(get_tutor_by_telegram_id=AsyncMock(return_value=8),
                                 safe_answer=AsyncMock(), get_all_tutors=AsyncMock())
        call = SimpleNamespace(data="tutor_profile_7", from_user=SimpleNamespace(id=123))
        with patch.object(photo_hardening, "_runtime_legacy", legacy):
            await photo_hardening._hardened_show_tutor_own_profile(call)
        legacy.get_all_tutors.assert_not_awaited()
        self.assertTrue(legacy.safe_answer.await_args.kwargs["show_alert"])

    async def test_back_to_panel_removes_tracked_own_photo(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={"tutor_info_photo_message_id": 42}),
                                update_data=AsyncMock())
        bot = SimpleNamespace(delete_message=AsyncMock())
        call = SimpleNamespace(data="back_to_tutor_panel_7", from_user=SimpleNamespace(id=123),
                               message=SimpleNamespace(chat=SimpleNamespace(id=123)), bot=bot)
        legacy = SimpleNamespace(
            get_tutor_by_telegram_id=AsyncMock(return_value=7),
            dp=SimpleNamespace(fsm=SimpleNamespace(get_context=lambda *args, **kwargs: state)),
            _own_profile_original_back=AsyncMock(), TelegramBadRequest=_TelegramBadRequest,
        )
        with patch.object(photo_hardening, "_runtime_legacy", legacy):
            await photo_hardening._hardened_back_to_tutor_panel(call)
        bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=42)
        legacy._own_profile_original_back.assert_awaited_once_with(call)


if __name__ == "__main__":
    unittest.main()
