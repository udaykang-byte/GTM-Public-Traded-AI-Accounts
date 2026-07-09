"""db.py helpers: the pure ones (order_by_tier_priority) tested directly;
set_status's status_changed_at gating tested against a fake in-memory
Supabase client chain (monkeypatched db.client) — no network."""
from pipeline import db
from pipeline.db import order_by_tier_priority


def test_orders_by_tier_ascending_then_priority_descending():
    companies = [
        {"cik": 1, "tier": "T2"},
        {"cik": 2, "tier": "T1"},
        {"cik": 3, "tier": "T1"},
    ]
    priority = {1: 50.0, 2: 10.0, 3: 90.0}
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [3, 2, 1]


def test_null_tier_sorts_as_t3():
    companies = [
        {"cik": 1, "tier": None},
        {"cik": 2, "tier": "T2"},
        {"cik": 3, "tier": "T4"},
    ]
    result = order_by_tier_priority(companies, {})
    assert [c["cik"] for c in result] == [2, 1, 3]


def test_unrecognized_tier_value_sorts_as_t3():
    companies = [{"cik": 1, "tier": "bogus"}, {"cik": 2, "tier": "T1"}]
    result = order_by_tier_priority(companies, {})
    assert [c["cik"] for c in result] == [2, 1]


def test_missing_priority_sorts_last_within_tier():
    companies = [{"cik": 1, "tier": "T1"}, {"cik": 2, "tier": "T1"}]
    priority = {1: 20.0}  # cik 2 missing from the map entirely
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [1, 2]


def test_none_priority_value_sorts_last_within_tier():
    companies = [{"cik": 1, "tier": "T1"}, {"cik": 2, "tier": "T1"}]
    priority = {1: 20.0, 2: None}
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [1, 2]


def test_empty_companies_list():
    assert order_by_tier_priority([], {}) == []


# ---------- v3 phase 4: set_status stamps status_changed_at only on ----------
# ---------- a real transition, never on a no-op re-assert            ----------

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, client, mode, payload=None):
        self._client = client
        self._mode = mode
        self._payload = payload

    def select(self, cols):
        return _FakeQuery(self._client, "select")

    def update(self, patch):
        return _FakeQuery(self._client, "update", patch)

    def eq(self, col, val):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self._mode == "update":
            self._client.updates.append(self._payload)
            self._client.current_status = self._payload.get("status", self._client.current_status)
            return _FakeResult([])
        return _FakeResult([{"status": self._client.current_status}])


class FakeSupabase:
    """Just enough of the supabase client chain for set_status:
    .table().select().eq().limit().execute() and .table().update().eq().execute()."""

    def __init__(self, current_status):
        self.current_status = current_status
        self.updates: list[dict] = []

    def table(self, name):
        return _FakeQuery(self, None)


def test_set_status_same_status_does_not_restamp_status_changed_at(monkeypatch):
    fake = FakeSupabase(current_status="enriched")
    monkeypatch.setattr(db, "client", lambda: fake)

    # the standard two-pass enrich (edgar then parallel) re-asserts 'enriched'
    db.set_status(1, "enriched")
    db.set_status(1, "enriched")

    assert len(fake.updates) == 2
    for patch in fake.updates:
        assert "status_changed_at" not in patch  # dwell time never reset by a no-op


def test_set_status_real_transition_stamps_status_changed_at(monkeypatch):
    fake = FakeSupabase(current_status="enriched")
    monkeypatch.setattr(db, "client", lambda: fake)

    db.set_status(1, "scored")

    [patch] = fake.updates
    assert patch["status"] == "scored"
    assert patch["status_changed_at"]  # stamped, non-empty ISO string


def test_set_status_noop_then_transition_stamps_only_the_transition(monkeypatch):
    fake = FakeSupabase(current_status="enriched")
    monkeypatch.setattr(db, "client", lambda: fake)

    db.set_status(1, "enriched")   # no-op
    db.set_status(1, "qualified")  # real transition (fake tracks the update)
    db.set_status(1, "qualified")  # no-op again at the new status

    stamps = ["status_changed_at" in p for p in fake.updates]
    assert stamps == [False, True, False]


def test_set_status_accepts_status_enum_and_still_gates(monkeypatch):
    from pipeline.models import Status

    fake = FakeSupabase(current_status="enriched")
    monkeypatch.setattr(db, "client", lambda: fake)

    db.set_status(1, Status.enriched)

    [patch] = fake.updates
    assert patch["status"] == "enriched"
    assert "status_changed_at" not in patch
