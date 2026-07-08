"""ingest() wiring: prescreen failures are written as disqualified rows with
dq_reason/tier (never enriched); --force bypasses; already-known tickers get
no DQ treatment (nothing is written for them). db + universe monkeypatched —
no network, no Supabase."""
import pytest

from pipeline import cli
from pipeline.models import Company


def make_company(**over) -> Company:
    base = dict(
        cik=99, ticker="BIGCO", name="Big Software Co", exchange="Nasdaq",
        sic="7372", sic_description="software", sector_bucket="saas",
        market_cap=900_000_000,  # far above the default 300M cap band
    )
    base.update(over)
    return Company(**base)


@pytest.fixture
def db_calls(monkeypatch):
    from pipeline import db, universe

    calls: dict = {"upserted": []}
    monkeypatch.setattr(universe, "resolve_tickers", lambda ts: ([make_company()], []))
    monkeypatch.setattr(db, "existing_ciks", lambda: set())
    monkeypatch.setattr(
        db, "upsert_companies",
        lambda rows: (calls["upserted"].extend(rows), len(rows))[1],
    )
    return calls


def test_ingest_writes_prescreen_failure_as_disqualified(db_calls, capsys):
    cli.ingest(tickers="BIGCO", csv=None, dry_run=False, force=False)

    [c] = db_calls["upserted"]
    assert c.status.value == "disqualified"
    assert c.dq_reason == "outside_cap_band"
    assert c.tier == "T4"
    out = capsys.readouterr().out
    assert "prescreen disqualified 1" in out


def test_ingest_force_bypasses_prescreen(db_calls, capsys):
    cli.ingest(tickers="BIGCO", csv=None, dry_run=False, force=True)

    [c] = db_calls["upserted"]
    assert c.status.value == "new"
    assert c.dq_reason == ""
    assert c.tier is None
    assert "prescreen disqualified" not in capsys.readouterr().out


def test_ingest_known_ticker_gets_no_dq_treatment(db_calls, monkeypatch, capsys):
    from pipeline import db

    monkeypatch.setattr(db, "existing_ciks", lambda: {99})
    cli.ingest(tickers="BIGCO", csv=None, dry_run=False, force=False)

    assert db_calls["upserted"] == []  # nothing written for known rows
    out = capsys.readouterr().out
    assert "DQ:" not in out  # no DQ note for rows nothing is written for
    assert "prescreen disqualified" not in out


def test_ingest_dry_run_previews_dq_without_writing(db_calls, capsys):
    cli.ingest(tickers="BIGCO", csv=None, dry_run=True, force=False)

    assert db_calls["upserted"] == []
    out = capsys.readouterr().out
    assert "prescreen disqualified 1" in out
