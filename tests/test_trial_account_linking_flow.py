import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "archive"


def parsed(name: str):
    return ast.parse((LEGACY / name).read_text(encoding="utf-8"))


def async_function(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function {name!r} not found")


def calls_named(function: ast.AsyncFunctionDef, name: str):
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


class TrialFlowTests(unittest.TestCase):
    def test_both_bots_recheck_trial_and_store_email_on_booking(self):
        for filename in ("Bot_test_legacy.py", "vk_bot_legacy.py"):
            module = parsed(filename)
            function = async_function(module, "confirm_trial_booking")
            self.assertTrue(calls_named(function, "is_trial_available"), filename)
            add_calls = calls_named(function, "add_booking")
            self.assertEqual(len(add_calls), 1, filename)
            keywords = {keyword.arg: keyword.value for keyword in add_calls[0].keywords}
            self.assertIn("trial_email", keywords, filename)
            self.assertIsInstance(keywords.get("booking_type"), ast.Constant, filename)
            self.assertEqual(keywords["booking_type"].value, "trial", filename)

    def test_payment_email_is_copied_to_platform_student_profile(self):
        expectations = {
            "Bot_test_legacy.py": ("process_payment_email_state", "telegram"),
            "vk_bot_legacy.py": ("process_payment_email", "vk"),
        }
        for filename, (function_name, platform) in expectations.items():
            function = async_function(parsed(filename), function_name)
            calls = calls_named(function, "set_student_email")
            self.assertEqual(len(calls), 1, filename)
            self.assertEqual(calls[0].args[0].value, platform, filename)


class AccountLinkingFlowTests(unittest.TestCase):
    def test_both_bots_create_and_consume_one_time_codes(self):
        expectations = {
            "Bot_test_legacy.py": ("account_link_create", "account_link_code_received"),
            "vk_bot_legacy.py": ("account_link_create", "account_link_code_received"),
        }
        for filename, (create_name, consume_name) in expectations.items():
            module = parsed(filename)
            self.assertTrue(
                calls_named(async_function(module, create_name), "create_account_link_code"),
                filename,
            )
            self.assertTrue(
                calls_named(async_function(module, consume_name), "consume_account_link_code"),
                filename,
            )

    def test_linked_accounts_drive_student_statistics(self):
        for filename in ("Bot_test_legacy.py", "vk_bot_legacy.py"):
            function = async_function(parsed(filename), "show_student_stats")
            self.assertTrue(calls_named(function, "get_bookings_for_account"), filename)

    def test_vk_actions_use_linked_profile_ownership(self):
        source = (ROOT / "vk_bot.py").read_text(encoding="utf-8")
        self.assertIn("account_owns_booking(\"vk\", event.user_id, booking)", source)
        self.assertIn("legacy._require_vk_booking_owner = _linked_vk_booking_owner", source)

    def test_conflicting_profile_emails_are_reset_instead_of_silently_overwritten(self):
        source = (ROOT / "database.py").read_text(encoding="utf-8")
        self.assertIn("email_reset = bool(", source)
        self.assertIn("merged_email = None if email_reset", source)
        for filename in ("Bot_test_legacy.py", "vk_bot_legacy.py"):
            self.assertIn(
                'if result.get("email_reset")',
                (LEGACY / filename).read_text(encoding="utf-8"),
            )


class ScheduleMigrationTests(unittest.TestCase):
    def test_old_schedule_tables_receive_unique_index(self):
        source = (LEGACY / "database_legacy.py").read_text(encoding="utf-8")
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_slot", source)
        self.assertIn("async def add_schedule_slots", source)
        self.assertIn("ON CONFLICT DO NOTHING", source)


if __name__ == "__main__":
    unittest.main()
