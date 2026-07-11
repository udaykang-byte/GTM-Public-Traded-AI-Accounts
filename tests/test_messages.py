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
    "Congrats on the follow-on raise.\n\n"
    "The uncomfortable part starts now: your board wants it turned into "
    "growth, and hiring reps eats half of it before your first new meeting "
    "gets booked.\n\n"
    "From my experience, most software micro-caps in this spot put the money "
    "into headcount, then wait out six months of ramp.\n\n"
    "We build AI-run outbound instead: targeting, personalization, replies. "
    "Your raise shows up as pipeline, not payroll.\n\n"
    "Worth a look at how that maps to Test Co?"
)
BODY2 = (
    "Put together a two-page gap map of where an outbound system would slot in "
    "at Test Co, built from your own filings, not a template.\n\n"
    "Worth sending over? No pitch attached.\n\nUday"
)
BODY3 = (
    "One thing stands out from your filings: the ambition is public but no "
    "owner is named.\n\nHow is that getting staffed internally?\n\nUday"
)
BODY4 = (
    "Not trying to be a pest. Checking one last time.\n\n"
    "1. All set, not something you need help with\n"
    "2. Timing's off, circle back in a few months\n"
    "3. Wrong person, point me to who owns this?\n\n"
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
    (seq_with_step(2, body=BODY2.replace("your own filings", "your 8-K")), "cites a filing form"),
    (seq_with_step(2, body=BODY2.replace("a template", "the May 6th one")), "cites a calendar date"),
    (seq_with_step(2, body=BODY2.replace("a template", "the 2026-03-16 one")), "cites a calendar date"),
    (seq_with_step(2, body=BODY2.replace("your own filings", "your S-3")), "cites a filing form"),
    (seq_with_step(1, body=BODY1 + " Your shelf registration opens the door?"), "instrument language"),
    (seq_with_step(1, body=BODY1 + " Congrats on the PIPE?"), "instrument language"),
    (seq_with_step(2, body=BODY2.replace("a template", "a private placement recap")), "instrument language"),
    (seq_with_step(4, body=BODY4.replace("Uday", "Best")), "'Uday' sign-off"),
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


def test_month_cite_warns_but_bare_month_does_not(packet):
    hard, warn = _qa(seq_with_step(2, body=BODY2.replace("a template", "the one from November")), packet)
    assert hard == []
    assert any("event month" in w for w in warn)
    hard, warn2 = _qa(seq_with_step(2, body=BODY2.replace("a template", "a November planning template")), packet)
    assert not any("event month" in w for w in warn2)


def test_meeting_ask_before_step_four_warns(packet):
    _, warn = _qa(seq_with_step(2, body=BODY2.replace("Worth sending over?",
                                                      "Want to hop on a call?")), packet)
    assert any("meeting ask" in w for w in warn)


def test_step_four_cta_and_word_count_warnings(packet):
    _, warn = _qa(seq_with_step(4, cta_type="micro_commitment"), packet)
    assert any("breakup_options" in w for w in warn)
    _, warn = _qa(seq_with_step(1, body="Too short but has a question? " + " ".join(["pad"] * 10)), packet)
    assert any("step 1 body has" in w for w in warn)


def test_analyst_voice_warns(packet):
    hard, warn = _qa(seq_with_step(
        3, body=BODY3.replace("One thing", "That kind of hire usually means one thing")), packet)
    assert hard == []
    assert any("analyst voice" in w for w in warn)


def test_any_em_dash_hard_fails(packet):
    """Campaign-copywriting rule (2026-07-10): no em dashes in copy, ever —
    the single most reliable AI tell. Periods or commas instead."""
    for step in (1, 2, 3, 4):
        body = make_seq()["steps"][step - 1]["body"] + "\n\nOne — two?"
        hard, _ = _qa(seq_with_step(step, body=body), packet)
        assert any("em dash" in h for h in hard), (step, hard)


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
    assert packet["colleagues_also_messaged"] == [
        {"name": "Bob Roy", "title": "CEO", "role_bucket": "CEO"}]
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


def test_prepare_orders_companies_by_tier_priority_before_cap(dirs, monkeypatch):
    """A T1 company must be worked before a T2 company even when it's later
    in the raw DB order — see db.order_by_tier_priority."""
    q, r, a = dirs
    company_lo = {**COMPANY, "cik": 1, "ticker": "TST", "tier": "T2"}
    company_hi = {**COMPANY, "cik": 2, "ticker": "HI", "tier": "T1"}
    monkeypatch.setattr(messages.db, "get_companies",
                        lambda status=None, **kw: [dict(company_lo), dict(company_hi)])
    monkeypatch.setattr(messages.db, "get_contacts",
                        lambda cik: [dict(CONTACT_CEO)])
    monkeypatch.setattr(messages.db, "latest_score", lambda cik: dict(SCORE))
    monkeypatch.setattr(messages.db, "all_angles",
                        lambda: {1: [dict(ANGLE_ROW)], 2: [{**ANGLE_ROW, "company_cik": 2}]})

    written, _ = messages.prepare(limit=1)  # cap of 1 packet total across companies

    assert len(written) == 1
    assert "HI" in written[0]


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


# ---------- v3: personas ----------

def test_prepare_attaches_persona_block_for_cmo(dirs):
    q, r, a = dirs
    messages.prepare()
    persona = anne_packet(q)["persona"]
    assert persona is not None
    assert persona["committee_role"] == "budget owner"
    assert persona["seniority"] == "c-suite"
    assert len(persona["pains"]) == 3
    assert set(persona["language"]) == {"their_words", "avoid"}


CONTACT_UNRECOGNIZED = {"id": 21, "name": "Jamie Doe", "title": "Regional Facilities Manager",
                        "role_bucket": "", "linkedin_url": None, "email": "jamie@test.co"}


def test_prepare_persona_block_none_when_role_bucket_and_title_unrecognized(dirs):
    q, r, a = dirs
    messages.db.contacts_rows = [dict(CONTACT_UNRECOGNIZED)]
    messages.prepare()
    packet = json.loads((q / "TST__jamie-doe.json").read_text())
    assert packet["persona"] is None


def test_recommended_service_consults_personas_before_legacy_roles_by_service(monkeypatch):
    # sabotage roles_by_service so a fallback-to-legacy read finds nothing
    # and would return the LEAD service (ai_consultation). The persona maps
    # CMO -> ai_outreach, which is NOT the lead -- so ai_outreach can only
    # come out of the personas-first branch. Deleting or bypassing that
    # branch makes this fail.
    monkeypatch.setitem(messages.SETTINGS["people"], "roles_by_service", {})
    fits = [{"service": "ai_consultation", "priority": 1}, {"service": "ai_outreach", "priority": 2}]
    assert messages._recommended_service(fits, "CMO") == "ai_outreach"


def test_recommended_service_falls_back_to_legacy_when_personas_empty(monkeypatch):
    monkeypatch.setattr(messages, "PERSONAS", {})
    fits = [{"service": "ai_outreach", "priority": 1}, {"service": "ai_consultation", "priority": 2}]
    assert messages._recommended_service(fits, "CMO") == "ai_outreach"
    assert messages._recommended_service(fits, "CEO") == "ai_consultation"
    assert messages._recommended_service(fits, "") == "ai_outreach"  # lead service


def test_shared_json_instructs_persona_pains_as_raw_material_only(dirs):
    q, r, a = dirs
    messages.prepare()
    shared = json.loads((q / "_shared.json").read_text())
    assert any("persona" in rule.lower() and "do not invent" in rule.lower()
               for rule in shared["hard_rules"])


def test_commit_handles_packet_without_persona_key(dirs):
    """Old packets (written before this phase) never had a "persona" key —
    commit must not KeyError on them."""
    q, r, a = dirs
    messages.prepare(limit=1)
    packet_path = q / "TST__anne-smith.json"
    packet = json.loads(packet_path.read_text())
    del packet["persona"]
    packet_path.write_text(json.dumps(packet))
    _write_result(r, "TST__anne-smith.json", make_seq())

    summary = messages.commit(run_id="nopersona")

    assert summary["invalid"] == []
    assert len(summary["written"]) == 1


# ---------- v3: personalization heuristic (qa_check, warning only) ----------

def test_personalization_score_high_for_tailored_body(packet):
    assert messages._personalization_score(BODY1, packet) >= 3


def test_personalization_score_zero_for_generic_body(packet):
    generic = ("Hope you are doing well. Are you interested in learning more "
               "about our approach? " + " ".join(["word"] * 50))
    assert messages._personalization_score(generic, packet) == 0


def test_personalization_score_missing_persona_falls_back_to_angle_headline(packet):
    """No persona matched (packet["persona"] is None) -> the pain-led-opener
    check falls back to the primary angle's headline words instead of
    crashing or silently losing that point."""
    packet_no_persona = {**packet, "persona": None}
    headline = packet["angles"][0]["headline"]
    opener = f"{headline}. " + " ".join(["word"] * 40) + "?"
    assert messages._personalization_score(opener, packet_no_persona) >= 1


def test_qa_check_warns_below_personalization_min(packet):
    generic_seq = seq_with_step(1, body=(
        "Hope you are doing well. Are you interested in learning more about "
        "our approach? " + " ".join(["word"] * 50)))
    _, warn = _qa(generic_seq, packet)
    assert any(w.startswith("personalization ") for w in warn)


def test_qa_check_personalization_warning_never_a_hard_failure(packet):
    generic_seq = seq_with_step(1, body=(
        "Hope you are doing well. Are you interested in learning more about "
        "our approach? " + " ".join(["word"] * 50)))
    hard, _ = _qa(generic_seq, packet)
    assert hard == []


def test_qa_check_respects_personalization_min_setting(monkeypatch, packet):
    monkeypatch.setitem(messages.SETTINGS["messages"], "personalization_min", 0)
    generic_seq = seq_with_step(1, body=(
        "Hope you are doing well. Are you interested in learning more about "
        "our approach? " + " ".join(["word"] * 50)))
    _, warn = _qa(generic_seq, packet)
    assert not any(w.startswith("personalization ") for w in warn)


# ---------- v3: banned words single-sourced to settings.yaml ----------

def test_banned_words_reads_from_settings():
    assert messages._banned_words() == messages.SETTINGS["messages"]["banned_words"]


def test_banned_words_falls_back_to_default_when_settings_key_absent(monkeypatch, packet):
    monkeypatch.delitem(messages.SETTINGS["messages"], "banned_words")
    assert messages._banned_words() == messages._DEFAULT_BANNED_WORDS
    hard, _ = _qa(seq_with_step(3, body=BODY3.replace("staffed", "streamlined")), packet)
    assert any("banned word 'streamline'" in h for h in hard)


def test_banned_words_falls_back_to_default_when_settings_key_is_null(monkeypatch, packet):
    """A pack with a bare `banned_words:` key (YAML null) must fall back to
    the default list, same as an absent key — it must NOT be treated as an
    explicit empty list (that would silently disable the QA gate)."""
    monkeypatch.setitem(messages.SETTINGS["messages"], "banned_words", None)
    assert messages._banned_words() == messages._DEFAULT_BANNED_WORDS
    hard, _ = _qa(seq_with_step(3, body=BODY3.replace("staffed", "streamlined")), packet)
    assert any("banned word 'streamline'" in h for h in hard)


def test_banned_words_explicit_empty_list_disables_gate_not_fallback(monkeypatch, packet):
    """An explicit `banned_words: []` in a pack is a deliberate decision to
    disable the gate — it must NOT fall back to the default list (fallback is
    for the ABSENT key only)."""
    monkeypatch.setitem(messages.SETTINGS["messages"], "banned_words", [])
    assert messages._banned_words() == []
    hard, _ = _qa(seq_with_step(3, body=BODY3.replace("staffed", "streamlined")), packet)
    assert not any("banned word" in h for h in hard)


def test_banned_words_honors_settings_override(monkeypatch, packet):
    monkeypatch.setitem(messages.SETTINGS["messages"], "banned_words", ["bespoke"])
    hard, _ = _qa(seq_with_step(3, body=BODY3 + " We offer a bespoke approach."), packet)
    assert any("banned word 'bespoke'" in h for h in hard)
    # a word from the ORIGINAL default list is no longer banned once overridden
    hard2, _ = _qa(seq_with_step(3, body=BODY3.replace("staffed", "streamlined")), packet)
    assert not any("banned word 'streamline'" in h for h in hard2)


# --- packet slimming: analyst-voice reasoning dropped, service_fit collapsed ---

def test_prepare_packet_verdict_is_slim(dirs):
    """verdict.reasoning is analyst voice the copywriter is forbidden to use
    (filing forms, dates, instrument names) — dead weight that seeds QA
    retries. service_fit collapses to the recommended service's entry so the
    packet carries one rationale, not three."""
    q, r, a = dirs
    messages.prepare()
    v = anne_packet(q)["verdict"]
    assert "reasoning" not in v
    assert [f["service"] for f in v["service_fit"]] == ["ai_outreach"]
    assert v["why_now"] == "fresh raise"
    assert v["primary_angle"]["fingerprint"] == FP
    bob = json.loads((q / "TST__bob-roy.json").read_text())
    assert [f["service"] for f in bob["verdict"]["service_fit"]] == ["ai_consultation"]


def test_qa_service_gate_now_enforces_recommended_service(dirs):
    """With service_fit collapsed, a draft on any non-recommended service
    hard-fails packet consistency — matching the packet instructions."""
    q, r, a = dirs
    messages.prepare()
    packet = anne_packet(q)
    hard, _ = _qa(make_seq(service="ai_marketing"), packet)  # was in full fits
    assert any("service" in h for h in hard)


# --- distilled framework embed: comments + embed:skip sections never ship ---

def test_shared_framework_is_distilled(dirs):
    """_shared.json embeds a distilled framework: maintainer HTML comments and
    <!-- embed:skip --> sections (Business Context interview, QA checklist —
    both duplicated by services_catalog / hard_rules / qa_check) are stripped;
    the copy rules the copywriter actually needs survive."""
    q, r, a = dirs
    messages.prepare()
    fw = json.loads((q / "_shared.json").read_text())["copywriter_framework"]
    assert "<!--" not in fw
    assert "Business Context" not in fw          # interview framing skipped
    assert "QA Checklist" not in fw              # enforced by qa_check anyway
    # load-bearing sections survive verbatim
    for kept in ("SPARK", "Signal → Pain → Fix", "Voice", "value-prop line",
                 "The 4-Step Sequence", "Archetypes"):
        assert kept in fw, f"missing section: {kept}"
    # the whole point: materially smaller than the ~21KB source doc
    assert len(fw) < 17000


def test_distill_framework_strips_marked_spans_and_comments():
    text = (
        "# Title\n\n<!-- maintainer note -->\n\nkeep me\n\n"
        "<!-- embed:skip -->\n## Interview\nsecret setup\n<!-- /embed:skip -->\n\n"
        "also keep\n"
    )
    out = messages._distill_framework(text)
    assert "keep me" in out and "also keep" in out
    assert "maintainer note" not in out
    assert "Interview" not in out and "secret setup" not in out
    assert "\n\n\n" not in out


# ---------- R11: no-channel skip ----------

def test_prepare_skips_contact_with_no_email_and_no_linkedin(dirs):
    q, r, a = dirs
    messages.db.contacts_rows.append({"id": 13, "name": "Cara Ghost", "title": "CFO",
                                      "role_bucket": "CFO", "linkedin_url": None, "email": None})
    written, skips = messages.prepare()
    assert "TST__cara-ghost" in skips["no_channel"]
    assert not (q / "TST__cara-ghost.json").exists()
    assert len(written) == 2  # the two reachable contacts still get packets


def test_prepare_single_channel_contacts_not_skipped(dirs):
    # CONTACT_CMO is linkedin-only, CONTACT_CEO email-only — both reachable
    written, skips = messages.prepare()
    assert skips["no_channel"] == []
    assert len(written) == 2


# ---------- R12: angle summaries + colleague diversity ----------

def test_packet_angles_carry_one_line_summary(dirs):
    q, _, _ = dirs
    messages.prepare()
    pkt = anne_packet(q)
    assert pkt["angles"], "fixture should yield at least one fresh angle"
    for a in pkt["angles"]:
        assert isinstance(a.get("summary"), str) and a["summary"]
        assert "\n" not in a["summary"]
        assert a["family"] in a["summary"]


def test_packet_colleagues_carry_role_bucket_and_diversity_note(dirs):
    q, _, _ = dirs
    messages.prepare()
    pkt = anne_packet(q)
    assert pkt["colleagues_also_messaged"] == [
        {"name": "Bob Roy", "title": "CEO", "role_bucket": "CEO"}]
    note = pkt["diversity_note"]
    assert note and "same angle" in note.lower()
    assert "CMO" in note  # differentiate through THIS contact's lens


def test_packet_diversity_note_none_when_solo_contact(dirs):
    q, _, _ = dirs
    messages.db.contacts_rows = [dict(CONTACT_CMO)]
    messages.prepare()
    pkt = anne_packet(q)
    assert pkt["diversity_note"] is None


# ---------- dense-paragraph warning ----------

def test_dense_paragraph_warns(packet):
    """Voice rule "every sentence gets its own paragraph" — wall-of-text
    paragraphs (3+ sentences on one line) get a warning-tier flag. Two-beat
    idioms ("Worth sending over? No pitch attached.") stay allowed."""
    dense = ("You have a new CEO and a raise that closed. The board wants "
             "clarity on risk. We help close that gap. Are you seeing this too?")
    hard, warn = _qa(seq_with_step(1, body=dense), packet)
    assert any("sentence" in w and "paragraph" in w for w in warn)


def test_two_sentence_line_and_ps_line_do_not_warn(packet):
    # canonical bodies (incl. BODY2's "Worth sending over? No pitch attached.")
    _, warn = _qa(make_seq(), packet)
    assert not any("paragraph" in w for w in warn)
    ps = BODY2.replace("\n\nUday", "\n\nP.S. They ship weekly. Worth watching.\n\nUday")
    _, warn = _qa(seq_with_step(2, body=ps), packet)
    assert not any("paragraph" in w for w in warn)
