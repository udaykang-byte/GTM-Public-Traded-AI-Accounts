"""Pure db.py helpers that don't touch the Supabase client — safe to unit
test directly (no FakeDB needed)."""
from pipeline.db import order_by_tier_priority


def test_orders_by_tier_ascending_then_priority_descending():
    companies = [
        {"cik": 1, "tier": "T2"},
        {"cik": 2, "tier": "T1"},
        {"cik": 3, "tier": "T1"},
    ]
    priority = {1: 50.0, 2: 10.0, 3: 90.0}
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [3, 2, 1]


def test_null_tier_sorts_as_t3():
    companies = [
        {"cik": 1, "tier": None},
        {"cik": 2, "tier": "T2"},
        {"cik": 3, "tier": "T4"},
    ]
    result = order_by_tier_priority(companies, {})
    assert [c["cik"] for c in result] == [2, 1, 3]


def test_unrecognized_tier_value_sorts_as_t3():
    companies = [{"cik": 1, "tier": "bogus"}, {"cik": 2, "tier": "T1"}]
    result = order_by_tier_priority(companies, {})
    assert [c["cik"] for c in result] == [2, 1]


def test_missing_priority_sorts_last_within_tier():
    companies = [{"cik": 1, "tier": "T1"}, {"cik": 2, "tier": "T1"}]
    priority = {1: 20.0}  # cik 2 missing from the map entirely
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [1, 2]


def test_none_priority_value_sorts_last_within_tier():
    companies = [{"cik": 1, "tier": "T1"}, {"cik": 2, "tier": "T1"}]
    priority = {1: 20.0, 2: None}
    result = order_by_tier_priority(companies, priority)
    assert [c["cik"] for c in result] == [1, 2]


def test_empty_companies_list():
    assert order_by_tier_priority([], {}) == []
