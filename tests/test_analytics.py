"""analytics.py: outcome-analytics math (pure, fixture-driven) plus the
render() entry point cli.status(--analytics) calls. render() is exercised
with a FakeDB (monkeypatched module attribute, same pattern as
test_scoring.py) so these tests never touch Supabase."""
from datetime import datetime, timedelta, timezone

import pytest

from pipeline import analytics


# ---------- funnel_conversion: company-status snapshot ratios ----------

def test_funnel_conversion_computes_ratio_vs_previous_stage():
    counts = {"new": 100, "enriched": 40, "scored": 20, "qualified": 5,
              "disqualified": 10, "contacts_found": 3}
    rows = analytics.funnel_conversion(counts, analytics.STATUS_ORDER)
    by_status = {r["status"]: r for r in rows}
    assert by_status["new"]["count"] == 100
    assert by_status["new"]["rate_vs_prev"] is None  # nothing precedes the first stage
    assert by_status["enriched"]["rate_vs_prev"] == pytest.approx(0.4)
    assert by_status["scored"]["rate_vs_prev"] == pytest.approx(0.5)


def test_funnel_conversion_guards_division_by_zero():
    counts = {"new": 0, "enriched": 5}
    rows = analytics.funnel_conversion(counts, ["new", "enriched"])
    assert rows[1]["rate_vs_prev"] == 0.0  # prev stage empty -> 0, never a crash


def test_funnel_conversion_missing_status_defaults_to_zero():
    rows = analytics.funnel_conversion({}, ["new", "enriched"])
    assert [r["count"] for r in rows] == [0, 0]


# ---------- avg_time_in_stage ----------

def test_avg_time_in_stage_computes_days_since_status_changed_at():
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    companies = [
        {"status": "scored", "status_changed_at": (now - timedelta(days=10)).isoformat()},
        {"status": "scored", "status_changed_at": (now - timedelta(days=20)).isoformat()},
        {"status": "qualified", "status_changed_at": (now - timedelta(days=5)).isoformat()},
    ]
    result = analytics.avg_time_in_stage(companies, now=now)
    assert result["scored"] == {"n": 2, "avg_days": pytest.approx(15.0)}
    assert result["qualified"] == {"n": 1, "avg_days": pytest.approx(5.0)}


def test_avg_time_in_stage_skips_rows_with_no_status_changed_at():
    companies = [{"status": "new", "status_changed_at": None}]
    result = analytics.avg_time_in_stage(companies, now=datetime.now(timezone.utc))
    assert result == {}


def test_avg_time_in_stage_empty_input():
    assert analytics.avg_time_in_stage([]) == {}


def test_avg_time_in_stage_tolerates_z_suffix_timestamps():
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    companies = [{"status": "new", "status_changed_at": "2026-07-08T00:00:00Z"}]
    result = analytics.avg_time_in_stage(companies, now=now)
    assert result["new"]["avg_days"] == pytest.approx(1.0)


# ---------- benchmark_band ----------

def test_benchmark_band_classifies_rate_into_configured_bands():
    bands = {"poor": 0.01, "avg": 0.03, "good": 0.08}
    assert analytics.benchmark_band(0.10, bands) == "good"
    assert analytics.benchmark_band(0.08, bands) == "good"
    assert analytics.benchmark_band(0.05, bands) == "avg"
    assert analytics.benchmark_band(0.03, bands) == "avg"
    assert analytics.benchmark_band(0.02, bands) == "poor"
    assert analytics.benchmark_band(0.01, bands) == "poor"
    assert analytics.benchmark_band(0.0, bands) == "below poor"


# ---------- outcome_funnel: sent -> replied -> positive_reply -> meeting ----------

def _events(pairs):
    """pairs: list of (message_id, event) -> message_events-shaped dicts."""
    return [{"message_id": mid, "event": ev} for mid, ev in pairs]


def test_outcome_funnel_insufficient_data_below_min_sends():
    events = _events([(1, "sent"), (2, "sent")])
    result = analytics.outcome_funnel(events, min_sends=10)
    assert result["insufficient"] is True
    assert result["n_sent"] == 2
    assert result["min_sends"] == 10


def test_outcome_funnel_computes_rates_once_min_sends_met():
    events = _events([
        (1, "sent"), (2, "sent"), (3, "sent"), (4, "sent"),
        (1, "replied"), (2, "replied"),
        (1, "positive_reply"),
        (1, "meeting"),
    ])
    result = analytics.outcome_funnel(events, min_sends=4)
    assert result["insufficient"] is False
    assert result["n_sent"] == 4
    assert result["n_replied"] == 2
    assert result["n_positive_reply"] == 1
    assert result["n_meeting"] == 1
    assert result["reply_rate"] == pytest.approx(0.5)
    assert result["positive_reply_rate"] == pytest.approx(0.25)
    assert result["meeting_rate"] == pytest.approx(0.25)


def test_outcome_funnel_counts_each_message_once_per_event_type():
    # duplicate 'sent' events for the same message (e.g. re-imported CSV row)
    # must not double-count.
    events = _events([(1, "sent"), (1, "sent"), (2, "sent")])
    result = analytics.outcome_funnel(events, min_sends=1)
    assert result["n_sent"] == 2


def test_outcome_funnel_zero_events_is_insufficient_not_a_crash():
    result = analytics.outcome_funnel([], min_sends=10)
    assert result == {"insufficient": True, "min_sends": 10, "n_sent": 0}


# ---------- attribution_table: group messages by a dimension ----------

def test_attribution_table_groups_by_dimension_and_guards_small_groups():
    messages = [
        {"id": 1, "archetype": "observation"},
        {"id": 2, "archetype": "observation"},
        {"id": 3, "archetype": "whole_offer"},
    ]
    events = _events([
        (1, "sent"), (2, "sent"), (1, "replied"),
        (3, "sent"),
    ])
    result = analytics.attribution_table(messages, events, "archetype", min_sends=2)
    assert result["observation"]["insufficient"] is False
    assert result["observation"]["n_sent"] == 2
    assert result["observation"]["n_replied"] == 1
    assert result["whole_offer"]["insufficient"] is True  # only 1 sent, below min_sends=2


def test_attribution_table_missing_dimension_value_groups_as_unknown():
    messages = [{"id": 1, "service": None}]
    events = _events([(1, "sent")])
    result = analytics.attribution_table(messages, events, "service", min_sends=1)
    assert "unknown" in result
    assert result["unknown"]["n_sent"] == 1


def test_attribution_table_empty_input():
    assert analytics.attribution_table([], [], "archetype", min_sends=1) == {}


# ---------- render(): CLI entry point, degrade-pattern smoke tests ----------

class FakeDBEmpty:
    """Schema applied, zero data everywhere — must render cleanly."""

    def get_companies(self, status=None, tickers=None, limit=None):
        return []

    def all_messages(self):
        return {}

    def all_message_events(self):
        return []


class FakeDBUnmigrated:
    """Mirrors cli.status()'s tier_counts() degrade path: message_events
    doesn't exist yet -> PGRST205-style error."""

    def get_companies(self, status=None, tickers=None, limit=None):
        return []

    def all_messages(self):
        return {}

    def all_message_events(self):
        raise Exception("relation \"public.message_events\" does not exist (PGRST205)")


def test_render_on_empty_db_does_not_crash(monkeypatch, capsys):
    from rich.console import Console

    monkeypatch.setattr(analytics, "db", FakeDBEmpty())
    analytics.render(Console())
    out = capsys.readouterr().out
    assert "insufficient data" in out.lower()


def test_render_on_unmigrated_db_degrades_gracefully(monkeypatch, capsys):
    from rich.console import Console

    monkeypatch.setattr(analytics, "db", FakeDBUnmigrated())
    analytics.render(Console())
    out = capsys.readouterr().out
    assert "apply-schema" in out or "schema" in out.lower()


class FakeDBCompaniesNoTimestamp:
    """Companies exist but predate the status_changed_at column (select *
    just omits it — no error, unlike a missing table). Must not be reported
    as "no companies"."""

    def get_companies(self, status=None, tickers=None, limit=None):
        return [{"status": "scored"}, {"status": "disqualified"}]

    def all_messages(self):
        return {}

    def all_message_events(self):
        return []


def test_render_distinguishes_no_companies_from_missing_status_changed_at(monkeypatch, capsys):
    from rich.console import Console

    monkeypatch.setattr(analytics, "db", FakeDBCompaniesNoTimestamp())
    analytics.render(Console())
    out = capsys.readouterr().out
    assert "not populated yet" in out
    assert "no companies" not in out
