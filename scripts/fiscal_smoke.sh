#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/nikg18/edubots/venv/bin/python}"
cd "$ROOT"

printf '%s\n' '== py_compile =='
"$PYTHON_BIN" -m py_compile \
  fiscalization.py \
  fiscal_admin.py \
  payments.py \
  payment_reuse_telegram.py \
  payment_reuse_vk.py

printf '%s\n' '== unit tests =='
"$PYTHON_BIN" -m unittest tests.test_fiscalization -v

printf '%s\n' '== fiscal preflight (read-only business data + safe schema creation) =='
set +e
"$PYTHON_BIN" fiscal_admin.py preflight
PREFLIGHT_RC=$?
set -e

if [[ "$PREFLIGHT_RC" -eq 0 ]]; then
  printf '%s\n' 'FISCAL_SMOKE=CODE_OK_PREFLIGHT_READY'
  exit 0
fi

# NOT READY is an expected state before profiles/AgentSign/external setup are finished.
if [[ "$PREFLIGHT_RC" -eq 2 ]]; then
  printf '%s\n' 'FISCAL_SMOKE=CODE_OK_PREFLIGHT_NOT_READY'
  exit 0
fi

printf 'FISCAL_SMOKE=FAILED preflight_rc=%s\n' "$PREFLIGHT_RC" >&2
exit "$PREFLIGHT_RC"
