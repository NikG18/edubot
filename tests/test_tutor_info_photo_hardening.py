from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

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


if __name__ == "__main__":
    unittest.main()
