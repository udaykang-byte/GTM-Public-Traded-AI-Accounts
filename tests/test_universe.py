"""universe.screen(): the L1 prescreen runs after the cap-band stage — this
file covers that wiring specifically (network calls are monkeypatched; the
existing classify_sector/sector-vocabulary coverage lives in
test_config_profiles.py)."""
from pipeline import universe

UNIVERSE_SETTINGS = {
    "exchanges": ["Nasdaq"],
    "market_cap_min": 50_000_000, "market_cap_max": 300_000_000,
    "generic_tech_sic": [], "exclude_name_patterns": [],
    "sectors": {"saas": {"sic": ["7372"], "keywords": []}},
}
UNIVERSE_ROWS = [
    {"cik": 1, "name": "Good Software Inc", "ticker": "GOOD", "exchange": "Nasdaq"},
    {"cik": 2, "name": "Bad Software Inc", "ticker": "BADCO", "exchange": "Nasdaq"},
]
SLIM_BY_CIK = {
    1: {"cik": 1, "sic": "7372", "sic_description": "software", "website": None, "state": "CA", "name": "Good Software Inc"},
    2: {"cik": 2, "sic": "7372", "sic_description": "software", "website": None, "state": "CA", "name": "Bad Software Inc"},
}


def _patch_network(monkeypatch, market_cap=100_000_000):
    monkeypatch.setattr(universe, "fetch_universe", lambda: list(UNIVERSE_ROWS))
    monkeypatch.setattr(universe, "get_submission_slim", lambda cik: SLIM_BY_CIK[cik])
    monkeypatch.setattr(universe, "get_market_cap", lambda ticker, exchange=None: market_cap)


def test_screen_drops_prescreen_failures_and_counts_them(monkeypatch):
    monkeypatch.setitem(universe.SETTINGS, "universe", dict(UNIVERSE_SETTINGS))
    monkeypatch.setitem(universe.SETTINGS, "prescreen", {
        "exclude_tickers": ["BADCO"], "exclude_sic": [], "exchange_allowlist": [],
        "otc_only_exclude": True, "shell_name_patterns": [],
    })
    _patch_network(monkeypatch)

    results, stats = universe.screen()

    assert [c.ticker for c in results] == ["GOOD"]
    assert stats["prescreen_dq"] == 1


def test_screen_prescreen_dq_stat_present_and_zero_when_clean(monkeypatch):
    monkeypatch.setitem(universe.SETTINGS, "universe", dict(UNIVERSE_SETTINGS))
    monkeypatch.setitem(universe.SETTINGS, "prescreen", {})
    _patch_network(monkeypatch)

    results, stats = universe.screen()

    assert stats["prescreen_dq"] == 0
    assert {c.ticker for c in results} == {"GOOD", "BADCO"}
