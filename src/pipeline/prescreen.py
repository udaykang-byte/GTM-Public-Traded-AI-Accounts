"""L1 pre-screen: cheap, deterministic exclusions applied before EDGAR/Parallel
spend — customers/competitors, excluded SIC codes, OTC/unlisted exchanges,
shell-company name heuristics, and the micro-cap band.

`check()` is a pure function (no DB, no network, settings passed explicitly)
so it is unit-testable in isolation and callable from both `ingest` (single,
user-provided companies) and `universe.screen()` (bulk, inside the discover
funnel). Config lives under the `prescreen:` block in settings.yaml.
"""
from __future__ import annotations


def check(company: dict, settings: dict) -> str | None:
    """Returns a short dq_reason string if `company` fails the L1 prescreen,
    else None. `company` needs: ticker, name, sic, exchange, market_cap."""
    cfg = settings.get("prescreen", {}) or {}
    uni = settings.get("universe", {}) or {}

    ticker = str(company.get("ticker") or "").upper()
    name = str(company.get("name") or "")
    sic = str(company.get("sic") or "")
    exchange = str(company.get("exchange") or "").strip()
    exchange_lc = exchange.lower()
    cap = company.get("market_cap")

    exclude_tickers = {str(t).upper() for t in cfg.get("exclude_tickers", [])}
    if ticker in exclude_tickers:
        return "excluded_ticker"

    exclude_sic = {str(s) for s in cfg.get("exclude_sic", [])}
    if sic and sic in exclude_sic:
        return f"excluded_sic:{sic}"

    shell_patterns = [str(p).lower() for p in cfg.get("shell_name_patterns", []) if p]
    low_name = name.lower()
    for pat in shell_patterns:
        if pat in low_name:
            return f"shell_name:{pat}"

    if cfg.get("otc_only_exclude", True):
        is_otc_ish = (not exchange) or "otc" in exchange_lc or "pink" in exchange_lc
        if is_otc_ish:
            return "otc_listed"

    allowlist = cfg.get("exchange_allowlist") or uni.get("exchanges", [])
    allowed = {str(e).lower() for e in allowlist}
    if allowed and exchange_lc not in allowed:
        return f"exchange_not_allowed:{exchange or 'unknown'}"

    cap_min = cfg.get("market_cap_min", uni.get("market_cap_min"))
    cap_max = cfg.get("market_cap_max", uni.get("market_cap_max"))
    if cap is not None and cap_min is not None and cap_max is not None:
        if not (float(cap_min) <= float(cap) <= float(cap_max)):
            return "outside_cap_band"

    return None
