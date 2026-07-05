"""Parallel.ai web-research signal collectors P1-P6.

One structured task run per company (cost control) covering all six areas.
Taxonomy and rationale: docs/SIGNALS.md.
"""
from __future__ import annotations

from datetime import date

from pipeline.config import SETTINGS
from pipeline.models import Signal
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
