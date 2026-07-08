"""Universe screening: SEC company list -> sector buckets -> market-cap band.

Network etiquette: SEC asks for an identifying User-Agent and <=10 req/s.
Everything is cached under data/cache/ so the expensive first crawl never
repeats; subsequent discover runs are incremental.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta

import httpx

from pipeline import prescreen
from pipeline.config import CACHE_DIR, SETTINGS, edgar_identity, env
from pipeline.models import Company

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

SUBMISSIONS_CACHE = CACHE_DIR / "submissions"
SUBMISSIONS_CACHE.mkdir(parents=True, exist_ok=True)
UNIVERSE_CACHE = CACHE_DIR / "company_tickers_exchange.json"
CAP_CACHE = CACHE_DIR / "market_caps.json"

_MIN_INTERVAL = 0.13  # ~8 req/s, under SEC's 10/s ceiling
_last_request = 0.0
_http: httpx.Client | None = None


def _throttle() -> None:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _http_client() -> httpx.Client:
    # persistent client: keep-alive matters — a fresh TLS handshake per
    # request makes the universe crawl ~5x slower
    global _http
    if _http is None:
        _http = httpx.Client(
            headers={"User-Agent": edgar_identity(), "Accept-Encoding": "gzip"},
            timeout=30,
            follow_redirects=True,
        )
    return _http


def _sec_get(url: str) -> httpx.Response:
    _throttle()
    return _http_client().get(url)


def _atomic_write(path, text: str) -> None:
    # readers must never see a half-written cache file; the tmp name must be
    # unique per writer or concurrent crawls race each other on the rename
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json_cache(path) -> dict | list | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        try:
            path.unlink()
        except OSError:
            pass
        return None


# ---------- SEC company universe ----------

def fetch_universe(max_age_hours: int = 24) -> list[dict]:
    """All SEC-registered tickers: [{cik, name, ticker, exchange}, ...]."""
    if UNIVERSE_CACHE.exists():
        age = time.time() - UNIVERSE_CACHE.stat().st_mtime
        if age < max_age_hours * 3600:
            cached = _read_json_cache(UNIVERSE_CACHE)
            if cached is not None:
                return cached
    resp = _sec_get(TICKERS_URL)
    resp.raise_for_status()
    payload = resp.json()
    fields = payload["fields"]  # ["cik","name","ticker","exchange"]
    rows = [dict(zip(fields, row)) for row in payload["data"]]
    _atomic_write(UNIVERSE_CACHE, json.dumps(rows))
    return rows


def get_submission_slim(cik: int) -> dict | None:
    """SIC, description, website, HQ state for one company. Cached forever."""
    cache_file = SUBMISSIONS_CACHE / f"{cik}.json"
    if cache_file.exists():
        cached = _read_json_cache(cache_file)
        if cached is not None:
            return cached
    try:
        resp = _sec_get(SUBMISSIONS_URL.format(cik=cik))
        if resp.status_code != 200:
            return None
        d = resp.json()
    except Exception:
        return None
    slim = {
        "cik": cik,
        "sic": str(d.get("sic") or ""),
        "sic_description": d.get("sicDescription") or "",
        "website": d.get("website") or None,
        "state": (d.get("addresses", {}).get("business", {}) or {}).get("stateOrCountry"),
        "name": d.get("name") or "",
    }
    _atomic_write(cache_file, json.dumps(slim))
    return slim


# ---------- market cap (yfinance, cached) ----------

class CapFetchThrottled(Exception):
    """Yahoo Finance is rate-limiting us — stop the cap stage, resume later."""


_YF_PACE_SECONDS = 0.6


def _load_cap_cache() -> dict:
    if CAP_CACHE.exists():
        cached = _read_json_cache(CAP_CACHE)
        if isinstance(cached, dict):
            return cached
    return {}


# Google Finance via SerpAPI: fallback cap source while Yahoo rate-limits us.
_GF_EXCHANGES = {"nyse": "NYSE", "nasdaq": "NASDAQ", "nyse american": "NYSEAMERICAN"}
_CAP_SUFFIXES = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _parse_cap_text(text: str) -> float | None:
    m = re.match(r"([\d.]+)\s*([KMBT])", text.strip())
    return float(m.group(1)) * _CAP_SUFFIXES[m.group(2)] if m else None


def _serpapi_market_cap(ticker: str, exchange: str | None) -> float | None:
    api_key = env("SERPAPI_API_KEY")
    if not api_key:
        return None
    gf_ex = _GF_EXCHANGES.get((exchange or "").lower())
    q = f"{ticker}:{gf_ex}" if gf_ex else ticker
    try:
        resp = httpx.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_finance", "q": q, "api_key": api_key},
            timeout=30,
        )
        stats = resp.json().get("knowledge_graph", {}).get("key_stats", {}).get("stats", [])
    except Exception:
        return None
    for stat in stats:
        if stat.get("label", "").lower() in ("mkt. cap", "market cap"):
            return _parse_cap_text(stat.get("value") or "")
    return None


def get_market_cap(ticker: str, max_age_days: int = 7, exchange: str | None = None) -> float | None:
    cache = _load_cap_cache()
    entry = cache.get(ticker)
    # only trust successful lookups — a rate-limited run must not poison the
    # cache with nulls for a week
    if entry and entry.get("cap") is not None:
        fetched = datetime.fromisoformat(entry["at"])
        if datetime.now() - fetched < timedelta(days=max_age_days):
            return entry["cap"]
    try:
        import yfinance as yf

        time.sleep(_YF_PACE_SECONDS)
        info = yf.Ticker(ticker).fast_info
        cap = info["marketCap"] if "marketCap" in info else getattr(info, "market_cap", None)
    except Exception as exc:
        if "ratelimit" in type(exc).__name__.lower() or "too many requests" in str(exc).lower():
            cap = _serpapi_market_cap(ticker, exchange)
            if cap is None:
                raise CapFetchThrottled(ticker) from exc
        else:
            return None  # genuinely missing (delisted etc.) — retry next run, don't cache
    if cap is None:
        return None
    cap = float(cap)
    cache[ticker] = {"cap": cap, "at": datetime.now().isoformat()}
    _atomic_write(CAP_CACHE, json.dumps(cache))
    return cap


# ---------- sector classification ----------

def classify_sector(sic: str, name: str, sic_description: str = "") -> str:
    """Returns the matching SETTINGS sector key (free vocabulary — profile
    packs bring their own sector sets), or "other"."""
    uni = SETTINGS.get("universe", {})
    sectors: dict = uni.get("sectors", {})
    generic = {str(s) for s in uni.get("generic_tech_sic", [])}
    text = f"{name} {sic_description}".lower()

    def kw_match(key: str) -> bool:
        return any(kw.lower() in text for kw in sectors.get(key, {}).get("keywords", []))

    def claims(key: str) -> bool:
        cfg = sectors.get(key, {})
        if sic in {str(s) for s in cfg.get("exclude_sic", [])}:
            return False
        return sic in {str(s) for s in cfg.get("sic", [])}

    # generic tech SIC: keywords decide the domain; sectors that claim the
    # SIC directly (saas in the default pack) are the fallback. A sector with
    # generic_keyword_match: false only claims via its explicit sic list.
    if sic in generic:
        claimers = [key for key in sectors if claims(key)]
        for key in sectors:
            if key in claimers:
                continue
            if not sectors.get(key, {}).get("generic_keyword_match", True):
                continue
            if kw_match(key):
                return key
        return claimers[0] if claimers else "other"

    # explicit SIC membership for the domain sectors
    for key in sectors:
        if claims(key):
            return key

    return "other"


# ---------- resolution + screening ----------

def _allowed_exchange(exchange: str | None) -> bool:
    allowed = {e.lower() for e in SETTINGS.get("universe", {}).get("exchanges", [])}
    return bool(exchange) and exchange.lower() in allowed


def build_company(uni_row: dict, with_cap: bool = True) -> Company | None:
    """Universe row -> Company with SIC, sector, market cap."""
    cik = int(uni_row["cik"])
    slim = get_submission_slim(cik)
    if slim is None:
        return None
    sector = classify_sector(slim["sic"], uni_row.get("name", ""), slim["sic_description"])
    cap = get_market_cap(uni_row["ticker"], exchange=uni_row.get("exchange")) if with_cap else None
    return Company(
        cik=cik,
        ticker=str(uni_row["ticker"]).upper(),
        name=uni_row.get("name") or slim["name"],
        exchange=uni_row.get("exchange"),
        sic=slim["sic"] or None,
        sic_description=slim["sic_description"] or None,
        sector_bucket=sector,
        market_cap=cap,
        website=slim["website"],
        hq_state=slim["state"],
    )


def resolve_tickers(tickers: list[str]) -> tuple[list[Company], list[str]]:
    """User-provided tickers -> Companies (ingest path). Returns (resolved, unresolved)."""
    universe = {str(u["ticker"]).upper(): u for u in fetch_universe()}
    resolved: list[Company] = []
    unresolved: list[str] = []
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        row = universe.get(t)
        if row is None:
            unresolved.append(t)
            continue
        company = build_company(row)
        if company is None:
            unresolved.append(t)
        else:
            resolved.append(company)
    return resolved, unresolved


def screen(
    limit: int | None = None,
    skip_ciks: set[int] | None = None,
    progress=None,
) -> tuple[list[Company], dict]:
    """Full discovery screen. Returns (companies in band, funnel stats).

    progress: optional callable(stage:str, done:int, total:int) for UI updates.
    """
    uni = SETTINGS.get("universe", {})
    cap_min = float(uni.get("market_cap_min", 50_000_000))
    cap_max = float(uni.get("market_cap_max", 300_000_000))
    exclude_names = [str(p).lower() for p in uni.get("exclude_name_patterns", [])]
    skip_ciks = skip_ciks or set()

    universe = fetch_universe()
    listed = [u for u in universe if _allowed_exchange(u.get("exchange"))]

    stats = {
        "universe": len(universe),
        "listed_on_target_exchanges": len(listed),
        "already_in_db": 0,
        "sic_failed": 0,
        "name_excluded": 0,
        "sector_matched": 0,
        "cap_checked": 0,
        "cap_throttled_at": None,
        "prescreen_dq": 0,
        "in_band": 0,
    }

    # Stage 1: SIC + sector (cached crawl)
    sector_matched: list[tuple[dict, dict]] = []
    seen_ciks: set[int] = set()  # dual-class listings: one row per company
    for i, row in enumerate(listed):
        if progress and i % 200 == 0:
            progress("sic", i, len(listed))
        cik = int(row["cik"])
        if cik in skip_ciks:
            stats["already_in_db"] += 1
            continue
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)
        slim = get_submission_slim(cik)
        if slim is None:
            stats["sic_failed"] += 1
            continue
        name = (row.get("name") or slim["name"] or "").lower()
        if any(p in name for p in exclude_names):
            stats["name_excluded"] += 1
            continue
        sector = classify_sector(slim["sic"], row.get("name", ""), slim["sic_description"])
        if sector != "other":
            sector_matched.append((row, slim))
    stats["sector_matched"] = len(sector_matched)

    # Stage 2: market-cap band (cached)
    results: list[Company] = []
    for i, (row, slim) in enumerate(sector_matched):
        if progress and i % 25 == 0:
            progress("cap", i, len(sector_matched))
        try:
            cap = get_market_cap(row["ticker"], exchange=row.get("exchange"))
        except CapFetchThrottled:
            # partial results are fine — successful caps are cached, the rest
            # resume on the next run once Yahoo cools down
            stats["cap_throttled_at"] = f"{i}/{len(sector_matched)}"
            break
        stats["cap_checked"] += 1
        if cap is None or not (cap_min <= cap <= cap_max):
            continue
        sector = classify_sector(slim["sic"], row.get("name", ""), slim["sic_description"])
        company = Company(
            cik=int(row["cik"]),
            ticker=str(row["ticker"]).upper(),
            name=row.get("name") or slim["name"],
            exchange=row.get("exchange"),
            sic=slim["sic"] or None,
            sic_description=slim["sic_description"] or None,
            sector_bucket=sector,
            market_cap=cap,
            website=slim["website"],
            hq_state=slim["state"],
        )
        # L1 pre-screen: exclude_tickers/exclude_sic/shell-name heuristics on
        # top of the exchange + cap-band checks already applied above —
        # filtered candidates are dropped (never seeded), saving EDGAR/Parallel
        # spend before it happens
        if prescreen.check(company.model_dump(), SETTINGS):
            stats["prescreen_dq"] += 1
            continue
        results.append(company)
        if limit and len(results) >= limit:
            break
    stats["in_band"] = len(results)
    return results, stats
