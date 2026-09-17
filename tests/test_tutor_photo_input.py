"""Photo editing must pass the input guard and persist the Telegram file ID."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from input_hardening import install_telegram_input_hardening


class Message:
    def __init__(self, *, text=None, photo=None):
        self.text = text
        self.photo = photo
        self.answer = AsyncMock()


class PhotoInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guards = []
        states = SimpleNamespace(**{
            name: SimpleNamespace(state=name)
            for name in ("waiting_photo", "waiting_new_value", "waiting_subject_name",
                         "adding_subject_name", "editing_subject_name_state")
        })
        legacy = SimpleNamespace(
            AdminStates=states, BaseMiddleware=object, Message=Message,
            dp=SimpleNamespace(message=SimpleNamespace(outer_middleware=self.guards.append)),
        )
        install_telegram_input_hardening(SimpleNamespace(legacy=legacy))
        self.guard = self.guards[0]

    def state(self, name="waiting_new_value", field="photo"):
        return SimpleNamespace(
            get_state=AsyncMock(return_value=name),
            get_data=AsyncMock(return_value={"edit_tutor_id": 42, "edit_field": field}),
            clear=AsyncMock(),
        )

    async def test_photo_edit_reaches_save_handler_and_uses_largest_photo(self):
        source = Path(__file__).resolve().parents[1] / "archive" / "Bot_test_legacy.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_new_value")
        fn.decorator_list = []
        for arg in fn.args.args:
            arg.annotation = None
        save = AsyncMock()
        namespace = {
            "update_tutor": save,
            "InlineKeyboardMarkup": lambda **kw: kw,
            "InlineKeyboardButton": lambda **kw: kw,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     str(source), "exec"), namespace)
        state = self.state()
        message = Message(photo=[SimpleNamespace(file_id="small"),
                                 SimpleNamespace(file_id="large")])

        async def handler(event, data):
            await namespace["process_new_value"](event, data["state"])

        await self.guard(handler, message, {"state": state})
        save.assert_awaited_once_with(42, photo="large")
        state.clear.assert_awaited_once()
        self.assertIn("сохранены", message.answer.await_args.args[0])

    async def test_photos_still_rejected_for_text_fields(self):
        for field in ("name", "desc", "inn", "telegram_id", "vk_id", "commission"):
            with self.subTest(field=field):
                handler = AsyncMock()
                message = Message(photo=[SimpleNamespace(file_id="photo")])
                await self.guard(handler, message, {"state": self.state(field=field)})
                handler.assert_not_awaited()
                message.answer.assert_awaited_once()

    async def test_other_media_cannot_clear_existing_photo(self):
        handler = AsyncMock()
        message = Message()
        await self.guard(handler, message, {"state": self.state()})
        handler.assert_not_awaited()

    async def test_add_photo_and_text_messages_still_pass(self):
        for message, state in (
            (Message(photo=[SimpleNamespace(file_id="photo")]), self.state("waiting_photo")),
            (Message(text="нет"), self.state()),
            (Message(text="Новое имя"), self.state(field="name")),
        ):
            handler = AsyncMock()
            await self.guard(handler, message, {"state": state})
            handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
