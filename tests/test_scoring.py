import json
from pathlib import Path

import pytest

from pipeline import scoring

COMPANY = {
    "cik": 1, "ticker": "TST", "name": "Test Co", "exchange": "Nasdaq",
    "sector_bucket": "saas", "market_cap": 1e8, "sic_description": "software",
    "website": None, "hq_state": "CA", "status": "enriched",
}
SIGNAL = {
    "type": "E1", "source": "edgar", "title": "AI language", "detail": "d",
    "evidence_url": None, "evidence_quote": None, "observed_at": "2026-06-01",
    "weight": 15.0,
}


class FakeDB:
    def __init__(self):
        self.scores = []
        self.statuses = []
        self.angles_rows = []
        self.company_status = "enriched"
        self.stale_marked = []

    def get_companies(self, status=None):
        return [dict(COMPANY)] if status in (None, "enriched") else []

    def all_signals(self):
        return {1: [dict(SIGNAL)]}

    def get_company_by_ticker(self, ticker):
        return {**dict(COMPANY), "status": self.company_status} if ticker == "TST" else None

    def insert_score(self, row):
        self.scores.append(row)

    def set_status(self, cik, status, profile=None):
        self.statuses.append((cik, str(status), profile))

    def all_angles(self):
        return {1: [dict(r) for r in self.angles_rows]} if self.angles_rows else {}

    def get_angles(self, cik):
        return [dict(r) for r in self.angles_rows]

    def mark_angles_stale(self, ids):
        self.stale_marked.extend(ids)
        return len(ids)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    q, r, a = tmp_path / "queue", tmp_path / "results", tmp_path / "archive"
    for d in (q, r, a):
        d.mkdir()
    monkeypatch.setattr(scoring, "QUEUE_DIR", q)
    monkeypatch.setattr(scoring, "RESULTS_DIR", r)
    monkeypatch.setattr(scoring, "ARCHIVE_DIR", a)
    monkeypatch.setattr(scoring, "db", FakeDB())
    return q, r, a


def test_prepare_writes_shared_file_and_slim_packets(dirs):
    q, r, a = dirs
    written = scoring.prepare()

    shared = json.loads((q / "_shared.json").read_text())
    assert set(shared) == {"services_catalog", "rubric", "output_schema", "instructions"}

    assert written == [str(q / "TST.json")]
    packet = json.loads((q / "TST.json").read_text())
    for heavy in ("services_catalog", "rubric", "output_schema"):
        assert heavy not in packet
    assert packet["shared_file"] == (q / "_shared.json").as_posix()
    assert packet["output_path"] == (r / "TST.json").as_posix()
    assert "_shared.json" in packet["instructions"]
    assert packet["base_score"]["total"] > 0


def test_pending_queue_ignores_shared_file(dirs):
    q, r, a = dirs
    scoring.prepare()
    names = [p.name for p in scoring.pending_queue()]
    assert names == ["TST.json"]


# a real archived verdict doubles as a schema-valid result fixture
_ARCHIVED = sorted(
    p for p in Path("data/scoring_archive").rglob("*.json")
    if not p.name.startswith(("packet_", "_"))
)


@pytest.mark.skipif(not _ARCHIVED, reason="no archived verdicts on this machine")
def test_commit_archives_shared_file_and_drains_queue(dirs):
    q, r, a = dirs
    scoring.prepare()
    # Use the most recent archived verdict: older runs can predate schema
    # changes (e.g. why_now became required) and would fail validation here.
    (r / "TST.json").write_text(_ARCHIVED[-1].read_text())

    summary = scoring.commit(run_id="testrun")

    assert not summary["invalid"] and not summary["orphan"]
    buckets = [b for b in ("qualified", "review", "disqualified")
               if any(i["ticker"] == "TST" for i in summary[b])]
    assert len(buckets) == 1
    run_dir = a / "testrun"
    assert (run_dir / "_shared.json").exists()
    assert (run_dir / "TST.json").exists()
    assert (run_dir / "packet_TST.json").exists()
    assert not (q / "_shared.json").exists()  # queue drained -> shared file removed
    assert scoring.db.scores and scoring.db.statuses


from datetime import date, timedelta

ANGLE_ROW = {
    "id": 3,
    "fingerprint": "funding:0001234-26-000042", "family": "funding",
    "headline": "Offering priced ~$12M — 424B5", "details": {"instrument": "follow_on"},
    "event_date": (date.today() - timedelta(days=20)).isoformat(),
    "strength": 1.0, "evidence_url": "https://sec.gov/x", "evidence_quote": "proceeds",
    "company_cik": 1, "source": "edgar", "status": "active",
}
STALE_ANGLE_ROW = {**ANGLE_ROW, "id": 7, "fingerprint": "funding:old",
                   "event_date": (date.today() - timedelta(days=400)).isoformat()}


def make_verdict(**overrides):
    v = {
        "ticker": "TST", "intent": 25, "capability_gap": 20, "timing": 15,
        "commercial_fit": 15, "profile": "adopter",
        "service_fit": [{"service": "ai_outreach", "priority": 1, "rationale": "r"}],
        "reasoning": "cited", "why_now": "fresh event", "evidence_cited": [],
        "confidence": "high", "angle_ranking": [], "primary_angle": None,
    }
    v.update(overrides)
    return v


def _run_commit(dirs, angles_rows, verdict, company_status="enriched"):
    q, r, a = dirs
    scoring.db.angles_rows = angles_rows
    scoring.db.company_status = company_status
    scoring.prepare()
    (r / "TST.json").write_text(json.dumps(verdict))
    return scoring.commit(run_id="t")


def test_packet_includes_only_active_angles(dirs):
    q, r, a = dirs
    scoring.db.angles_rows = [dict(ANGLE_ROW), dict(STALE_ANGLE_ROW)]
    scoring.prepare()
    packet = json.loads((q / "TST.json").read_text())
    assert [x["fingerprint"] for x in packet["angles"]] == ["funding:0001234-26-000042"]
    assert packet["angles"][0]["age_days"] == 20


def test_prepare_marks_out_of_window_active_angles_stale(dirs):
    q, r, a = dirs
    scoring.db.angles_rows = [dict(ANGLE_ROW), dict(STALE_ANGLE_ROW)]
    scoring.prepare()
    assert scoring.db.stale_marked == [STALE_ANGLE_ROW["id"]]


def test_qualifies_with_active_angle(dirs):
    summary = _run_commit(dirs, [dict(ANGLE_ROW)], make_verdict())
    assert [x["ticker"] for x in summary["qualified"]] == ["TST"]


def test_blocked_without_angle_goes_review_with_reason(dirs):
    summary = _run_commit(dirs, [], make_verdict())
    assert summary["qualified"] == []
    assert summary["review"][0]["gate_reason"] == "no_active_angle"
    assert scoring.db.scores[0]["gate_reason"] == "no_active_angle"


def test_stale_only_angles_also_blocked(dirs):
    summary = _run_commit(dirs, [dict(STALE_ANGLE_ROW)], make_verdict())
    assert summary["qualified"] == []
    assert summary["review"][0]["gate_reason"] == "no_active_angle"


def test_require_angle_false_restores_v1_gate(dirs, monkeypatch):
    monkeypatch.setitem(scoring.SETTINGS.setdefault("scoring", {}), "require_angle", False)
    summary = _run_commit(dirs, [], make_verdict())
    assert [x["ticker"] for x in summary["qualified"]] == ["TST"]


def test_no_demotion_for_contacts_found(dirs):
    low = make_verdict(intent=5, capability_gap=5, timing=5, commercial_fit=5)
    summary = _run_commit(dirs, [dict(ANGLE_ROW)], low, company_status="contacts_found")
    assert summary["kept"][0]["ticker"] == "TST"
    # FakeDB stores str(Status.x) which renders as "Status.contacts_found"
    assert scoring.db.statuses[-1][1].endswith("contacts_found")


def test_no_demotion_clears_gate_reason_when_would_be_blocked(dirs):
    # no angles + a qualifying score would normally be gate-blocked
    # ("no_active_angle"); a contacts_found company must be kept clean of it
    summary = _run_commit(dirs, [], make_verdict(), company_status="contacts_found")
    assert summary["kept"][0]["ticker"] == "TST"
    assert "gate_reason" not in summary["kept"][0]
    assert scoring.db.scores[0]["gate_reason"] == ""


def test_hallucinated_primary_angle_stripped(dirs):
    v = make_verdict(primary_angle={"fingerprint": "made:up", "family": "funding",
                                    "why_this_angle": "x"},
                     angle_ranking=[{"fingerprint": "made:up", "family": "funding",
                                     "message_hook": "h"}])
    _run_commit(dirs, [dict(ANGLE_ROW)], v)
    assert scoring.db.scores[0]["primary_angle"] is None
    assert scoring.db.scores[0]["angle_ranking"] == []
