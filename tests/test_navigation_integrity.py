import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TG_SOURCE = (ROOT / "archive" / "Bot_test_legacy.py").read_text(encoding="utf-8")
VK_SOURCE = (ROOT / "archive" / "vk_bot_legacy.py").read_text(encoding="utf-8")


class NavigationIntegrityTests(unittest.TestCase):
    @staticmethod
    def _string_shape(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                str(part.value) if isinstance(part, ast.Constant) else "123"
                for part in node.values
            )
        return None

    def test_vk_profile_back_buttons_use_routable_command(self):
        module = ast.parse(VK_SOURCE)
        function = next(
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "show_trial_dates"
        )
        source = ast.get_source_segment(VK_SOURCE, function)
        self.assertNotIn('"cmd": "tutor_info"', source)
        self.assertIn('"cmd": f"tutor_info_{tid}"', source)

    def test_vk_router_accepts_buttons_sent_before_fix(self):
        self.assertIn('cmd == "tutor_info" or cmd.startswith("tutor_info_")', VK_SOURCE)

    def test_profile_navigation_clears_abandoned_flow_state(self):
        self.assertIn(
            "async def show_tutor_info(event: MessageEvent):\n"
            "    tid = event.payload.get(\"tutor_id\")\n"
            "    await state_dispenser.delete(event.user_id)",
            VK_SOURCE,
        )
        self.assertIn(
            "async def back_to_tutors(call: CallbackQuery, state: FSMContext):\n"
            "    await state.clear()",
            TG_SOURCE,
        )

    def test_telegram_subject_back_uses_existing_route(self):
        callback_source = (ROOT / "callback_hardening.py").read_text(encoding="utf-8")
        self.assertNotIn('callback_data="back_to_booking_tutors"', callback_source)
        self.assertIn('callback_data="back_to_tutors_booking"', callback_source)

    def test_restart_guards_cover_schedule_back_button(self):
        source = (ROOT / "vk_restart_hardening.py").read_text(encoding="utf-8")
        self.assertIn('"back_to_schedule_day": ("tid", "current_day")', source)

    def test_every_literal_telegram_back_button_has_a_route(self):
        sources = [ROOT / "archive" / "Bot_test_legacy.py", *ROOT.glob("*.py")]
        button_values = set()
        exact_routes = set()
        prefix_routes = set()
        regex_routes = []
        for path in sources:
            module = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == "callback_data":
                            value = self._string_shape(keyword.value)
                            if value and "back" in value:
                                button_values.add(value)
                    if (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"startswith", "regexp"}
                        and node.args
                    ):
                        value = self._string_shape(node.args[0])
                        if value:
                            if node.func.attr == "startswith":
                                prefix_routes.add(value)
                            else:
                                regex_routes.append(value)
                if (
                    isinstance(node, ast.Compare)
                    and len(node.ops) == 1
                    and isinstance(node.ops[0], ast.Eq)
                ):
                    value = self._string_shape(node.comparators[0])
                    if value:
                        exact_routes.add(value)

        unmatched = []
        for value in sorted(button_values):
            if value in exact_routes or any(value.startswith(prefix) for prefix in prefix_routes):
                continue
            if any(re.search(pattern, value) for pattern in regex_routes):
                continue
            unmatched.append(value)
        self.assertEqual(unmatched, [])

    def test_every_literal_vk_back_button_has_a_route(self):
        module = ast.parse(VK_SOURCE)
        button_values = set()
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "payload" or not isinstance(keyword.value, ast.Dict):
                    continue
                mapping = {
                    key.value: value
                    for key, value in zip(keyword.value.keys, keyword.value.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                command = self._string_shape(mapping.get("cmd"))
                if command and ("back" in command or command.startswith("tutor_info")):
                    button_values.add(command)

        router_start = VK_SOURCE.index("async def universal_callback_handler")
        router_end = VK_SOURCE.index("# ==================== Фоновые задачи", router_start)
        router = VK_SOURCE[router_start:router_end]
        exact_routes = set(re.findall(r'cmd == "([^"]+)"', router))
        prefix_routes = set(re.findall(r'cmd\.startswith\("([^"]+)"\)', router))
        unmatched = [
            value for value in sorted(button_values)
            if value not in exact_routes
            and not any(value.startswith(prefix) for prefix in prefix_routes)
        ]
        self.assertEqual(unmatched, [])


if __name__ == "__main__":
    unittest.main()
