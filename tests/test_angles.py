from datetime import date

import pytest
from pydantic import ValidationError

from pipeline.models import Angle, ScoreVerdict


def make_angle(**overrides):
    base = dict(
        company_cik=999, family="funding", headline="Offering priced ~$12M — 424B5 filed 2026-05-01",
        details={"amount_usd": 12_000_000, "instrument": "follow_on", "filing_type": "424B5"},
        evidence_url="https://www.sec.gov/x", evidence_quote="use the net proceeds for growth",
        event_date=date(2026, 5, 1), source="edgar", strength=1.0,
        fingerprint="funding:0001234-26-000042",
    )
    base.update(overrides)
    return Angle(**base)


def test_angle_validates_funding_details():
    a = make_angle()
    assert a.details["instrument"] == "follow_on"
    assert a.details["amount_usd"] == 12_000_000


def test_angle_rejects_bad_instrument():
    with pytest.raises(ValidationError):
        make_angle(details={"instrument": "ico"})


def test_leadership_details_require_role():
    with pytest.raises(ValidationError):
        make_angle(family="leadership", details={"person_name": "Jane Roe"})
    a = make_angle(family="leadership", details={"role": "CRO", "start_date": "2026-04-01"})
    assert a.details["role"] == "CRO"


def test_ai_move_details_require_initiative():
    with pytest.raises(ValidationError):
        make_angle(family="ai_move", details={"partner": "Google"})


def test_score_verdict_backward_compatible_without_angle_fields():
    v = ScoreVerdict(
        ticker="TST", intent=10, capability_gap=10, timing=10, commercial_fit=10,
        profile="laggard", service_fit=[], reasoning="r", why_now="w",
    )
    assert v.angle_ranking == []
    assert v.primary_angle is None


def test_score_verdict_accepts_angle_fields():
    v = ScoreVerdict(
        ticker="TST", intent=10, capability_gap=10, timing=10, commercial_fit=10,
        profile="adopter", service_fit=[], reasoning="r", why_now="w",
        angle_ranking=[{"fingerprint": "f1", "family": "funding", "message_hook": "hook"}],
        primary_angle={"fingerprint": "f1", "family": "funding", "why_this_angle": "freshest"},
    )
    assert v.primary_angle.fingerprint == "f1"


# Task 3: angle logic tests

from datetime import timedelta

from pipeline import angles


def test_fresh_within_window():
    assert angles.is_fresh("funding", date.today() - timedelta(days=100))
    assert angles.is_fresh("ai_move", (date.today() - timedelta(days=100)).isoformat())


def test_stale_beyond_window():
    assert not angles.is_fresh("funding", date.today() - timedelta(days=400))
    assert not angles.is_fresh("ai_move", date.today() - timedelta(days=300))  # 270d window


def test_strength_full_when_recent_with_full_evidence():
    d = date.today() - timedelta(days=30)
    assert angles.compute_strength("funding", d, has_quote=True, has_url=True) == 1.0


def test_strength_decays_to_floor_at_window_edge():
    d = date.today() - timedelta(days=365)
    s = angles.compute_strength("funding", d, has_quote=True, has_url=True)
    assert abs(s - 0.25) < 0.01


def test_strength_zero_when_stale():
    d = date.today() - timedelta(days=400)
    assert angles.compute_strength("funding", d, has_quote=True, has_url=True) == 0.0


def test_strength_evidence_quality_tiers():
    d = date.today() - timedelta(days=10)
    assert angles.compute_strength("funding", d, has_quote=False, has_url=True) == 0.7
    assert angles.compute_strength("funding", d, has_quote=False, has_url=False) == 0.4


def test_fingerprint_prefixed_and_stable():
    fp = angles.make_fingerprint("leadership", "CRO", "2026-04-01")
    assert fp == "leadership:cro:2026-04-01"
    assert fp == angles.make_fingerprint("leadership", "CRO", "2026-04-01")


def test_slim_includes_age_days():
    row = make_angle().model_dump(mode="json")
    s = angles.slim(row)
    assert set(s) == {"fingerprint", "family", "headline", "details", "event_date",
                      "strength", "evidence_url", "evidence_quote", "age_days"}
    assert isinstance(s["age_days"], int)


def test_select_deep_targets_orders_by_total_and_caps():
    cands = [{"cik": 1, "ticker": "A"}, {"cik": 2, "ticker": "B"}, {"cik": 3, "ticker": "C"}]
    totals = {1: 50, 2: 70, 3: 60}
    picked = angles.select_deep_targets(cands, totals, cap=2)
    assert [c["ticker"] for c in picked] == ["B", "C"]
