"""Parallel.ai web-research signal collectors P1-P6.

One structured task run per company (cost control) covering all six areas.
Taxonomy and rationale: docs/SIGNALS.md.
"""
from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from pipeline import angles as angles_mod
from pipeline.config import SETTINGS
from pipeline.models import Angle, Signal
from pipeline.parallel_client import run_task, run_tasks_batch


def _w(sig_type: str) -> float:
    return float(SETTINGS.get("scoring", {}).get("weights", {}).get(sig_type, 5))


def _area(desc: str, extra_props: dict | None = None) -> dict:
    props = {
        "found": {"type": "boolean", "description": f"True if: {desc}"},
        "summary": {"type": "string", "description": "1-3 sentences of what was found, or why not"},
        "evidence_urls": {"type": "array", "items": {"type": "string"}, "description": "Source URLs"},
    }
    props.update(extra_props or {})
    return {"type": "object", "properties": props, "required": ["found", "summary"]}


ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "ai_job_postings": _area(
            "the company currently has (or recently had) open roles for AI/ML engineers, "
            "data scientists, or AI product roles",
            {"roles": {"type": "array", "items": {"type": "string"}}},
        ),
        "gtm_hiring": _area(
            "the company is hiring SDRs, BDRs, sales development, demand gen, or marketing roles"
        ),
        "ai_announcements": _area(
            "the company publicly announced AI initiatives, pilots, features, or partnerships "
            "in the last 18 months (press releases, news)"
        ),
        "product_ai_gap": _area(
            "the company's product/service has NO meaningful AI capabilities while direct "
            "competitors are shipping AI features"
        ),
        "martech_stack": _area(
            "evidence about the company's marketing/sales tooling maturity (CRM, marketing "
            "automation, chatbots on site, etc.)",
            {"maturity": {"type": "string", "description": "low | medium | high"}},
        ),
        "exec_ai_commentary": _area(
            "executives discussed AI plans/challenges on earnings calls or in interviews",
            {"quotes": {"type": "array", "items": {"type": "string"}, "description": "Short verbatim quotes with speaker"}},
        ),
    },
    "required": [
        "ai_job_postings", "gtm_hiring", "ai_announcements",
        "product_ai_gap", "martech_stack", "exec_ai_commentary",
    ],
    "additionalProperties": False,
}

AREA_TO_SIGNAL = {
    "ai_job_postings": ("P1", "AI/ML roles in job postings"),
    "gtm_hiring": ("P2", "Hiring SDR/BDR/marketing headcount"),
    "ai_announcements": ("P3", "Public AI announcements/initiatives"),
    "product_ai_gap": ("P4", "Product lacks AI while competitors ship it"),
    "martech_stack": ("P5", "Martech/sales stack signal"),
    "exec_ai_commentary": ("P6", "Executive AI commentary"),
}

DEEP_EXTRA_PROPS = {
    "leadership_hires": {
        "type": "array",
        "description": "C-level/VP executives who joined in the last 12 months",
        "items": {"type": "object", "properties": {
            "role": {"type": "string", "description": "Title, e.g. 'Chief Revenue Officer'"},
            "person_name": {"type": "string"},
            "start_date": {"type": "string", "description": "YYYY-MM-DD; announcement date if start unknown"},
            "mandate_quote": {"type": "string", "description": "Verbatim quote about their mandate/priorities"},
            "source_url": {"type": "string"},
        }, "required": ["role"]},
    },
    "ai_moves": {
        "type": "array",
        "description": "AI initiatives: product launches, partnerships, pilots, notable exec statements",
        "items": {"type": "object", "properties": {
            "initiative": {"type": "string", "description": "Name of the product/initiative/partnership"},
            "move_type": {"type": "string", "description": "product_launch | partnership | pilot | exec_statement"},
            "partner": {"type": "string"},
            "exec_quote": {"type": "string"},
            "announced": {"type": "string", "description": "YYYY-MM-DD"},
            "source_url": {"type": "string"},
        }, "required": ["initiative"]},
    },
    "funding_news": {
        "type": "array",
        "description": "Press coverage of capital raises in the last 12 months (context; SEC filings are the source of record)",
        "items": {"type": "object", "properties": {
            "description": {"type": "string"},
            "amount_usd": {"type": "number"},
            "instrument": {"type": "string", "description": "follow_on | atm | pipe | shelf | debt | other"},
            "announced": {"type": "string", "description": "YYYY-MM-DD"},
            "quote": {"type": "string"},
            "source_url": {"type": "string"},
        }, "required": ["description"]},
    },
}

DEEP_SCHEMA = {
    "type": "object",
    "properties": {**ENRICH_SCHEMA["properties"], **DEEP_EXTRA_PROPS},
    "required": ENRICH_SCHEMA["required"],
    "additionalProperties": False,
}


def _input_text(company: dict) -> str:
    website = f" (website: {company['website']})" if company.get("website") else ""
    return (
        f"Research {company['name']} (stock ticker {company['ticker']}), a US-listed "
        f"{company.get('sector_bucket', 'technology')} company with roughly "
        f"${(company.get('market_cap') or 0)/1e6:.0f}M market cap{website}. "
        "Focus on the last 12-18 months. Investigate: current job postings (AI/ML roles "
        "and sales/marketing roles separately), press releases and news about AI "
        "initiatives, whether their product has AI capabilities compared to direct "
        "competitors, their marketing/sales tooling maturity, and executive commentary "
        "about AI from earnings calls or interviews. Be factual; if you can't find "
        "something, say found=false rather than guessing."
    )


def _deep_input_text(company: dict) -> str:
    return _input_text(company) + (
        " ADDITIONALLY, dig for dated outreach events from the last 12 months: "
        "(1) named C-level/VP hires with exact roles, start dates, and a verbatim quote about "
        "their mandate from the announcement or an interview; "
        "(2) AI initiatives with the initiative name, type (product launch / partnership / pilot / "
        "exec statement), partner if any, announcement date, and an exec quote; "
        "(3) news coverage of capital raises (amount, instrument, date, a quote). "
        "Every item needs a date and a source URL. Omit items you cannot date."
    )


def _signals_from_result(company: dict, result: dict) -> list[Signal]:
    """Map one Parallel structured result onto P1-P6 Signal rows."""
    content = result["content"]
    basis_urls = []
    for b in result.get("basis", []):
        for c in (b.get("citations") or []):
            if c.get("url"):
                basis_urls.append(c["url"])

    signals: list[Signal] = []
    today = date.today()
    for area, (sig_type, title) in AREA_TO_SIGNAL.items():
        data = content.get(area) or {}
        if not data.get("found"):
            continue
        urls = data.get("evidence_urls") or basis_urls[:2]
        quote = None
        if area == "exec_ai_commentary" and data.get("quotes"):
            quote = data["quotes"][0][:350]
        detail = (data.get("summary") or "").strip()
        if area == "ai_job_postings" and data.get("roles"):
            detail += f" Roles: {', '.join(data['roles'][:5])}"
        if area == "martech_stack" and data.get("maturity"):
            detail += f" (maturity: {data['maturity']})"
        signals.append(Signal(
            company_cik=company["cik"], source="parallel", type=sig_type,
            title=title, detail=detail[:1000],
            evidence_url=urls[0] if urls else None,
            evidence_quote=quote, observed_at=today,
            weight=_w(sig_type), raw=data,
        ))
    return signals


def _parse_date(s) -> str | None:
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(s)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _angles_from_result(company: dict, result: dict) -> tuple[list[Angle], list[str]]:
    """Map deep-task arrays onto Angle rows. Invalid/undated items drop with a
    warning; one bad item never sinks the company."""
    content = result["content"]
    cik = int(company["cik"])
    out: list[Angle] = []
    warnings: list[str] = []

    def add(family: str, headline: str, details: dict, event_date: str | None,
            url: str | None, quote: str | None, fingerprint: str):
        if not event_date:
            warnings.append(f"{family}: dropped item with no date ({headline[:60]})")
            return
        try:
            out.append(Angle(
                company_cik=cik, family=family, headline=headline, details=details,
                evidence_url=url, evidence_quote=quote, event_date=event_date,
                source="parallel",
                strength=angles_mod.compute_strength(family, event_date, bool(quote), bool(url)),
                fingerprint=fingerprint,
            ))
        except ValidationError as exc:
            warnings.append(f"{family}: invalid item dropped ({str(exc)[:120]})")

    for item in content.get("leadership_hires") or []:
        d = _parse_date(item.get("start_date"))
        role = (item.get("role") or "").strip()
        if not role:
            warnings.append("leadership: dropped item with no role")
            continue
        name = item.get("person_name")
        add("leadership",
            f"New {role}" + (f": {name}" if name else "") + (f" (started {d})" if d else ""),
            {"role": role, "person_name": name, "start_date": d,
             "mandate_quote": item.get("mandate_quote")},
            d, item.get("source_url"), item.get("mandate_quote"),
            angles_mod.make_fingerprint("leadership", role, d))

    for item in content.get("ai_moves") or []:
        d = _parse_date(item.get("announced"))
        move_type = item.get("move_type") if item.get("move_type") in (
            "product_launch", "partnership", "pilot", "exec_statement") else "product_launch"
        add("ai_move",
            f"AI move: {item.get('initiative', '?')} ({move_type}, {d})",
            {"initiative": item.get("initiative"), "move_type": move_type,
             "partner": item.get("partner"), "exec_quote": item.get("exec_quote"), "announced": d},
            d, item.get("source_url"), item.get("exec_quote"),
            angles_mod.make_fingerprint("ai_move", item.get("initiative"), (d or "")[:7]))

    for item in content.get("funding_news") or []:
        d = _parse_date(item.get("announced"))
        instrument = item.get("instrument") if item.get("instrument") in (
            "follow_on", "atm", "pipe", "shelf", "debt", "other") else "other"
        add("funding",
            f"Funding news: {(item.get('description') or '?')[:80]} ({d})",
            {"amount_usd": item.get("amount_usd"), "instrument": instrument,
             "announced": d, "use_of_proceeds": None, "filing_type": None},
            d, item.get("source_url"), item.get("quote"),
            angles_mod.make_fingerprint("funding-news", (d or "")[:7], instrument))

    return out, warnings


def collect(company: dict) -> tuple[list[Signal], list[str]]:
    """One Parallel task run -> P1-P6 signals for one company."""
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    try:
        result = run_task(
            _input_text(company),
            ENRICH_SCHEMA,
            processor=cfg.get("processor", "base"),
            timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
        )
    except Exception as exc:
        return [], [f"parallel task failed: {type(exc).__name__}: {exc}"]
    return _signals_from_result(company, result), []


def collect_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[str]]]:
    """One Parallel task per company — created up front, polled together."""
    if not companies:
        return {}
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    results = run_tasks_batch(
        [(_input_text(c), ENRICH_SCHEMA) for c in companies],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    out: dict[int, tuple[list[Signal], list[str]]] = {}
    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            out[int(company["cik"])] = ([], [f"parallel task failed: {type(result).__name__}: {result}"])
        else:
            try:
                out[int(company["cik"])] = (_signals_from_result(company, result), [])
            except Exception as exc:
                out[int(company["cik"])] = ([], [f"parallel result parse failed: {type(exc).__name__}: {exc}"])
    return out


def collect_deep_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[Angle], list[str]]]:
    """Deep tier: one richer task per company -> (P-signals, angles, warnings)."""
    if not companies:
        return {}
    cfg = SETTINGS.get("enrich", {}).get("deep", {})
    results = run_tasks_batch(
        [(_deep_input_text(c), DEEP_SCHEMA) for c in companies],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 900)),
    )
    out: dict[int, tuple[list[Signal], list[Angle], list[str]]] = {}
    for company, result in zip(companies, results):
        cik = int(company["cik"])
        if isinstance(result, Exception):
            out[cik] = ([], [], [f"deep task failed: {type(result).__name__}: {result}"])
            continue
        try:
            sigs = _signals_from_result(company, result)
            angles_found, warnings = _angles_from_result(company, result)
            out[cik] = (sigs, angles_found, warnings)
        except Exception as exc:
            out[cik] = ([], [], [f"deep result parse failed: {type(exc).__name__}: {exc}"])
    return out
