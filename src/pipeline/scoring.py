"""Scoring: deterministic base score + packet handoff for LLM reasoning.

v1 flow (no LLM API cost):
  score --prepare  ->  data/scoring_queue/<TICKER>.json packets
  /score skill     ->  Claude Code Haiku subagents write data/scoring_results/<TICKER>.json
  score --commit   ->  validate, write to Supabase, qualify/disqualify

Packets are slim: the shared rubric/catalog/schema live in data/scoring_queue/_shared.json (each packet's `shared_file`); a scorer needs the packet plus that shared file.
Packets carry active outreach angles; commit enforces the angle-required gate.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from pipeline import angles as angles_mod
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

RUBRIC += """
angle_ranking / primary_angle: the packet's `angles` list holds dated, structured
outreach events (families: funding, leadership, ai_move) with a pre-computed
strength. Rank ALL of them by outreach power — strength, specificity, and fit
with your service_fit — strongest first, each with a one-sentence message_hook
(the opening line a seller could use). Set primary_angle to the single best one
with why_this_angle. Copy fingerprint and family EXACTLY from the packet. If
`angles` is empty, return angle_ranking: [] and primary_angle: null — do not
invent angles from signals.
"""


def stacking_bonus(signals: list[dict], cfg: dict | None = None) -> float:
    """Bonus applied to the deterministic BASE score (never the LLM verdict)
    when evidence spans several distinct scoring components — signals stacked
    across intent/timing/capability_gap/commercial_fit are a stronger buying
    signal than the same total weight concentrated in one component."""
    cfg = cfg if cfg is not None else SETTINGS.get("scoring", {}).get("stacking", {})
    min_components = int(cfg.get("min_components", 3))
    bonus = float(cfg.get("bonus", 5))
    hit = {COMPONENT_OF[s["type"]] for s in signals if s.get("type") in COMPONENT_OF}
    return bonus if len(hit) >= min_components else 0.0


def urgency_of(age_days: int | None, cfg: dict | None = None) -> str | None:
    """SLA/urgency bucket for a signal's age — informational context riding on
    the packet for the scorer, never fed into the deterministic math. None
    when the signal has no observed_at date."""
    if age_days is None:
        return None
    cfg = cfg if cfg is not None else SETTINGS.get("scoring", {}).get("urgency", {}).get("windows", {})
    hot = int(cfg.get("hot", 30))
    warm = int(cfg.get("warm", 90))
    if age_days <= hot:
        return "hot"
    if age_days <= warm:
        return "warm"
    return "cold"


def tier_of(total: float, bucket: str, cfg: dict | None = None) -> str:
    """Pure tier classification, computed at commit time from the verdict
    total + gate bucket — tier is never an LLM output. T1 = qualified with
    total >= tiers.t1_min; T2 = qualified below that bar; T3 = review band;
    T4 = disqualified. 'kept' (already-qualified/contacts_found, protected
    from demotion) is treated the same as 'qualified'."""
    cfg = cfg if cfg is not None else SETTINGS.get("scoring", {}).get("tiers", {})
    t1_min = float(cfg.get("t1_min", 80))
    if bucket in ("qualified", "kept"):
        return "T1" if total >= t1_min else "T2"
    if bucket == "disqualified":
        return "T4"
    return "T3"  # review (or any other/unknown bucket)


def priority_score(total: float, stacking: float, max_angle_strength: float, cfg: dict | None = None) -> float:
    """Composite ordering key for /people and messages --prepare: mostly the
    verdict total, nudged by stacked evidence and the strongest fresh outreach
    angle — a ready hook beats a cold qualified account of the same score.
    Weights: scoring.priority.{total_weight,stacking_weight,angle_strength_weight}."""
    cfg = cfg if cfg is not None else SETTINGS.get("scoring", {}).get("priority", {})
    tw = float(cfg.get("total_weight", 1.0))
    sw = float(cfg.get("stacking_weight", 1.0))
    aw = float(cfg.get("angle_strength_weight", 10.0))
    return round(total * tw + stacking * sw + max_angle_strength * aw, 2)


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
    bonus = stacking_bonus(signals)
    totals["stacking_bonus"] = bonus
    totals["total"] = round(
        sum(totals[c] for c in ("intent", "capability_gap", "timing", "commercial_fit")) + bonus, 1
    )
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
    angles_by_cik = db.all_angles()
    stale_ids = [
        a["id"]
        for rows in angles_by_cik.values()
        for a in rows
        if a.get("id") is not None and a.get("status") == "active"
        and not angles_mod.is_fresh(a["family"], a["event_date"])
    ]
    db.mark_angles_stale(stale_ids)
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
            s["urgency"] = urgency_of(s["age_days"])
        derived = _derived_cohort_signal(company, slim_signals, peers, signals_by_cik)
        if derived:
            # packet uniformity: the synthetic E8 carries the same urgency key
            # as collected signals (undated -> None)
            derived["urgency"] = urgency_of(_signal_age_days(derived))
            slim_signals.append(derived)
        active_angles = [
            angles_mod.slim(a)
            for a in angles_by_cik.get(int(company["cik"]), [])
            if angles_mod.is_fresh(a["family"], a["event_date"])
        ]
        active_angles.sort(key=lambda a: -(a.get("strength") or 0))
        output_path = (RESULTS_DIR / (company["ticker"] + ".json")).as_posix()
        packet = {
            "ticker": company["ticker"],
            "company": {
                k: company.get(k)
                for k in ("cik", "ticker", "name", "exchange", "sector_bucket", "market_cap", "sic_description", "website", "hq_state")
            },
            "signals": slim_signals,
            "angles": active_angles,
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
    require_angle = bool(cfg.get("require_angle", True))
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")

    summary = {"qualified": [], "review": [], "disqualified": [], "invalid": [], "orphan": [], "kept": []}
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

        packet_fps = {a["fingerprint"] for a in packet.get("angles", [])}
        if verdict.primary_angle and verdict.primary_angle.fingerprint not in packet_fps:
            verdict.primary_angle = None
        verdict.angle_ranking = [r for r in verdict.angle_ranking if r.fingerprint in packet_fps]
        has_angle = bool(packet.get("angles"))

        gate_reason = ""
        if total >= threshold and has_hard and (has_angle or not require_angle):
            new_status, bucket = Status.qualified, "qualified"
        elif total < floor:
            new_status, bucket = Status.disqualified, "disqualified"
        else:
            new_status, bucket = Status.scored, "review"
            if total >= threshold and has_hard and not has_angle:
                gate_reason = "no_active_angle"

        # tightened gate never demotes accounts already past qualification;
        # gate_reason is scoped to the review band, so a kept account never
        # carries a stale "no_active_angle" into its summary item or scores row
        if company.get("status") in ("qualified", "contacts_found"):
            new_status, bucket = Status(company["status"]), "kept"
            gate_reason = ""

        # tier/priority are computed here, never LLM outputs — base_stacking
        # reads defensively since packets prepared before this feature shipped
        # have no stacking_bonus key
        tier = tier_of(total, bucket)
        base_stacking = float((packet.get("base_score") or {}).get("stacking_bonus", 0) or 0)
        angles = packet.get("angles") or []
        max_angle_strength = max((a.get("strength") or 0) for a in angles) if angles else 0.0
        priority = priority_score(total, base_stacking, max_angle_strength)

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
            "angle_ranking": [r.model_dump(mode="json") for r in verdict.angle_ranking],
            "primary_angle": verdict.primary_angle.model_dump(mode="json") if verdict.primary_angle else None,
            "gate_reason": gate_reason,
            "tier": tier,
            "priority": priority,
            "model": "claude-code/haiku-subagent",
        })

        db.set_status(company["cik"], new_status, profile=verdict.profile.value, tier=tier)
        item = {"ticker": ticker, "total": total, "profile": verdict.profile.value}
        if gate_reason:
            item["gate_reason"] = gate_reason
        summary[bucket].append(item)

        shutil.move(str(result_file), archive / result_file.name)
        shutil.move(str(packet_file), archive / f"packet_{ticker}.json")

    if shared_path.exists() and not pending_queue():
        shared_path.unlink()  # queue fully drained — next prepare rewrites it

    return summary


def pending_queue() -> list[Path]:
    return sorted(p for p in QUEUE_DIR.glob("*.json") if not p.name.startswith("_"))


def pending_results() -> list[Path]:
    return sorted(RESULTS_DIR.glob("*.json"))
