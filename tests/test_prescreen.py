"""L1 pre-screen: pure function, no DB/network — settings passed explicitly."""
from pipeline import prescreen

BASE_SETTINGS = {
    "universe": {"exchanges": ["NYSE", "Nasdaq", "NYSE American"],
                 "market_cap_min": 50_000_000, "market_cap_max": 300_000_000},
    "prescreen": {
        "exclude_tickers": ["ACME"],
        "exclude_sic": ["6770"],
        "exchange_allowlist": [],
        "otc_only_exclude": True,
        "shell_name_patterns": ["acquisition corp", "blank check"],
    },
}

GOOD_COMPANY = {
    "ticker": "GOOD", "name": "Good Software Inc", "sic": "7372",
    "exchange": "Nasdaq", "market_cap": 100_000_000,
}


def test_clean_company_passes():
    assert prescreen.check(GOOD_COMPANY, BASE_SETTINGS) is None


def test_excluded_ticker_blocks_regardless_of_case():
    company = {**GOOD_COMPANY, "ticker": "acme"}
    assert prescreen.check(company, BASE_SETTINGS) == "excluded_ticker"


def test_excluded_sic_blocks():
    company = {**GOOD_COMPANY, "sic": "6770"}
    assert prescreen.check(company, BASE_SETTINGS) == "excluded_sic:6770"


def test_shell_name_pattern_blocks():
    company = {**GOOD_COMPANY, "name": "Big Acquisition Corp"}
    reason = prescreen.check(company, BASE_SETTINGS)
    assert reason == "shell_name:acquisition corp"


def test_otc_blank_exchange_blocks_by_default():
    company = {**GOOD_COMPANY, "exchange": ""}
    assert prescreen.check(company, BASE_SETTINGS) == "otc_listed"


def test_otc_named_exchange_blocks_by_default():
    company = {**GOOD_COMPANY, "exchange": "OTC Pink"}
    assert prescreen.check(company, BASE_SETTINGS) == "otc_listed"


def test_otc_only_exclude_false_allows_blank_exchange_through_to_allowlist():
    settings = {**BASE_SETTINGS, "prescreen": {**BASE_SETTINGS["prescreen"], "otc_only_exclude": False}}
    company = {**GOOD_COMPANY, "exchange": ""}
    # falls through OTC check, then fails the exchange allowlist (falls back
    # to universe.exchanges, and "" isn't one of them)
    assert prescreen.check(company, settings) == "exchange_not_allowed:unknown"


def test_exchange_allowlist_overrides_universe_exchanges():
    settings = {**BASE_SETTINGS, "prescreen": {**BASE_SETTINGS["prescreen"],
                                                "otc_only_exclude": False,
                                                "exchange_allowlist": ["CBOE"]}}
    company = {**GOOD_COMPANY, "exchange": "Nasdaq"}  # allowed by universe.exchanges, not by the override
    assert prescreen.check(company, settings) == "exchange_not_allowed:Nasdaq"
    company_ok = {**GOOD_COMPANY, "exchange": "CBOE"}
    assert prescreen.check(company_ok, settings) is None


def test_outside_cap_band_blocks():
    company = {**GOOD_COMPANY, "market_cap": 10_000_000}
    assert prescreen.check(company, BASE_SETTINGS) == "outside_cap_band"
    company = {**GOOD_COMPANY, "market_cap": 500_000_000}
    assert prescreen.check(company, BASE_SETTINGS) == "outside_cap_band"


def test_unknown_market_cap_does_not_block():
    company = {**GOOD_COMPANY, "market_cap": None}
    assert prescreen.check(company, BASE_SETTINGS) is None


def test_empty_prescreen_block_is_a_noop_except_otc_default():
    # missing prescreen block entirely -> defaults (otc_only_exclude True is
    # the only default with teeth); a listed company sails through
    settings = {"universe": BASE_SETTINGS["universe"]}
    assert prescreen.check(GOOD_COMPANY, settings) is None


def test_reason_precedence_ticker_before_sic():
    company = {**GOOD_COMPANY, "ticker": "ACME", "sic": "6770"}
    assert prescreen.check(company, BASE_SETTINGS) == "excluded_ticker"
