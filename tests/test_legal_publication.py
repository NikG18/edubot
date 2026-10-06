import ast
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", "postgresql://unused")


def legal_constants():
    """Load the actual catalogue without importing the bot/database runtime."""
    tree = ast.parse((ROOT / "legal_common.py").read_text())
    scope = {"DOC_BASE_URL": "https://example.test/legal"}
    names = {"DOC_VERSION", "DOC_EDITION_URL", "DOCS", "BEFORE_PAYMENT_DOC_TYPES",
             "STUDENT_DOC_TYPES", "TUTOR_DOC_TYPES"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in names:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "catalogue", "exec"), scope)
    return scope


class LegalPublicationTests(unittest.TestCase):
    def test_catalogue_points_to_complete_version_with_exact_hashes(self):
        scope = legal_constants()
        edition = ROOT / "legal" / "versions" / scope["DOC_VERSION"]
        manifest = json.loads((edition / "manifest.json").read_text())
        self.assertEqual(manifest["version"], scope["DOC_VERSION"])
        for doc in scope["DOCS"].values():
            filename = doc["url"].rsplit("/", 1)[1]
            with self.subTest(filename=filename):
                self.assertIn(f'/versions/{scope["DOC_VERSION"]}/', doc["url"])
                data = (edition / filename).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), doc["hash"])
                self.assertEqual(manifest["sha256"][filename], doc["hash"])
                self.assertEqual(data, (ROOT / "legal" / filename).read_bytes())

    def test_refund_rules_are_available_before_payment_and_to_both_roles(self):
        scope = legal_constants()
        for name in ("BEFORE_PAYMENT_DOC_TYPES", "STUDENT_DOC_TYPES", "TUTOR_DOC_TYPES"):
            with self.subTest(role=name):
                self.assertIn("refund_rules", scope[name])

    def test_old_offer_is_preserved_separately(self):
        old = ROOT / "legal/versions/2026-09-09-r1/02_student_offer.md"
        current = ROOT / "legal/02_student_offer.md"
        self.assertTrue(old.is_file())
        self.assertNotEqual(old.read_bytes(), current.read_bytes())


class LateCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_late_cancel_button_cannot_finalize_or_charge(self):
        import completion_hardening as completion
        legacy = SimpleNamespace(safe_answer=AsyncMock())
        with patch.object(completion._db, 'get_booking', new_callable=AsyncMock) as get_booking, \
             patch.object(completion, '_mark_completed', new_callable=AsyncMock) as mark, \
             patch.object(completion, '_finalize_completed_booking', new_callable=AsyncMock) as finalize:
            await completion._finalize_admin_outcome(None, legacy, object(), 123, 'late_student_cancel')
            get_booking.assert_not_awaited()
            mark.assert_not_awaited()
            finalize.assert_not_awaited()
            legacy.safe_answer.assert_awaited_once()

    async def test_only_held_outcome_can_be_recorded_as_completed(self):
        import completion_hardening as completion
        self.assertEqual(completion.completion_outcome_details('held')[0], 'completed')
        with self.assertRaises(ValueError):
            completion.completion_outcome_details('late_student_cancel')


if __name__ == '__main__':
    unittest.main()
