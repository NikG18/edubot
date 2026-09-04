# Archive

This directory contains legacy implementations and one-off migration/patch scripts retained for history and transitional compatibility.

Archived on the PR #5 hardening branch:

- `Bot_test_legacy.py` — original Telegram legacy implementation.
- `vk_bot_legacy.py` — original VK legacy implementation.
- `database_legacy.py` — original database compatibility implementation.
- `messaging_legacy.py` — original messaging compatibility implementation.
- `database_sql_old.py` — obsolete SQL/database implementation.
- `apply_vk_fix.py` — one-off VK patch script.
- `mig.py` — obsolete SQLite/Neon migration script.

The repository root keeps tiny compatibility aliases for the four `*_legacy.py` modules that are still imported by the active compatibility layers. Those root files contain no legacy business logic; they only alias the archived modules so the current hardened entrypoints remain bootable during the refactor.

New functionality must not be added to files in this directory.
