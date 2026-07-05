"""Scoring: deterministic base score + packet handoff for LLM reasoning.

v1 flow (no LLM API cost):
  score --prepare  ->  data/scoring_queue/<TICKER>.json packets
  /score skill     ->  Claude Code Haiku subagents write data/scoring_results/<TICKER>.json
  score --commit   ->  validate, write to Supabase, qualify/disqualify

The packet is self-contained: a scorer needs nothing but the packet.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from pipeline import db
from pipeline.config import ARCHIVE_DIR, QUEUE_DIR, RESULTS_DIR, SERVICES, SETTINGS
from pipeline.models import ScoreVerdict, Status

# dated event signals lose relevance: full weight while fresh, linear decay to
# a floor at the collection-window edge (knobs in config scoring.recency)
DECAY_WINDOW_DAYS = {"E3": 365, "E4": 365, "E7": 730, "P3": 548}


def _signal_age_days(s: dict) -> int | None:
    observed = s.get("observed_at")
    if not observed:
        return None
    try:
        d = datetime.fromisoformat(str(observed)).date() if "T" in str(observed) else date.fromisoformat(str(observed)[:10])
    except ValueError:
        return None
    return max((date.today() - d).days, 0)


def effective_weight(s: dict) -> float:
    weight = float(s.get("weight") or 0)
    window = DECAY_WINDOW_DAYS.get(s.get("type", ""))
    age = _signal_age_days(s)
    if window is None or age is None:
        return weight
    rec = SETTINGS.get("scoring", {}).get("recency", {})
    full = int(rec.get("full_days", 90))
    floor = float(rec.get("floor", 0.25))
    if age <= full:
        return weight
    frac = min((age - full) / max(window - full, 1), 1.0)
    return round(weight * (1.0 - frac * (1.0 - floor)), 2)


# which component each signal type feeds (caps in config scoring.component_caps)
COMPONENT_OF = {
    "E1": "intent", "E2": "intent", "P3": "intent", "P6": "intent",
    "E6": "capability_gap", "P4": "capability_gap",
    "E3": "timing", "E4": "timing", "E7": "timing",
    "E5": "commercial_fit", "E9": "commercial_fit",
    "P1": "commercial_fit", "P2": "commercial_fit", "P5": "commercial_fit",
}

RUBRIC = """Score this company as a prospect for martechs.io's AI services (0-100 total).

Components (respect the max for each):
- intent (0-30): stated AI interest/urgency. Filings language in strategy sections
  beats risk-factor boilerplate; exec commentary and public announcements count.
- capability_gap (0-25): how much they LACK internal AI capability — no tech
  leadership, no AI hires, no AI in product. A company with AI engineers and
  shipped AI features has a small gap.
- timing (0-25): open buying window — new executive, restructuring/cost mandate,
  recent IPO. RECENCY IS THE POINT: every dated signal carries age_days and a
  pre-decayed effective_weight — score from those, not the raw weight. An event
  ≤90 days old is a hot window; 6 months is cooling; if the newest dated event
  is >180 days old, timing must be ≤8.
- commercial_fit (0-20): would they buy outside services — GTM inefficiency
  (S&M rising, growth slowing), cash to spend, hiring in sales/marketing,
  right size/sector for a services engagement.

Profile classification:
- "laggard": talks about AI (or is conspicuously silent while peers act) but shows
  no execution — no AI hires, no AI product features. Lead with consultation.
- "adopter": visibly investing (AI job postings, launched AI initiatives). Lead
  with specialist services (lead-gen, outreach, custom agents).
- "hybrid": clear intent + early scattered execution. "unclear": weak evidence.

service_fit: rank 1-3 services from the catalog that best match the signals, each
with priority (1 = lead pitch) and a one-sentence rationale tied to evidence.
The `service` field must be the catalog KEY exactly (e.g. "ai_consultation",
"custom_ai_agents"), not the display name.

reasoning: 3-6 sentences. MUST cite specific evidence from the packet (quote
fragments, filing dates, URLs). Never invent facts not present in the packet.
The base_score is deterministic signal math — you may deviate from it when the
evidence justifies it, and should explain when you do.

why_now: 1-3 sentences — the outreach thesis. Name the FRESHEST dated evidence
(signal type + date + what it says) and the concrete window it opens ("CFO
appointed 2026-05-12 — first-100-days agenda"). If no dated evidence is under
180 days old, say exactly that ("no fresh timing event; thesis rests on
structural gap X") — do not manufacture urgency.
"""


def base_components(signals: list[dict]) -> dict:
    caps = SETTINGS.get("scoring", {}).get("component_caps", {})
    totals = {"intent": 0.0, "capability_gap": 0.0, "timing": 0.0, "commercial_fit": 0.0}
    for s in signals:
        comp = COMPONENT_OF.get(s["type"])
        if comp:
            totals[comp] += effective_weight(s)
    for comp, cap in caps.items():
        if comp in totals:
            totals[comp] = min(totals[comp], float(cap))
    totals = {k: round(v, 1) for k, v in totals.items()}
    totals["total"] = round(sum(totals.values()), 1)
    return totals


def _derived_cohort_signal(
    company: dict, signals: list[dict], peers: list[dict], signals_by_cik: dict[int, list[dict]]
) -> dict | None:
    """E8 peer-laggard note, computed from our own enriched cohort in the DB."""
    sector = company.get("sector_bucket")
    cohort = [p for p in peers if p.get("sector_bucket") == sector and p["cik"] != company["cik"]]
    if len(cohort) < 5:
        return None
    has_ai_lang = any(s["type"] in ("E1", "E2") for s in signals)
    if has_ai_lang:
        return None
    with_ai = sum(
        1 for p in cohort
        if any(s["type"] in ("E1", "E2", "P3") for s in signals_by_cik.get(int(p["cik"]), []))
    )
    share = with_ai / len(cohort)
    if share >= 0.4:
        return {
            "type": "E8", "source": "derived",
            "title": "Sector peer laggard: no AI language while peers discuss it",
            "detail": f"{with_ai}/{len(cohort)} enriched {sector} peers show AI signals; this company shows none",
            "weight": float(SETTINGS.get("scoring", {}).get("weights", {}).get("E8", 6)),
        }
    return None


def prepare(limit: int | None = None, statuses: tuple[str, ...] = ("enriched",)) -> list[str]:
    """Build scoring packets. Returns list of packet paths."""
    companies: list[dict] = []
    for st in statuses:
        companies.extend(db.get_companies(status=st))
    if limit:
        companies = companies[:limit]

    schema = ScoreVerdict.model_json_schema()
    shared_path = QUEUE_DIR / "_shared.json"
    shared_path.write_text(json.dumps({
        "services_catalog": SERVICES,
        "rubric": RUBRIC,
        "output_schema": schema,
        "instructions": (
            "This file is identical for every packet in the queue — read it ONCE. "
            "For each packet: score the company against `rubric` using "
            "`services_catalog`, and write a verdict JSON matching `output_schema` "
            "EXACTLY to the packet's `output_path`."
        ),
    }, indent=2, default=str))

    peers = db.get_companies()
    signals_by_cik = db.all_signals()
    written: list[str] = []
    for company in companies:
        signals = signals_by_cik.get(int(company["cik"]), [])
        slim_signals = [
            {k: s.get(k) for k in ("type", "source", "title", "detail", "evidence_url", "evidence_quote", "observed_at", "weight")}
            for s in signals
        ]
        for s in slim_signals:
            s["age_days"] = _signal_age_days(s)
            s["effective_weight"] = effective_weight(s)
        derived = _derived_cohort_signal(company, slim_signals, peers, signals_by_cik)
        if derived:
            slim_signals.append(derived)
        output_path = (RESULTS_DIR / (company["ticker"] + ".json")).as_posix()
        packet = {
            "ticker": company["ticker"],
            "company": {
                k: company.get(k)
                for k in ("cik", "ticker", "name", "exchange", "sector_bucket", "market_cap", "sic_description", "website", "hq_state")
            },
            "signals": slim_signals,
            "base_score": base_components(slim_signals),
            "hard_signals_present": sorted(
                {s["type"] for s in slim_signals}
                & set(SETTINGS.get("scoring", {}).get("hard_signals", []))
            ),
            "shared_file": shared_path.as_posix(),
            "output_path": output_path,
            "instructions": (
                f"First read {shared_path.as_posix()} ONCE per batch — it holds the "
                f"rubric, services_catalog, and output_schema shared by every packet. "
                f"Then write your verdict as JSON matching output_schema EXACTLY to: "
                f"{output_path} . Component scores are integers within their maximums. "
                "reasoning must cite packet evidence. Do not add fields. Do not wrap "
                "in markdown."
            ),
        }
        path = QUEUE_DIR / f"{company['ticker']}.json"
        path.write_text(json.dumps(packet, indent=2, default=str))
        written.append(str(path))
    return written


def commit(run_id: str | None = None) -> dict:
    """Validate results, write scores, transition statuses. Returns summary."""
    cfg = SETTINGS.get("scoring", {})
    threshold = float(cfg.get("qualify_threshold", 65))
    floor = float(cfg.get("disqualify_below", 45))
    hard = set(cfg.get("hard_signals", []))
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")

    summary = {"qualified": [], "review": [], "disqualified": [], "invalid": [], "orphan": []}
    archive = ARCHIVE_DIR / run_id
    archive.mkdir(parents=True, exist_ok=True)

    shared_path = QUEUE_DIR / "_shared.json"
    if shared_path.exists():
        shutil.copy2(shared_path, archive / "_shared.json")

    for result_file in sorted(RESULTS_DIR.glob("*.json")):
        ticker = result_file.stem.upper()
        packet_file = QUEUE_DIR / f"{ticker}.json"
        if not packet_file.exists():
            summary["orphan"].append(ticker)
            continue
        try:
            verdict = ScoreVerdict.model_validate_json(result_file.read_text())
        except (ValidationError, json.JSONDecodeError) as exc:
            summary["invalid"].append(f"{ticker}: {str(exc)[:200]}")
            continue

        packet = json.loads(packet_file.read_text())
        company = db.get_company_by_ticker(ticker)
        if company is None:
            summary["orphan"].append(ticker)
            continue

        signal_types = {s["type"] for s in packet["signals"]}
        has_hard = bool(signal_types & hard)
        total = verdict.total

        db.insert_score({
            "company_cik": company["cik"],
            "run_id": run_id,
            "base_score": packet["base_score"]["total"],
            "intent": verdict.intent,
            "capability_gap": verdict.capability_gap,
            "timing": verdict.timing,
            "commercial_fit": verdict.commercial_fit,
            "total": total,
            "profile": verdict.profile.value,
            "service_fit": [sf.model_dump() for sf in verdict.service_fit],
            "reasoning": verdict.reasoning,
            "why_now": verdict.why_now,
            "evidence_cited": verdict.evidence_cited,
            "confidence": verdict.confidence,
            "model": "claude-code/haiku-subagent",
        })

        if total >= threshold and has_hard:
            new_status, bucket = Status.qualified, "qualified"
        elif total < floor:
            new_status, bucket = Status.disqualified, "disqualified"
        else:
            new_status, bucket = Status.scored, "review"
        db.set_status(company["cik"], new_status, profile=verdict.profile.value)
        summary[bucket].append({"ticker": ticker, "total": total, "profile": verdict.profile.value})

        shutil.move(str(result_file), archive / result_file.name)
        shutil.move(str(packet_file), archive / f"packet_{ticker}.json")

    if shared_path.exists() and not pending_queue():
        shared_path.unlink()  # queue fully drained — next prepare rewrites it

    return summary


def pending_queue() -> list[Path]:
    return sorted(p for p in QUEUE_DIR.glob("*.json") if not p.name.startswith("_"))


def pending_results() -> list[Path]:
    return sorted(RESULTS_DIR.glob("*.json"))
