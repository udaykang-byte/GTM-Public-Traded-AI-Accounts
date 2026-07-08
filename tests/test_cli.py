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


# ---------- v3: export --messages -> deliverability_checklist.md ----------

def test_deliverability_stats_computes_count_avg_links_and_warned():
    msg_by_cik = {
        1: [
            {"qa_warnings": ["unverified number '97%'"], "steps": [
                {"step": 1, "body": " ".join(["word"] * 80)},
                {"step": 2, "body": "see https://martechs.io for the gap map"},
                {"step": 3, "body": "no links here"},
                {"step": 4, "body": "bye"},
            ]},
            {"qa_warnings": [], "steps": [
                {"step": 1, "body": " ".join(["word"] * 100)},
                {"step": 2, "body": ""}, {"step": 3, "body": ""}, {"step": 4, "body": ""},
            ]},
        ],
    }
    stats = cli._deliverability_stats(msg_by_cik)
    assert stats == {"count": 2, "avg_step1_words": 90.0, "link_count": 1, "warned_count": 1}


def test_deliverability_stats_empty_input():
    stats = cli._deliverability_stats({})
    assert stats == {"count": 0, "avg_step1_words": 0, "link_count": 0, "warned_count": 0}


def test_deliverability_checklist_md_includes_static_guidance_and_computed_stats():
    md = cli._deliverability_checklist_md(
        {"count": 2, "avg_step1_words": 90, "link_count": 1, "warned_count": 1})
    for term in ("SPF", "DKIM", "DMARC", "Stop-on-reply", "Link tracking", "Open tracking",
                 "10-20 minutes"):
        assert term in md
    assert "Drafts exported: 2" in md
    assert "Avg step-1 word count: 90" in md
    assert "Links found across all steps: 1" in md
    assert "Drafts with at least one QA warning: 1" in md


def test_export_messages_writes_deliverability_checklist_file(tmp_path, monkeypatch):
    from pipeline import db as db_mod

    company = {"cik": 1, "ticker": "TST", "name": "Test Co", "sector_bucket": "saas",
               "market_cap": 1e8, "status": "contacts_found", "profile": "adopter"}
    contact = {"id": 1, "name": "Anne Smith", "title": "CMO", "role_bucket": "CMO",
               "email": None, "linkedin_url": None}
    message_row = {
        "ticker": "TST", "contact_name": "Anne Smith", "contact_title": "CMO",
        "contact_id": 1, "archetype": "observation", "service": "ai_outreach",
        "angle_family": "funding", "angle_fingerprint": "fp1", "qa_warnings": [],
        "status": "draft", "created_at": "2026-01-01",
        "steps": [
            {"step": 1, "day_offset": 0, "subject": "test co outbound",
             "body": " ".join(["word"] * 80), "cta_type": "confirm_problem"},
            {"step": 2, "day_offset": 3, "subject": None,
             "body": "see https://martechs.io", "cta_type": "offer_deliverable"},
            {"step": 3, "day_offset": 8, "subject": None, "body": "hi there", "cta_type": "micro_commitment"},
            {"step": 4, "day_offset": 16, "subject": None, "body": "bye", "cta_type": "breakup_options"},
        ],
    }
    monkeypatch.setattr(db_mod, "get_companies",
                        lambda status=None, **kw: [dict(company)] if status in (None, "contacts_found") else [])
    monkeypatch.setattr(db_mod, "latest_score",
                        lambda cik: {"total": 70, "service_fit": [{"service": "ai_outreach"}]})
    monkeypatch.setattr(db_mod, "get_angles", lambda cik: [])
    monkeypatch.setattr(db_mod, "get_contacts", lambda cik: [dict(contact)])
    monkeypatch.setattr(db_mod, "all_messages", lambda: {1: [dict(message_row)]})

    out = tmp_path / "qualified.csv"
    cli.export(out=out, messages=True)

    checklist = tmp_path / "deliverability_checklist.md"
    assert checklist.exists()
    text = checklist.read_text()
    assert "SPF" in text and "DKIM" in text
    assert "Drafts exported: 1" in text
    assert "Avg step-1 word count: 80" in text
    assert "Links found across all steps: 1" in text
    assert "Drafts with at least one QA warning: 0" in text
