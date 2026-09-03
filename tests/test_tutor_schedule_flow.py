import ast
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "Bot_test_legacy.py").read_text(
    encoding="utf-8"
)
MODULE = ast.parse(SOURCE)


def async_function(name: str) -> ast.AsyncFunctionDef:
    for node in MODULE.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function {name!r} not found")


class TutorScheduleStateTests(unittest.TestCase):
    def test_day_management_enters_the_state_expected_by_action_buttons(self):
        function = async_function("_show_day_management")
        transitions = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_state"
        ]

        self.assertTrue(
            any(
                call.args
                and isinstance(call.args[0], ast.Attribute)
                and call.args[0].attr == "manage_day_slots"
                for call in transitions
            )
        )

    def test_day_callback_is_acknowledged(self):
        function = async_function("edit_day")
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "safe_answer"
                for node in ast.walk(function)
            )
        )

    def test_delete_cancel_returns_to_selected_day(self):
        delete_start = async_function("del_slot_start")
        self.assertTrue(
            any(
                isinstance(node, ast.Constant)
                and node.value == "back_to_schedule_day"
                for node in ast.walk(delete_start)
            )
        )

        back_handler = async_function("back_to_schedule_day")
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_show_day_management"
                for node in ast.walk(back_handler)
            )
        )


if __name__ == "__main__":
    unittest.main()
