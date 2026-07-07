import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from pipeline import messages
from pipeline.models import MessageSequence

FP = "funding:0001234-26-000042"
COMPANY = {
    "cik": 1, "ticker": "TST", "name": "Test Co", "exchange": "Nasdaq",
    "sector_bucket": "saas", "market_cap": 1e8, "sic_description": "software",
    "website": None, "hq_state": "CA", "status": "contacts_found",
}
ANGLE_ROW = {
    "id": 3, "fingerprint": FP, "family": "funding",
    "headline": "Offering priced — 424B5", "details": {"instrument": "follow_on"},
    "event_date": (date.today() - timedelta(days=20)).isoformat(),
    "strength": 1.0, "evidence_url": "https://sec.gov/x", "evidence_quote": "proceeds",
    "company_cik": 1, "source": "edgar", "status": "active",
}
STALE_ANGLE_ROW = {**ANGLE_ROW, "id": 7, "fingerprint": "funding:old",
                   "event_date": (date.today() - timedelta(days=400)).isoformat()}
CONTACT_CMO = {"id": 11, "name": "Anne Smith", "title": "Chief Marketing Officer",
               "role_bucket": "CMO", "linkedin_url": "https://linkedin.com/in/anne", "email": None}
CONTACT_CEO = {"id": 12, "name": "Bob Roy", "title": "CEO", "role_bucket": "CEO",
               "linkedin_url": None, "email": "bob@test.co"}
SCORE = {
    "profile": "adopter", "why_now": "fresh raise", "reasoning": "cited",
    "service_fit": [
        {"service": "ai_outreach", "priority": 1, "rationale": "r"},
        {"service": "ai_marketing", "priority": 2, "rationale": "r"},
        {"service": "ai_consultation", "priority": 3, "rationale": "r"},
    ],
    "primary_angle": {"fingerprint": FP, "family": "funding", "why_this_angle": "w"},
    "angle_ranking": [{"fingerprint": FP, "family": "funding", "message_hook": "h"}],
}


class FakeDB:
    def __init__(self):
        self.upserted = []
        self.stale_marked = []
        self.messages_rows: dict[int, list[dict]] = {}
        self.angles_rows = [dict(ANGLE_ROW)]
        self.contacts_rows = [dict(CONTACT_CMO), dict(CONTACT_CEO)]
        self.score_row = dict(SCORE)

    def get_companies(self, status=None, **kw):
        return [dict(COMPANY)] if status == "contacts_found" else []

    def get_company_by_ticker(self, ticker):
        return dict(COMPANY) if ticker == "TST" else None

    def latest_score(self, cik):
        return dict(self.score_row) if self.score_row else None

    def get_contacts(self, cik):
        return [dict(c) for c in self.contacts_rows]

    def all_angles(self):
        return {1: [dict(a) for a in self.angles_rows]}

    def all_messages(self):
        return {k: [dict(m) for m in v] for k, v in self.messages_rows.items()}

    def mark_angles_stale(self, ids):
        self.stale_marked.extend(ids)
        return len(ids)

    def upsert_message(self, row):
        self.upserted.append(row)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    q, r, a = tmp_path / "queue", tmp_path / "results", tmp_path / "archive"
    for d in (q, r, a):
        d.mkdir()
    monkeypatch.setattr(messages, "MSG_QUEUE_DIR", q)
    monkeypatch.setattr(messages, "MSG_RESULTS_DIR", r)
    monkeypatch.setattr(messages, "MSG_ARCHIVE_DIR", a)
    monkeypatch.setattr(messages, "db", FakeDB())
    return q, r, a


# copy that passes every deterministic QA gate (counted by hand)
BODY1 = (
    "Saw the follow-on Test Co priced in March — that kind of raise usually "
    "comes with a mandate to show progress fast.\n\n"
    "Most micro-cap software teams at your stage still run outbound by hand. "
    "Your board heard the plan. Your team feels the gap between the "
    "announcement and what ships.\n\n"
    "The window the raise opened narrows every month it sits unstaffed.\n\n"
    "Are you seeing this too?"
)
BODY2 = (
    "Put together a two-page gap map of where an outbound system would slot in "
    "at Test Co — built from your own filings, not a template.\n\n"
    "Worth sending over? No pitch attached.\n\nUday"
)
BODY3 = (
    "One thing stands out from your filings: the ambition is public but no "
    "owner is named.\n\nHow is that getting staffed internally?\n\nUday"
)
BODY4 = (
    "Not trying to be a pest — checking one last time.\n\n"
    "1. All set — not something you need help with\n"
    "2. Timing's off — circle back in a few months\n"
    "3. Wrong person — point me to who owns this?\n\n"
    "No worries either way.\n\nUday"
)


def make_seq(**overrides) -> dict:
    seq = {
        "ticker": "TST", "contact_name": "Anne Smith",
        "contact_title": "Chief Marketing Officer",
        "archetype": "observation", "angle_fingerprint": FP,
        "angle_family": "funding", "service": "ai_outreach",
        "steps": [
            {"step": 1, "subject": "test co + outbound", "body": BODY1, "cta_type": "confirm_problem"},
            {"step": 2, "subject": None, "body": BODY2, "cta_type": "offer_deliverable"},
            {"step": 3, "subject": None, "body": BODY3, "cta_type": "micro_commitment"},
            {"step": 4, "subject": None, "body": BODY4, "cta_type": "breakup_options"},
        ],
    }
    seq.update(overrides)
    return seq


def seq_with_step(step_no: int, **step_overrides) -> dict:
    seq = make_seq()
    seq["steps"][step_no - 1] = {**seq["steps"][step_no - 1], **step_overrides}
    return seq


def anne_packet(q) -> dict:
    return json.loads((q / "TST__anne-smith.json").read_text())


# ---------- model validators ----------

def test_sequence_requires_four_ordered_steps():
    seq = make_seq()
    seq["steps"] = seq["steps"][:3]
    with pytest.raises(ValidationError):
        MessageSequence.model_validate(seq)
    seq = make_seq()
    seq["steps"][1], seq["steps"][2] = seq["steps"][2], seq["steps"][1]
    with pytest.raises(ValidationError, match="numbered 1-4 in order"):
        MessageSequence.model_validate(seq)


def test_subject_on_step_one_only():
    with pytest.raises(ValidationError, match="must not have a subject"):
        MessageSequence.model_validate(seq_with_step(2, subject="hello there friend"))
    with pytest.raises(ValidationError, match="step 1 must have a subject"):
        MessageSequence.model_validate(seq_with_step(1, subject=None))


# ---------- qa_check ----------

def _qa(seq_dict, packet):
    seq = MessageSequence.model_validate(seq_dict)
    return messages.qa_check(seq, packet)


@pytest.fixture
def packet(dirs):
    q, r, a = dirs
    messages.prepare()
    return anne_packet(q)


def test_clean_sequence_passes_qa(packet):
    hard, warn = _qa(make_seq(), packet)
    assert hard == []
    assert warn == []


@pytest.mark.parametrize("seq_dict, needle", [
    (make_seq(archetype="case_study"), "not allowed"),
    (make_seq(angle_fingerprint="made:up"), "not in packet angles"),
    (make_seq(ticker="XXX"), "packet ticker"),
    (make_seq(contact_name="Wrong Person"), "packet contact"),
    (make_seq(service="ai_lead_generation"), "not in packet service_fit"),
    (seq_with_step(1, subject="Test Co + Outbound"), "lowercase"),
    (seq_with_step(1, subject="hi there"), "words (need 3-5)"),
    (seq_with_step(1, subject="one two three four five six"), "words (need 3-5)"),
    (seq_with_step(1, subject="test co + outbound!"), "special characters"),
    (seq_with_step(1, subject="re: test co outbound"), "re:/fwd:"),
    (seq_with_step(2, body=" ".join(["word"] * 151) + "?"), "hard max"),
    (seq_with_step(1, body=BODY1.replace("?", ".")), "no question CTA"),
    (seq_with_step(1, body=BODY1 + " See https://martechs.io now."), "contains a link"),
    (seq_with_step(2, body=BODY2.replace("Uday", "{{firstName}}")), "merge variables"),
    (seq_with_step(2, body=BODY2.replace("Uday", "[Your name]")), "merge variables"),
    (seq_with_step(3, body=BODY3.replace("staffed", "streamlined")), "banned word 'streamline'"),
])
def test_hard_qa_failures(packet, seq_dict, needle):
    hard, _ = _qa(seq_dict, packet)
    assert any(needle in h for h in hard), hard


def test_unverified_number_warns_but_packet_number_does_not(packet):
    # "42" appears in the packet (angle fingerprint); "97" appears nowhere
    hard, warn = _qa(seq_with_step(3, body=BODY3 + "\n\nPeers cut 97% of it."), packet)
    assert hard == []
    assert any("unverified number '97%'" in w for w in warn)
    _, warn2 = _qa(seq_with_step(3, body=BODY3 + "\n\nYour 42 filing shows it."), packet)
    assert not any("unverified" in w for w in warn2)


def test_meeting_ask_before_step_four_warns(packet):
    _, warn = _qa(seq_with_step(2, body=BODY2.replace("Worth sending over?",
                                                      "Want to hop on a call?")), packet)
    assert any("meeting ask" in w for w in warn)


def test_step_four_cta_and_word_count_warnings(packet):
    _, warn = _qa(seq_with_step(4, cta_type="micro_commitment"), packet)
    assert any("breakup_options" in w for w in warn)
    _, warn = _qa(seq_with_step(1, body="Too short but has a question? " + " ".join(["pad"] * 10)), packet)
    assert any("step 1 body has" in w for w in warn)


# ---------- prepare ----------

def test_prepare_writes_one_packet_per_contact(dirs):
    q, r, a = dirs
    written, skips = messages.prepare()
    assert [p.split("/")[-1] for p in written] == ["TST__anne-smith.json", "TST__bob-roy.json"]
    assert not any(skips.values())

    shared = json.loads((q / "_shared.json").read_text())
    assert set(shared) == {"copywriter_framework", "services_catalog", "output_schema",
                           "sequence_plan", "hard_rules", "instructions"}
    assert "SPARK" in shared["copywriter_framework"]

    packet = anne_packet(q)
    assert packet["contact"]["name"] == "Anne Smith"
    assert packet["contact"]["has_email"] is False and packet["contact"]["has_linkedin"] is True
    assert packet["colleagues_also_messaged"] == [{"name": "Bob Roy", "title": "CEO"}]
    assert packet["primary_angle_fingerprint"] == FP
    assert packet["angles"][0]["age_days"] == 20
    assert "Anne Smith" in packet["instructions"] and "CMO" in packet["instructions"]
    assert packet["output_path"] == (r / "TST__anne-smith.json").as_posix()


def test_prepare_picks_service_by_contact_role(dirs):
    q, r, a = dirs
    messages.prepare()
    assert anne_packet(q)["recommended_service"] == "ai_outreach"       # CMO in ai_outreach roles
    bob = json.loads((q / "TST__bob-roy.json").read_text())
    assert bob["recommended_service"] == "ai_consultation"              # CEO only in ai_consultation roles


def test_prepare_skips_company_without_fresh_angle(dirs):
    q, r, a = dirs
    messages.db.angles_rows = [dict(STALE_ANGLE_ROW)]
    written, skips = messages.prepare()
    assert written == []
    assert skips["no_angle"] == ["TST"]
    assert messages.db.stale_marked == [STALE_ANGLE_ROW["id"]]


def test_prepare_falls_back_when_primary_angle_stale(dirs):
    q, r, a = dirs
    messages.db.angles_rows = [dict(STALE_ANGLE_ROW), dict(ANGLE_ROW)]
    messages.db.score_row = {
        **SCORE,
        "primary_angle": {"fingerprint": "funding:old", "family": "funding", "why_this_angle": "w"},
        "angle_ranking": [{"fingerprint": "funding:old", "family": "funding", "message_hook": "h"},
                          {"fingerprint": FP, "family": "funding", "message_hook": "h2"}],
    }
    written, skips = messages.prepare()
    assert written and not skips["no_angle"]
    assert anne_packet(q)["primary_angle_fingerprint"] == FP


def test_prepare_skips_existing_pair_unless_forced(dirs):
    q, r, a = dirs
    messages.db.messages_rows = {1: [{"contact_id": 11, "angle_fingerprint": FP}]}
    written, skips = messages.prepare()
    assert [p.split("/")[-1] for p in written] == ["TST__bob-roy.json"]
    assert skips["existing"] == ["TST__anne-smith"]
    written, _ = messages.prepare(force=True)
    assert len(written) == 2


def test_prepare_respects_limit(dirs):
    written, _ = messages.prepare(limit=1)
    assert len(written) == 1


def test_prepare_dry_run_writes_nothing(dirs):
    q, r, a = dirs
    written, _ = messages.prepare(dry_run=True)
    assert len(written) == 2
    assert list(q.iterdir()) == []
    assert messages.db.stale_marked == []


# ---------- commit ----------

def _write_result(r, name, seq_dict, day_offset=99):
    for s in seq_dict["steps"]:
        s["day_offset"] = day_offset  # LLM value must be ignored
    (r / name).write_text(json.dumps(seq_dict))


def test_commit_valid_sequence_upserts_and_archives(dirs):
    q, r, a = dirs
    messages.prepare(limit=1)
    _write_result(r, "TST__anne-smith.json", make_seq())

    summary = messages.commit(run_id="testrun")

    assert summary["invalid"] == [] and summary["orphan"] == []
    assert summary["written"] == [{"ticker": "TST", "contact": "Anne Smith",
                                   "archetype": "observation", "service": "ai_outreach"}]
    row = messages.db.upserted[0]
    assert row["company_cik"] == 1 and row["contact_id"] == 11
    assert row["contact_name"] == "Anne Smith" and row["status"] == "draft"
    assert [s["day_offset"] for s in row["steps"]] == [0, 3, 8, 16]  # stamped, not the LLM's 99
    run_dir = a / "testrun"
    assert (run_dir / "TST__anne-smith.json").exists()
    assert (run_dir / "packet_TST__anne-smith.json").exists()
    assert (run_dir / "_shared.json").exists()
    assert not (q / "_shared.json").exists()  # queue drained -> shared removed


def test_commit_hard_qa_failure_leaves_files_for_respawn(dirs):
    q, r, a = dirs
    messages.prepare(limit=1)
    bad = seq_with_step(3, body=BODY3.replace("staffed", "streamlined"))
    _write_result(r, "TST__anne-smith.json", bad)

    summary = messages.commit(run_id="t2")

    assert messages.db.upserted == []
    assert len(summary["invalid"]) == 1 and "banned word" in summary["invalid"][0]
    assert (r / "TST__anne-smith.json").exists()      # result stays for re-spawn
    assert (q / "TST__anne-smith.json").exists()      # packet stays queued
    assert (q / "_shared.json").exists()              # queue not drained


def test_commit_invalid_json_and_orphans(dirs):
    q, r, a = dirs
    messages.prepare(limit=1)
    (r / "TST__anne-smith.json").write_text("not json")
    (r / "TST__ghost.json").write_text(json.dumps(make_seq()))

    summary = messages.commit(run_id="t3")

    assert any("TST__anne-smith" in i for i in summary["invalid"])
    assert summary["orphan"] == ["TST__ghost"]


def test_commit_warnings_ride_on_row(dirs):
    q, r, a = dirs
    messages.prepare(limit=1)
    _write_result(r, "TST__anne-smith.json",
                  seq_with_step(3, body=BODY3 + "\n\nPeers cut 97% of it."))

    summary = messages.commit(run_id="t4")

    assert summary["invalid"] == []
    warns = messages.db.upserted[0]["qa_warnings"]
    assert any("unverified number" in w for w in warns)
    assert "TST__anne-smith" in summary["warnings"]
