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
