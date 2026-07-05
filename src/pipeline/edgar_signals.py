"""EDGAR signal collectors E1-E9.

Filing text/sections come from edgartools; hard financial numbers come from
SEC's companyfacts XBRL API directly (deterministic, cache-friendly).
Signal taxonomy and rationale: docs/SIGNALS.md.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import httpx

from pipeline.config import CACHE_DIR, SETTINGS, edgar_identity
from pipeline.models import Signal

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FACTS_CACHE = CACHE_DIR / "companyfacts"
FACTS_CACHE.mkdir(parents=True, exist_ok=True)

_initialized = False


def _init_edgar() -> None:
    global _initialized
    if not _initialized:
        from edgar import set_identity

        set_identity(edgar_identity())
        _initialized = True


def _w(sig_type: str) -> float:
    return float(SETTINGS.get("scoring", {}).get("weights", {}).get(sig_type, 5))


# ---------- text analysis helpers ----------

AI_PHRASES = [
    "artificial intelligence", "machine learning", "generative ai",
    "large language model", "ai-powered", "ai-driven", "ai-enabled",
    "deep learning", "predictive analytics", "intelligent automation",
    "chatbot", "chatgpt",
]
AI_WORD = re.compile(r"\bAI\b")

RESTRUCTURING_PHRASES = [
    "restructuring plan", "restructuring program", "cost reduction program",
    "cost reduction plan", "workforce reduction", "reduction in force",
    "cost savings initiative", "operational efficiency program",
]

EXEC_TITLES = {
    "chief executive officer": "CEO", "chief technology officer": "CTO",
    "chief information officer": "CIO", "chief marketing officer": "CMO",
    "chief digital officer": "CDO", "chief operating officer": "COO",
    "chief revenue officer": "CRO", "chief ai officer": "CAIO",
    "chief artificial intelligence officer": "CAIO",
    "chief financial officer": "CFO",
}
APPOINT_WORDS = ["appoint", "named", "will serve as", "has joined", "hired", "promoted to"]

TECH_LEADER_PATTERNS = [
    "chief technology officer", "chief information officer",
    "chief digital officer", "chief innovation officer",
    "chief ai officer", "chief artificial intelligence officer",
    "chief data officer", "chief product and technology officer",
]


def count_ai_mentions(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    count = sum(lower.count(p) for p in AI_PHRASES)
    count += len(AI_WORD.findall(text))
    return count


def extract_quote(text: str, max_len: int = 350) -> str | None:
    """Sentence around the first strong AI phrase — becomes evidence_quote."""
    if not text:
        return None
    lower = text.lower()
    pos = -1
    for p in AI_PHRASES:
        i = lower.find(p)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        m = AI_WORD.search(text)
        pos = m.start() if m else -1
    if pos < 0:
        return None
    start = max(text.rfind(".", 0, pos) + 1, pos - 200)
    end = text.find(".", pos)
    end = min(end + 1 if end > 0 else pos + 200, start + max_len)
    return " ".join(text[start:end].split())


def _filing_url(cik: int, filing) -> str:
    for attr in ("homepage_url", "url", "filing_url"):
        val = getattr(filing, attr, None)
        if isinstance(val, str) and val.startswith("http"):
            return val
    acc = str(getattr(filing, "accession_no", "") or getattr(filing, "accession_number", ""))
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"


def _filing_sections(filing) -> dict[str, str]:
    """{'strategy': Item 1 + Item 7 text, 'risk': Item 1A text, 'all': everything}."""
    # Preferred: edgartools typed object — named sections, then item indexing
    try:
        obj = filing.obj()
        strategy_parts: list[str] = []
        for attr in ("business", "management_discussion", "mda"):
            val = getattr(obj, attr, None)
            if val and len(str(val)) > 200:
                strategy_parts.append(str(val))
        if not strategy_parts:
            for item in ("Item 1", "Item 7"):
                try:
                    val = obj[item]
                except Exception:
                    val = None
                if val and len(str(val)) > 200:
                    strategy_parts.append(str(val))
        risk_text = ""
        risk = getattr(obj, "risk_factors", None)
        if risk and len(str(risk)) > 200:
            risk_text = str(risk)
        else:
            try:
                val = obj["Item 1A"]
                if val and len(str(val)) > 200:
                    risk_text = str(val)
            except Exception:
                pass
        if strategy_parts or risk_text:
            parts = {"strategy": "\n".join(strategy_parts), "risk": risk_text}
            parts["all"] = parts["strategy"] + "\n" + risk_text
            return parts
    except Exception:
        pass
    # Fallback: split raw text on Item headers
    try:
        text = filing.markdown()
    except Exception:
        try:
            text = filing.text()
        except Exception:
            return {}
    if not text:
        return {}
    pattern = re.compile(r"(?im)^\s*#*\s*item\s+(1a|1b|1|2|7a|7|8)\b")
    pieces: dict[str, str] = {}
    matches = list(pattern.finditer(text))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        item = m.group(1).lower()
        pieces[item] = pieces.get(item, "") + text[m.start():end]
    sections = {
        "strategy": pieces.get("1", "") + "\n" + pieces.get("7", ""),
        "risk": pieces.get("1a", ""),
        "all": text,
    }
    return sections


# ---------- E1/E2: AI language in annual reports ----------

def ai_language_signals(edgar_company, company: dict) -> list[Signal]:
    cik = company["cik"]
    # exact 10-K only — amendments (10-K/A) often carry just Part III items
    filings = [
        f for f in edgar_company.get_filings(form="10-K")
        if str(getattr(f, "form", "")) == "10-K"
    ][:2]
    if not filings:
        return []
    latest = filings[0]
    latest_sections = _filing_sections(latest)
    if not latest_sections:
        return []
    s_count = count_ai_mentions(latest_sections.get("strategy", ""))
    r_count = count_ai_mentions(latest_sections.get("risk", ""))
    if s_count == 0 and r_count == 0:
        total = count_ai_mentions(latest_sections.get("all", ""))
        if total == 0:
            return []  # no AI language at all — cohort laggard handled at scoring
        s_count = total  # sections unsplit; treat as strategy-ish

    prior_total = 0
    prior_strategy = 0
    if len(filings) > 1:
        prior_sections = _filing_sections(filings[1])
        prior_strategy = count_ai_mentions(prior_sections.get("strategy", ""))
        prior_total = prior_strategy + count_ai_mentions(prior_sections.get("risk", ""))

    url = _filing_url(cik, latest)
    observed = getattr(latest, "filing_date", None)
    raw = {
        "strategy_mentions": s_count, "risk_mentions": r_count,
        "prior_total": prior_total, "prior_strategy": prior_strategy,
    }
    signals: list[Signal] = []

    if s_count > 0:
        quote = extract_quote(latest_sections.get("strategy", "") or latest_sections.get("all", ""))
        if prior_total == 0 and s_count >= 2:
            title, weight = "First-time AI language in strategy sections of 10-K", _w("E1")
        elif prior_strategy > 0 and s_count >= max(3, 2 * prior_strategy):
            title, weight = "AI language expanding sharply YoY in 10-K", round(_w("E1") * 0.7, 1)
        elif s_count >= 3:
            title, weight = "Sustained AI language in 10-K strategy sections", round(_w("E1") * 0.5, 1)
        else:
            title, weight = None, 0
        if title:
            signals.append(Signal(
                company_cik=cik, source="edgar", type="E1", title=title,
                detail=f"{s_count} strategy-section mentions, {r_count} risk-section, prior-year total {prior_total}",
                evidence_url=url, evidence_quote=quote, observed_at=observed,
                weight=weight, raw=raw,
            ))
    if r_count > 0 and s_count == 0:
        quote = extract_quote(latest_sections.get("risk", ""))
        signals.append(Signal(
            company_cik=cik, source="edgar", type="E2",
            title="AI appears only in Risk Factors (aware but defensive)",
            detail=f"{r_count} risk-factor mentions, zero in Business/MD&A",
            evidence_url=url, evidence_quote=quote, observed_at=observed,
            weight=_w("E2"), raw=raw,
        ))
    return signals


# ---------- E3/E4: 8-K events ----------

ITEM_NUM = re.compile(r"\d+\.\d+")


def _filing_items(filing) -> set[str]:
    """Item numbers (e.g. {'5.02'}) from filing-index metadata — no document download."""
    raw = getattr(filing, "items", None)
    if raw is None:
        return set()
    if isinstance(raw, str):
        return set(ITEM_NUM.findall(raw))
    try:
        return {m for part in raw for m in ITEM_NUM.findall(str(part))}
    except TypeError:
        return set()


def eightk_signals(edgar_company, company: dict) -> list[Signal]:
    cik = company["cik"]
    lookback = int(SETTINGS.get("enrich", {}).get("edgar", {}).get("eightk_lookback_days", 365))
    cutoff = date.today() - timedelta(days=lookback)
    signals: list[Signal] = []
    seen_types: set[str] = set()

    for filing in edgar_company.get_filings(form="8-K"):
        fdate = getattr(filing, "filing_date", None)
        if fdate is None or fdate < cutoff:
            break  # filings are newest-first
        items = _filing_items(filing)
        # strict item filter (spec decision): 5.02 = exec change, 2.05 =
        # restructuring charge. Only these are worth a document download.
        want_e3 = "E3" not in seen_types and "5.02" in items
        want_e4 = "E4" not in seen_types and "2.05" in items
        if not (want_e3 or want_e4):
            continue
        try:
            text = filing.text()
        except Exception:
            continue
        if not text:
            continue
        lower = text.lower()

        if want_e3:
            hit_titles = [
                abbrev for phrase, abbrev in EXEC_TITLES.items() if phrase in lower
            ]
            is_appointment = any(w in lower for w in APPOINT_WORDS)
            if hit_titles and is_appointment:
                # anchor the quote at the appointment word nearest an exec
                # title — first-in-document lands on cover-page boilerplate
                appoint_positions = [
                    m.start() for w in APPOINT_WORDS for m in re.finditer(re.escape(w), lower)
                ]
                title_positions = [
                    m.start() for p in EXEC_TITLES for m in re.finditer(re.escape(p), lower)
                ]
                pos = min(
                    appoint_positions,
                    key=lambda ap: min(abs(ap - tp) for tp in title_positions),
                ) if appoint_positions and title_positions else 0
                window = lower[max(0, pos - 250): pos + 450]
                near = sorted({a for p, a in EXEC_TITLES.items() if p in window})
                if near:
                    hit_titles = near
                quote = " ".join(text[max(0, pos - 150): pos + 250].split())
                signals.append(Signal(
                    company_cik=cik, source="edgar", type="E3",
                    title=f"Leadership change ≤12mo: {', '.join(sorted(set(hit_titles)))}",
                    detail=f"8-K Item 5.02 filed {fdate}",
                    evidence_url=_filing_url(cik, filing), evidence_quote=quote,
                    observed_at=fdate, weight=_w("E3"),
                    raw={"titles": sorted(set(hit_titles))},
                ))
                seen_types.add("E3")

        if want_e4:
            phrase_hit = next((p for p in RESTRUCTURING_PHRASES if p in lower), None)
            pos = lower.find(phrase_hit) if phrase_hit else max(lower.find("item 2.05"), 0)
            quote = " ".join(text[max(0, pos - 100): pos + 300].split())
            signals.append(Signal(
                company_cik=cik, source="edgar", type="E4",
                title="Restructuring / cost-reduction program announced",
                detail=f"8-K Item 2.05 filed {fdate}" + (f' ("{phrase_hit}")' if phrase_hit else ""),
                evidence_url=_filing_url(cik, filing), evidence_quote=quote,
                observed_at=fdate, weight=_w("E4"), raw={},
            ))
            seen_types.add("E4")

        if {"E3", "E4"} <= seen_types:
            break
    return signals


# ---------- E5/E9: XBRL financials via companyfacts ----------

def _get_companyfacts(cik: int) -> dict | None:
    cache_file = FACTS_CACHE / f"{cik}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    try:
        resp = httpx.get(
            FACTS_URL.format(cik=cik),
            headers={"User-Agent": edgar_identity()},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    slim = {"tags": {}}
    for tag, body in (data.get("facts", {}).get("us-gaap", {}) or {}).items():
        usd = (body.get("units", {}) or {}).get("USD")
        if not usd:
            continue
        annual = {}
        for entry in usd:
            if entry.get("fp") == "FY" and entry.get("form") in ("10-K", "10-K/A") and entry.get("fy"):
                fy = int(entry["fy"])
                prev = annual.get(fy)
                if prev is None or str(entry.get("end", "")) > str(prev.get("end", "")):
                    annual[fy] = {"val": entry.get("val"), "end": entry.get("end")}
        if annual:
            slim["tags"][tag] = annual
    cache_file.write_text(json.dumps(slim))
    return slim


def _annual_series(facts: dict, candidates: list[str]) -> list[tuple[int, float]]:
    for tag in candidates:
        annual = facts.get("tags", {}).get(tag)
        if annual:
            series = sorted((int(fy), float(v["val"])) for fy, v in annual.items() if v.get("val") is not None)
            if series:
                return series
    return []


REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
]
SM_TAGS = ["SellingAndMarketingExpense", "SellingGeneralAndAdministrativeExpense"]
CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue"]


def financial_signals(company: dict) -> list[Signal]:
    cik = company["cik"]
    facts = _get_companyfacts(cik)
    if not facts:
        return []
    signals: list[Signal] = []
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    revenue = _annual_series(facts, REVENUE_TAGS)
    sm = _annual_series(facts, SM_TAGS)
    sm_tag_used = next((t for t in SM_TAGS if facts.get("tags", {}).get(t)), None)

    if len(revenue) >= 2 and len(sm) >= 2:
        rev_map, sm_map = dict(revenue), dict(sm)
        common = sorted(set(rev_map) & set(sm_map))[-2:]
        if len(common) == 2 and all(rev_map[y] for y in common):
            y0, y1 = common
            ratio0, ratio1 = sm_map[y0] / rev_map[y0], sm_map[y1] / rev_map[y1]
            growth = (rev_map[y1] - rev_map[y0]) / abs(rev_map[y0])
            if ratio1 > ratio0 * 1.05 and growth < 0.15:
                label = "S&M" if sm_tag_used == "SellingAndMarketingExpense" else "SG&A"
                signals.append(Signal(
                    company_cik=cik, source="edgar", type="E5",
                    title=f"{label} spend rising faster than revenue (GTM inefficiency)",
                    detail=(
                        f"FY{y0}->FY{y1}: {label}/revenue {ratio0:.1%} -> {ratio1:.1%}; "
                        f"revenue growth {growth:+.1%} (rev ${rev_map[y1]/1e6:.0f}M)"
                    ),
                    evidence_url=facts_url, observed_at=None, weight=_w("E5"),
                    raw={"ratio_prior": ratio0, "ratio_latest": ratio1, "growth": growth, "tag": sm_tag_used},
                ))

    cash = _annual_series(facts, CASH_TAGS)
    if cash and cash[-1][1] >= 10_000_000:
        signals.append(Signal(
            company_cik=cik, source="edgar", type="E9",
            title="Cash position supports services spend",
            detail=f"FY{cash[-1][0]} cash & equivalents ${cash[-1][1]/1e6:.0f}M",
            evidence_url=facts_url, weight=_w("E9"),
            raw={"cash": cash[-1][1], "fy": cash[-1][0]},
        ))
    return signals


# ---------- E6: tech-leadership gap from proxy ----------

def leadership_gap_signal(edgar_company, company: dict) -> list[Signal]:
    cik = company["cik"]
    filings = list(edgar_company.get_filings(form="DEF 14A"))[:1]
    if not filings:
        return []
    proxy = filings[0]
    try:
        text = proxy.text()
    except Exception:
        return []
    if not text:
        return []
    lower = text[:400_000].lower()
    has_tech_leader = any(p in lower for p in TECH_LEADER_PATTERNS) or bool(
        re.search(r"\bCTO\b|\bCIO\b", text[:400_000])
    )
    if has_tech_leader:
        return []
    return [Signal(
        company_cik=cik, source="edgar", type="E6",
        title="No technology leadership disclosed in latest proxy",
        detail=f"DEF 14A filed {getattr(proxy, 'filing_date', '?')} names no CTO/CIO/CDO-type officer",
        evidence_url=_filing_url(cik, proxy),
        observed_at=getattr(proxy, "filing_date", None),
        weight=_w("E6"), raw={},
    )]


# ---------- E7: recent IPO ----------

def ipo_signal(edgar_company, company: dict) -> list[Signal]:
    cik = company["cik"]
    lookback = int(SETTINGS.get("enrich", {}).get("edgar", {}).get("ipo_lookback_days", 730))
    cutoff = date.today() - timedelta(days=lookback)
    for form in ("424B4", "S-1", "8-A12B"):
        try:
            filings = list(edgar_company.get_filings(form=form))[:1]
        except Exception:
            continue
        if filings:
            fdate = getattr(filings[0], "filing_date", None)
            if fdate and fdate >= cutoff:
                return [Signal(
                    company_cik=cik, source="edgar", type="E7",
                    title=f"Recent IPO — {form} filed {fdate}",
                    detail="Newly public companies are building GTM and reporting muscle",
                    evidence_url=_filing_url(cik, filings[0]),
                    observed_at=fdate, weight=_w("E7"), raw={"form": form},
                )]
    return []


# ---------- orchestrator ----------

def collect(company: dict) -> tuple[list[Signal], list[str]]:
    """Run all EDGAR collectors for one company. Returns (signals, error notes)."""
    _init_edgar()
    from edgar import Company as EdgarCompany

    errors: list[str] = []
    try:
        ec = EdgarCompany(int(company["cik"]))
    except Exception as exc:
        return [], [f"edgar company lookup failed: {exc}"]

    signals: list[Signal] = []
    for name, fn in [
        ("ai_language", lambda: ai_language_signals(ec, company)),
        ("eightk", lambda: eightk_signals(ec, company)),
        ("financials", lambda: financial_signals(company)),
        ("leadership", lambda: leadership_gap_signal(ec, company)),
        ("ipo", lambda: ipo_signal(ec, company)),
    ]:
        try:
            signals.extend(fn())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    # one signal per type, keep the strongest
    best: dict[str, Signal] = {}
    for s in signals:
        if s.type not in best or s.weight > best[s.type].weight:
            best[s.type] = s
    return list(best.values()), errors
