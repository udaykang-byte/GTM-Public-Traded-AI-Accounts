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

    def get_companies(self, status=None):
        return [dict(COMPANY)] if status in (None, "enriched") else []

    def all_signals(self):
        return {1: [dict(SIGNAL)]}

    def get_company_by_ticker(self, ticker):
        return dict(COMPANY) if ticker == "TST" else None

    def insert_score(self, row):
        self.scores.append(row)

    def set_status(self, cik, status, profile=None):
        self.statuses.append((cik, str(status), profile))


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
