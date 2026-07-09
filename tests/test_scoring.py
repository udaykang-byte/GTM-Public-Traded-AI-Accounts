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

    def set_status(self, cik, status, profile=None, tier=None):
        self.statuses.append((cik, str(status), profile, tier))

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
    # pre-gate off by default so these tests exercise the LLM scoring path
    # regardless of live settings.yaml; the pre-gate tests re-enable it
    monkeypatch.setitem(scoring.SETTINGS.setdefault("scoring", {}), "pre_gate", {"enabled": False})
    return q, r, a


def test_prepare_writes_shared_file_and_slim_packets(dirs):
    q, r, a = dirs
    written, gated = scoring.prepare()
    assert gated == []

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


# ---------- v3: stacking bonus (base score only, never the LLM verdict) ----------

def test_stacking_bonus_applies_when_min_components_hit():
    signals = [{"type": "E1"}, {"type": "E3"}, {"type": "E5"}]  # intent/timing/commercial_fit
    assert scoring.stacking_bonus(signals, cfg={"min_components": 3, "bonus": 5}) == 5.0


def test_stacking_bonus_zero_below_min_components():
    signals = [{"type": "E1"}, {"type": "E2"}]  # both intent -> 1 distinct component
    assert scoring.stacking_bonus(signals, cfg={"min_components": 3, "bonus": 5}) == 0.0


def test_stacking_bonus_ignores_signal_types_with_no_component():
    signals = [{"type": "E1"}, {"type": "E3"}, {"type": "UNKNOWN"}]
    assert scoring.stacking_bonus(signals, cfg={"min_components": 3, "bonus": 5}) == 0.0


def test_base_components_adds_stacking_bonus_to_total(monkeypatch):
    monkeypatch.setitem(scoring.SETTINGS.setdefault("scoring", {}), "stacking",
                         {"min_components": 2, "bonus": 5})
    signals = [
        {"type": "E1", "weight": 10, "observed_at": None},
        {"type": "E3", "weight": 10, "observed_at": None},
    ]
    result = scoring.base_components(signals)
    assert result["stacking_bonus"] == 5.0
    assert result["total"] == 10 + 10 + 5


def test_base_components_no_bonus_below_threshold(monkeypatch):
    monkeypatch.setitem(scoring.SETTINGS.setdefault("scoring", {}), "stacking",
                         {"min_components": 3, "bonus": 5})
    signals = [{"type": "E1", "weight": 10, "observed_at": None}]
    result = scoring.base_components(signals)
    assert result["stacking_bonus"] == 0.0
    assert result["total"] == 10


# ---------- v3: urgency metadata (informational, optional, never in the math) ----------

def test_urgency_of_buckets():
    cfg = {"hot": 30, "warm": 90}
    assert scoring.urgency_of(10, cfg=cfg) == "hot"
    assert scoring.urgency_of(30, cfg=cfg) == "hot"
    assert scoring.urgency_of(31, cfg=cfg) == "warm"
    assert scoring.urgency_of(90, cfg=cfg) == "warm"
    assert scoring.urgency_of(91, cfg=cfg) == "cold"
    assert scoring.urgency_of(None, cfg=cfg) is None


def test_prepare_adds_urgency_to_signals(dirs):
    q, r, a = dirs
    scoring.prepare()
    packet = json.loads((q / "TST.json").read_text())
    assert packet["signals"][0]["urgency"] in ("hot", "warm", "cold")


def test_derived_cohort_signal_carries_urgency_key(dirs):
    """Packet uniformity: the synthetic E8 gets an urgency key like every
    other signal (undated -> None, serialized as null)."""
    q, r, a = dirs
    peers = [{**COMPANY, "cik": i, "ticker": f"P{i}"} for i in range(2, 8)]
    fake = scoring.db
    fake.get_companies = lambda status=None: (
        [dict(COMPANY)] if status == "enriched" else [dict(COMPANY), *peers]
    )
    # target company has no AI language (E6 only); every peer shows E1
    fake.all_signals = lambda: {
        1: [{**SIGNAL, "type": "E6", "observed_at": None}],
        **{i: [dict(SIGNAL)] for i in range(2, 8)},
    }
    scoring.prepare()
    packet = json.loads((q / "TST.json").read_text())
    e8 = [s for s in packet["signals"] if s["type"] == "E8"]
    assert e8, "derived cohort signal expected in packet"
    assert "urgency" in e8[0] and e8[0]["urgency"] is None


def test_commit_handles_signals_without_urgency_field(dirs):
    """Old queue packets predate the urgency field — commit must not require it."""
    q, r, a = dirs
    scoring.db.angles_rows = [dict(ANGLE_ROW)]
    scoring.prepare()
    packet_path = q / "TST.json"
    packet = json.loads(packet_path.read_text())
    for s in packet["signals"]:
        s.pop("urgency", None)
    packet_path.write_text(json.dumps(packet))
    (r / "TST.json").write_text(json.dumps(make_verdict()))
    summary = scoring.commit(run_id="t")
    assert summary["invalid"] == []


def test_commit_handles_packet_missing_stacking_bonus_key(dirs):
    """Old queue packets predate the stacking bonus — commit must not KeyError."""
    q, r, a = dirs
    scoring.db.angles_rows = [dict(ANGLE_ROW)]
    scoring.prepare()
    packet_path = q / "TST.json"
    packet = json.loads(packet_path.read_text())
    del packet["base_score"]["stacking_bonus"]
    packet_path.write_text(json.dumps(packet))
    (r / "TST.json").write_text(json.dumps(make_verdict()))
    summary = scoring.commit(run_id="t")
    assert summary["invalid"] == []
    assert scoring.db.scores[0]["priority"] is not None


# ---------- v3: tier_of (pure; T1 >= tiers.t1_min, T2 qualified below it,
# T3 review, T4 disqualified; 'kept' counts as qualified) ----------

@pytest.mark.parametrize("total,bucket,expected", [
    (44, "disqualified", "T4"),
    (45, "review", "T3"),
    (64, "review", "T3"),
    (65, "qualified", "T2"),
    (79, "qualified", "T2"),
    (80, "qualified", "T1"),
    (85, "kept", "T1"),
    (50, "kept", "T2"),
])
def test_tier_of_boundaries(total, bucket, expected):
    assert scoring.tier_of(total, bucket, cfg={"t1_min": 80}) == expected


def test_tier_of_defaults_t1_min_80():
    assert scoring.tier_of(80, "qualified") == "T1"
    assert scoring.tier_of(79, "qualified") == "T2"


# ---------- v3: priority_score (composite ordering key) ----------

def test_priority_score_weights():
    cfg = {"total_weight": 1.0, "stacking_weight": 1.0, "angle_strength_weight": 10.0}
    assert scoring.priority_score(70, 5, 0.8, cfg=cfg) == 70 + 5 + 8.0


def test_priority_score_zero_inputs():
    cfg = {"total_weight": 1.0, "stacking_weight": 1.0, "angle_strength_weight": 10.0}
    assert scoring.priority_score(0, 0, 0, cfg=cfg) == 0.0


# ---------- v3: commit stores tier + priority end-to-end ----------

@pytest.mark.parametrize("intent,capg,timing,comm,expected_bucket,expected_tier", [
    (9, 10, 15, 10, "disqualified", "T4"),   # total 44
    (10, 10, 15, 10, "review", "T3"),        # total 45
    (19, 15, 20, 10, "review", "T3"),        # total 64
    (20, 15, 20, 10, "qualified", "T2"),     # total 65
    (24, 20, 25, 10, "qualified", "T2"),     # total 79
    (25, 20, 25, 10, "qualified", "T1"),     # total 80
])
def test_commit_stores_tier_at_boundaries(dirs, intent, capg, timing, comm, expected_bucket, expected_tier):
    v = make_verdict(intent=intent, capability_gap=capg, timing=timing, commercial_fit=comm)
    summary = _run_commit(dirs, [dict(ANGLE_ROW)], v)
    assert any(i["ticker"] == "TST" for i in summary[expected_bucket])
    assert scoring.db.scores[-1]["tier"] == expected_tier
    assert scoring.db.statuses[-1][-1] == expected_tier


def test_commit_stores_priority_on_score_row(dirs):
    _run_commit(dirs, [dict(ANGLE_ROW)], make_verdict())
    assert isinstance(scoring.db.scores[-1]["priority"], (int, float))


# ---- deterministic pre-gate (v3.1: skip LLM when the verdict can't change the outcome) ----

PRE_GATE_ON = {"enabled": True, "max_llm_lift": 40}


def test_pre_gate_disabled_returns_none():
    assert scoring.pre_gate(0.0, has_hard=False, cfg={"enabled": False}) is None


def test_pre_gate_no_hard_signal_blocks_regardless_of_base():
    assert scoring.pre_gate(90.0, has_hard=False, cfg=PRE_GATE_ON) == "no_hard_signal"


def test_pre_gate_base_below_reach():
    # qualify_threshold 65, lift 40 -> gate fires below base 25
    assert scoring.pre_gate(24.9, has_hard=True, cfg=PRE_GATE_ON) == "base_below_reach"


def test_pre_gate_reachable_base_needs_llm():
    assert scoring.pre_gate(25.0, has_hard=True, cfg=PRE_GATE_ON) is None


def test_pregate_verdict_is_schema_valid_and_caps_components():
    from pipeline.models import ScoreVerdict
    base = {"intent": 99.0, "capability_gap": 3.4, "timing": 0.0,
            "commercial_fit": 12.6, "stacking_bonus": 5.0, "total": 115.0}
    v = ScoreVerdict.model_validate(scoring.pregate_verdict("TST", base, "no_hard_signal"))
    assert v.intent == 30 and v.capability_gap == 3 and v.commercial_fit == 13
    assert v.profile.value == "unclear" and v.service_fit == []


def test_prepare_pre_gates_low_base_and_writes_synthetic_result(dirs, monkeypatch):
    q, r, a = dirs
    monkeypatch.setitem(scoring.SETTINGS["scoring"], "pre_gate", PRE_GATE_ON)
    written, gated = scoring.prepare()  # TST base = 15 (one E1), hard signal present
    assert written == []
    assert gated == [{"ticker": "TST", "total": 15, "reason": "base_below_reach"}]
    assert (q / "TST.json").exists()  # packet still written for commit
    result = json.loads((r / "TST.json").read_text())
    assert result["pregate"] == "base_below_reach"


def test_commit_of_pregated_result_labels_model_and_disqualifies(dirs, monkeypatch):
    q, r, a = dirs
    monkeypatch.setitem(scoring.SETTINGS["scoring"], "pre_gate", PRE_GATE_ON)
    scoring.prepare()
    summary = scoring.commit(run_id="t")
    # TST synthetic total 15 < disqualify_below 45 -> disqualified, T4
    assert [x["ticker"] for x in summary["disqualified"]] == ["TST"]
    row = scoring.db.scores[-1]
    assert row["model"] == "deterministic/pre-gate"
    assert row["tier"] == "T4"


def test_commit_of_llm_result_keeps_model_label(dirs):
    _run_commit(dirs, [dict(ANGLE_ROW)], make_verdict())
    assert scoring.db.scores[-1]["model"] == "claude-code/haiku-subagent"
