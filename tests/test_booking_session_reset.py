"""Exercise real tutor-selection handlers without starting either bot."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]


def load_handler(filename, namespace):
    tree = ast.parse((ROOT / "archive" / filename).read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "choose_tutor_booking")
    node.decorator_list = []
    for argument in node.args.args:
        argument.annotation = None
    node.returns = None
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, filename, "exec"), namespace)
    return namespace[node.name]


class Session:
    def __init__(self):
        self.data = dict(tutor_id=1, tutor_name="Old", subject="Old subject",
                         date="01.10.2026", time_slot="12:00-13:00",
                         booking_subject_index=0)
        self.state = "waiting_confirmation"

    async def clear(self):
        self.data = {}
        self.state = None

    async def set_state(self, value):
        self.state = value

    async def update_data(self, **values):
        self.data.update(values)

    async def delete(self, user_id):
        await self.clear()

    async def set(self, user_id, value):
        await self.set_state(value)

    async def update(self, user_id, **values):
        await self.update_data(**values)


class TutorSelectionSessionTests(unittest.IsolatedAsyncioTestCase):
    def assert_fresh_selection(self, session):
        self.assertEqual(session.state, "choosing_subject")
        self.assertEqual(session.data, {"tutor_id": 2, "tutor_name": "New"})

    async def test_telegram_old_tutor_button_discards_pending_confirmation(self):
        session = Session()
        call = SimpleNamespace(
            data="tutor_booking_2",
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        keyboard = AsyncMock(return_value="subjects")
        handler = load_handler("Bot_test_legacy.py", {
            "safe_answer": AsyncMock(),
            "get_all_tutors": AsyncMock(return_value={2: {"name": "New"}}),
            "make_subjects_keyboard": keyboard,
            "BookingStates": SimpleNamespace(choosing_subject="choosing_subject"),
        })
        await handler(call, session)
        self.assert_fresh_selection(session)
        keyboard.assert_awaited_once_with(2, back_callback="back_to_tutors_booking")
        call.message.edit_text.assert_awaited_once()

    async def test_vk_old_tutor_button_discards_previous_subject_and_slot(self):
        session = Session()
        event = SimpleNamespace(user_id=123, payload={"tutor_id": 2})
        keyboard = AsyncMock(return_value="subjects")
        edit = AsyncMock()
        handler = load_handler("vk_bot_legacy.py", {
            "get_all_tutors": AsyncMock(return_value={2: {"name": "New"}}),
            "make_subjects_keyboard": keyboard,
            "state_dispenser": session,
            "edit_event_message": edit,
            "BookingStates": SimpleNamespace(choosing_subject="choosing_subject"),
        })
        await handler(event)
        self.assert_fresh_selection(session)
        keyboard.assert_awaited_once_with(2, back_callback="back_to_tutors_booking")
        edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
