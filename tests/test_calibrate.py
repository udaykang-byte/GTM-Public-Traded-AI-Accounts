"""calibrate: outcome -> signal-weight report. Report-only — it must never
touch settings; suggestions require the min-sends bar on BOTH the signal row
and the baseline."""
from pipeline import calibrate


MSGS = {  # company_cik -> messages (as db.all_messages returns)
    1: [{"id": 101, "company_cik": 1}, {"id": 102, "company_cik": 1}],
    2: [{"id": 201, "company_cik": 2}],
    3: [{"id": 301, "company_cik": 3}],
}
SIGS = {
    1: [{"type": "E1"}, {"type": "E3"}],
    2: [{"type": "E1"}],
    3: [{"type": "P2"}],
}


def _events(sent_ids, replied_ids=(), positive_ids=()):
    ev = [{"message_id": i, "event": "sent"} for i in sent_ids]
    ev += [{"message_id": i, "event": "replied"} for i in replied_ids]
    ev += [{"message_id": i, "event": "positive_reply"} for i in positive_ids]
    return ev


def test_signal_outcome_table_groups_messages_by_company_signals():
    events = _events([101, 102, 201, 301], replied_ids=[101, 201], positive_ids=[101])
    table = calibrate.signal_outcome_table(MSGS, events, SIGS, min_sends=2)
    # E1 companies (cik 1,2) -> messages 101,102,201: 3 sent, 2 replied, 1 positive
    assert table["E1"]["n_sent"] == 3 and table["E1"]["n_replied"] == 2
    assert table["E1"]["n_positive_reply"] == 1
    # P2 only cik 3 -> 1 sent < min_sends 2 -> insufficient
    assert table["P2"]["insufficient"] is True


def test_weight_suggestions_directions():
    baseline = {"insufficient": False, "n_sent": 100, "positive_reply_rate": 0.10, "reply_rate": 0.2}
    table = {
        "E1": {"insufficient": False, "n_sent": 30, "positive_reply_rate": 0.20, "reply_rate": 0.3},
        "E5": {"insufficient": False, "n_sent": 30, "positive_reply_rate": 0.05, "reply_rate": 0.1},
        "E3": {"insufficient": False, "n_sent": 30, "positive_reply_rate": 0.11, "reply_rate": 0.2},
        "P2": {"insufficient": True, "min_sends": 25, "n_sent": 3},
    }
    sugg = {s["signal"]: s["verdict"] for s in calibrate.weight_suggestions(table, baseline)}
    assert sugg["E1"] == "consider raising weight"   # 2.0x baseline
    assert sugg["E5"] == "consider lowering weight"  # 0.5x baseline
    assert sugg["E3"] == "hold"                      # 1.1x
    assert sugg["P2"] == "insufficient data"


def test_weight_suggestions_empty_when_baseline_insufficient():
    baseline = {"insufficient": True, "min_sends": 25, "n_sent": 4}
    table = {"E1": {"insufficient": False, "n_sent": 30, "positive_reply_rate": 0.2, "reply_rate": 0.3}}
    assert calibrate.weight_suggestions(table, baseline) == []


def test_zero_positive_baseline_never_falls_back_to_reply_rate():
    """A 0% positive-reply baseline is a real value, not missing data — the
    old `or reply_rate` fallback would silently rank signals against the
    broader reply rate."""
    baseline = {"insufficient": False, "n_sent": 30, "reply_rate": 0.5,
                "positive_reply_rate": 0.0, "meeting_rate": 0.0}
    table = {"E1": {"insufficient": False, "n_sent": 30, "reply_rate": 0.5,
                    "positive_reply_rate": 0.2, "meeting_rate": 0.0}}
    out = calibrate.weight_suggestions(table, baseline)
    assert out[0]["ratio"] is None
    assert out[0]["verdict"] == "no baseline rate"


def test_zero_positive_signal_rate_counts_as_lower_not_reply_fallback():
    baseline = {"insufficient": False, "n_sent": 40, "reply_rate": 0.2,
                "positive_reply_rate": 0.1, "meeting_rate": 0.0}
    table = {"E5": {"insufficient": False, "n_sent": 40, "reply_rate": 0.5,
                    "positive_reply_rate": 0.0, "meeting_rate": 0.0}}
    out = calibrate.weight_suggestions(table, baseline)
    assert out[0]["ratio"] == 0.0
    assert out[0]["verdict"] == "consider lowering weight"


def test_augment_with_derived_adds_e8_for_peer_laggards():
    """E8 is synthesized at scoring time and never persisted — calibrate must
    derive it the same way or the R7 weight can never be evaluated."""
    companies = [{"cik": i, "sector_bucket": "saas"} for i in range(1, 8)]
    signals_by_cik = {i: [{"type": "E1"}] for i in (2, 3, 4)}  # 3/6 peers = 50%
    signals_by_cik[1] = [{"type": "E9"}]  # target: no AI language
    out = calibrate.augment_with_derived({1: [{"id": 9}]}, signals_by_cik, companies)
    assert any(s["type"] == "E8" for s in out[1])
    # non-messaged companies untouched
    assert not any(s.get("type") == "E8" for s in out.get(2, []))
