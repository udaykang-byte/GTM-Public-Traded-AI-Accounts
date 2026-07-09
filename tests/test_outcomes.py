"""outcomes.py: monotonic status advancement for outcome events.

next_status() is pure (table-driven below); record() is the only function
that talks to db.py, monkeypatched here as a FakeDB — no Supabase, no
network (same pattern as test_scoring.py)."""
import pytest

from pipeline import outcomes


# ---------- next_status: pure monotonic-advancement rule ----------

@pytest.mark.parametrize(
    "current,event,expected",
    [
        # ladder: draft < approved < exported < sent < replied < positive_reply < meeting
        ("draft", "approved", "approved"),
        ("approved", "exported", "exported"),
        ("exported", "sent", "sent"),
        ("sent", "replied", "replied"),
        ("replied", "positive_reply", "positive_reply"),
        ("positive_reply", "meeting", "meeting"),
        # skipping stages still advances (event maps straight to its status)
        ("draft", "sent", "sent"),
        ("draft", "meeting", "meeting"),
        # same-or-backward events never move status backward
        ("sent", "approved", "sent"),
        ("meeting", "sent", "meeting"),
        ("replied", "replied", "replied"),  # duplicate event: no-op on status
    ],
)
def test_ladder_advances_only_forward(current, event, expected):
    assert outcomes.next_status(current, event) == expected


@pytest.mark.parametrize("terminal", ["rejected", "bounced", "opted_out"])
@pytest.mark.parametrize("event", ["approved", "sent", "replied", "positive_reply", "meeting", "opt_out"])
def test_terminal_states_never_advance_further(terminal, event):
    assert outcomes.next_status(terminal, event) == terminal


@pytest.mark.parametrize(
    "current,event,expected_terminal",
    [
        ("draft", "rejected", "rejected"),
        ("approved", "rejected", "rejected"),
        ("sent", "bounced", "bounced"),
        ("replied", "opt_out", "opted_out"),
        ("meeting", "opt_out", "opted_out"),  # terminal entry always allowed, even from the top
    ],
)
def test_entering_a_terminal_state_is_always_allowed_from_a_non_terminal_state(current, event, expected_terminal):
    assert outcomes.next_status(current, event) == expected_terminal


def test_opt_out_event_maps_to_opted_out_status():
    assert outcomes.EVENT_TO_STATUS["opt_out"] == "opted_out"


def test_unrecognized_current_status_does_not_crash():
    # defensive: a status outside the known ladder (e.g. future value) should
    # not raise — treat as "below everything", i.e. any known event advances.
    assert outcomes.next_status("some_future_status", "sent") == "sent"


# ---------- record(): DB-facing wrapper ----------

class FakeDB:
    def __init__(self, initial_status="draft"):
        self.events: list[tuple] = []
        self.status = initial_status
        self.advanced_to: list[str] = []

    def insert_message_event(self, message_id, event, occurred_at, note=""):
        self.events.append((message_id, event, occurred_at, note))

    def get_message(self, message_id):
        if message_id != 1:
            return None
        return {"id": 1, "status": self.status}

    def advance_message_status(self, message_id, status):
        self.advanced_to.append(status)
        self.status = status


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(outcomes, "db", fake)
    return fake


def test_record_always_inserts_the_event(fake_db):
    outcomes.record(1, "sent", occurred_at="2026-07-01", note="via CSV")
    assert fake_db.events == [(1, "sent", "2026-07-01", "via CSV")]


def test_record_advances_status_when_event_moves_forward(fake_db):
    result = outcomes.record(1, "sent")
    assert fake_db.advanced_to == ["sent"]
    assert result == {
        "message_id": 1, "event": "sent",
        "previous_status": "draft", "new_status": "sent", "advanced": True,
    }


def test_record_does_not_advance_on_a_backward_or_duplicate_event(fake_db):
    fake_db.status = "sent"
    result = outcomes.record(1, "approved")
    assert fake_db.advanced_to == []  # db.advance_message_status never called
    assert result["advanced"] is False
    assert result["new_status"] == "sent"


def test_record_never_advances_out_of_a_terminal_status(fake_db):
    fake_db.status = "bounced"
    result = outcomes.record(1, "meeting")
    assert fake_db.advanced_to == []
    assert result == {
        "message_id": 1, "event": "meeting",
        "previous_status": "bounced", "new_status": "bounced", "advanced": False,
    }
    # the event is still logged even though status never moves
    assert fake_db.events == [(1, "meeting", fake_db.events[0][2], "")]


def test_record_defaults_occurred_at_to_now_iso_string(fake_db):
    outcomes.record(1, "sent")
    _, _, occurred_at, _ = fake_db.events[0]
    assert isinstance(occurred_at, str) and len(occurred_at) >= 19  # ISO-ish, not empty


def test_record_rejects_unknown_event(fake_db):
    with pytest.raises(ValueError):
        outcomes.record(1, "nonsense_event")
    assert fake_db.events == []  # never inserts an invalid event


def test_record_raises_on_unknown_message_id(fake_db):
    with pytest.raises(ValueError):
        outcomes.record(999, "sent")
