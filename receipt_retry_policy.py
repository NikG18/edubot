"""Pure retry policy shared by regular and subscription closing receipts."""

FINAL_RECEIPT_STATUSES = frozenset({"submitted", "sent"})


def closing_receipt_claim_action(status: str | None) -> str:
    """Return the safe action for an already existing closing-receipt attempt.

    `unknown` is never retried blindly: a timeout may mean the fiscal provider
    accepted the request but the response was lost.
    """
    value = str(status or "").lower()
    if value in FINAL_RECEIPT_STATUSES:
        return "already_submitted"
    if value == "sending":
        return "in_progress"
    if value == "unknown":
        return "unknown"
    if value == "failed":
        return "retry"
    return "not_retryable"
