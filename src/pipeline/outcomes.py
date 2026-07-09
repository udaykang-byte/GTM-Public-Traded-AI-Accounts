"""Outcome tracking for drafted outreach sequences (v3 phase 4).

A message's lifecycle is append-only events (message_events) plus a single
current `messages.status` that only ever moves forward on a fixed ladder.
The ladder and the advancement rule are pure functions with no DB calls, so
they're unit-testable on their own; `record()` is the sole entry point that
talks to db.py — every write goes through it, per project convention.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from pipeline import db

# Event name (message_events.event) -> the messages.status value it drives.
# 'opt_out' is the event name; 'opted_out' is the status name — both CHECK
# constraints in sql/schema.sql must keep using these exact spellings.
EVENT_TO_STATUS: dict[str, str] = {
    "approved": "approved",
    "rejected": "rejected",
    "exported": "exported",
    "sent": "sent",
    "bounced": "bounced",
    "replied": "replied",
    "positive_reply": "positive_reply",
    "meeting": "meeting",
    "opt_out": "opted_out",
}

EVENTS: tuple[str, ...] = tuple(EVENT_TO_STATUS)

# Monotonic ladder: a message's status only ever moves to a higher rank.
# Terminal statuses rank above every ladder stage (including 'meeting') so
# they can always be *entered* from a non-terminal status, but — because
# next_status() special-cases "current is terminal" below — they can never
# be *left* once entered, regardless of what event arrives next.
STATUS_RANK: dict[str, int] = {
    "draft": 0,
    "approved": 1,
    "exported": 2,
    "sent": 3,
    "replied": 4,
    "positive_reply": 5,
    "meeting": 6,
    "rejected": 7,
    "bounced": 7,
    "opted_out": 7,
}

TERMINAL_STATUSES = frozenset({"rejected", "bounced", "opted_out"})


def next_status(current: str, event: str) -> str:
    """Pure monotonic-advancement rule. `current` is a messages.status value
    (any string tolerated — an unrecognized value ranks below every known
    stage, i.e. any known event can still advance it). `event` must be a key
    of EVENT_TO_STATUS."""
    if current in TERMINAL_STATUSES:
        return current
    new_status = EVENT_TO_STATUS[event]
    if STATUS_RANK[new_status] > STATUS_RANK.get(current, -1):
        return new_status
    return current


def record(
    message_id: int,
    event: str,
    occurred_at: date | datetime | str | None = None,
    note: str = "",
) -> dict:
    """Record one outcome event against a drafted message.

    Always inserts the event (append-only audit trail — even a no-op event
    that doesn't move status is logged). Advances messages.status only if
    the monotonic rule allows it. Returns a small summary dict for CLI
    printing; raises ValueError on an unknown event or message_id."""
    if event not in EVENT_TO_STATUS:
        raise ValueError(f"Unknown event {event!r} — expected one of {EVENTS}")

    row = db.get_message(message_id)
    if row is None:
        raise ValueError(f"message_id {message_id} not found")

    db.insert_message_event(message_id, event, _normalize_occurred_at(occurred_at), note)

    current = row["status"]
    new_status = next_status(current, event)
    advanced = new_status != current
    if advanced:
        db.advance_message_status(message_id, new_status)
    return {
        "message_id": message_id,
        "event": event,
        "previous_status": current,
        "new_status": new_status,
        "advanced": advanced,
    }


def _normalize_occurred_at(value: date | datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
