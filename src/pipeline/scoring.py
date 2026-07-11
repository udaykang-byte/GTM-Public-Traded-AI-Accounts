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
import re
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

# packets carry a citable snippet, not the filing paragraph — quotes past this
# length are token bloat the scorer never needs (rubric asks for citations, and
# a 200-char snippet is plenty to quote from)
MAX_QUOTE_CHARS = 200

# replicate results from the median-of-3 pass: <output_path>.run1.json etc.
_RUN_SUFFIX_RE = re.compile(r"\.run\d+$", re.IGNORECASE)


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
    "E6": "capability_gap", "P4": "capability_gap", "E8": "capability_gap",
    "E3": "timing", "E4": "timing", "E7": "timing",
    "E5": "commercial_fit", "E9": "commercial_fit",
    "P1": "commercial_fit", "P2": "commercial_fit", "P5": "commercial_fit",
}

# Parallel-sourced hard signals are web research, not filings — they satisfy
# the hard-signal gate only with a source URL and substantive detail. EDGAR
# hard signals are filing-derived and exempt. (All 123 archived P1/P2/P3
# already pass this floor; it guards future collector regressions.)
HARD_EVIDENCE_TYPES = {"P1", "P2", "P3"}
HARD_EVIDENCE_MIN_DETAIL = 40


def hard_types_present(signals: list[dict], hard: set[str]) -> set[str]:
    out = set()
    for s in signals:
        t = s.get("type")
        if t not in hard:
            continue
        if t in HARD_EVIDENCE_TYPES and not (
            s.get("evidence_url")
            and len((s.get("detail") or "").strip()) >= HARD_EVIDENCE_MIN_DETAIL
        ):
            continue
        out.add(t)
    return out

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
  ≤90 days old is a hot window; 6 months is cooling. The packet's
  `timing_ceiling` (the decayed timing math plus small headroom) is a HARD
  cap: timing must be ≤ timing_ceiling. Never manufacture urgency from
  undated evidence.
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


def pre_gate(base_total: float, has_hard: bool, cfg: dict | None = None) -> str | None:
    """Deterministic L2 gate: return the reason a company cannot qualify no
    matter what the LLM says — "no_hard_signal", or "base_below_reach" when
    base_total + max_llm_lift stays under qualify_threshold — else None
    (LLM scoring needed). Only ever blocks qualification; the review/DQ split
    for gated companies comes from the synthetic verdict total vs the floor."""
    cfg = cfg if cfg is not None else SETTINGS.get("scoring", {}).get("pre_gate", {})
    if not cfg.get("enabled", False):
        return None
    if not has_hard:
        return "no_hard_signal"
    threshold = float(SETTINGS.get("scoring", {}).get("qualify_threshold", 65))
    lift = float(cfg.get("max_llm_lift", 40))
    if base_total + lift < threshold:
        return "base_below_reach"
    return None


def pregate_verdict(ticker: str, base: dict, reason: str) -> dict:
    """Synthetic ScoreVerdict-shaped dict for a pre-gated company: component
    scores are the capped deterministic base components (stacking bonus is
    base-score-only and excluded, matching the verdict schema's component sum).
    The extra `pregate` key survives schema validation (extras are ignored)
    and lets commit() label the score row's model honestly."""
    maxes = {"intent": 30, "capability_gap": 25, "timing": 25, "commercial_fit": 20}
    comps = {k: min(int(round(float(base.get(k, 0)))), m) for k, m in maxes.items()}
    explain = {
        "no_hard_signal": "no hard signal present, so it cannot qualify",
        "base_below_reach": (
            f"base score {base.get('total')} cannot reach the qualify threshold "
            "even with the maximum observed LLM lift"
        ),
    }[reason]
    return {
        "ticker": ticker,
        **comps,
        "profile": "unclear",
        "service_fit": [],
        "reasoning": f"Pre-gated deterministically ({reason}): {explain}. Not LLM-scored.",
        "why_now": "No fresh-timing assessment — pre-gated before LLM scoring.",
        "evidence_cited": [],
        "confidence": "low",
        "angle_ranking": [],
        "primary_angle": None,
        "pregate": reason,
    }


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


def prepare(
    limit: int | None = None, statuses: tuple[str, ...] = ("enriched",)
) -> tuple[list[str], list[dict]]:
    """Build scoring packets. Returns (packet paths needing LLM scoring,
    pre-gated companies). Pre-gated companies get their packet written to the
    queue and a synthetic deterministic verdict written straight to results,
    so a plain `commit()` handles them uniformly — they just never cost a
    subagent."""
    companies: list[dict] = []
    for st in statuses:
        companies.extend(db.get_companies(status=st))
    if limit:
        companies = companies[:limit]

    schema = ScoreVerdict.model_json_schema()
    # Queue filenames carry a run stamp so every prepare produces
    # never-before-seen paths: the claude-mem read-priming hook truncates
    # Reads of any path it has prior observations for, which starves the
    # scorer subagents when packet paths repeat across runs.
    run_stamp = time.strftime("%Y%m%d-%H%M%S")
    shared_path = QUEUE_DIR / f"_shared-{run_stamp}.json"
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
    gated: list[dict] = []
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
            # packet uniformity: the synthetic E8 carries the same urgency/
            # age/effective_weight keys as collected signals (undated -> None)
            derived["age_days"] = _signal_age_days(derived)
            derived["effective_weight"] = effective_weight(derived)
            derived["urgency"] = urgency_of(derived["age_days"])
            slim_signals.append(derived)
        base_score = base_components(slim_signals)
        # one recency story: decay produces the timing math, and the LLM's
        # timing component may not exceed it plus small headroom (replaces the
        # old rubric-only ">180 days -> timing <=8" rule); commit clamps
        timing_ceiling = min(25, round(base_score["timing"]) + int(
            SETTINGS.get("scoring", {}).get("timing_ceiling_headroom", 8)))
        # after the base math is done, the raw decay inputs are packet bloat:
        # the scorer is told to use effective_weight/age_days, and it only
        # needs a citable snippet of each quote, not the filing paragraph
        for s in slim_signals:
            quote = s.get("evidence_quote")
            if quote and len(quote) > MAX_QUOTE_CHARS:
                s["evidence_quote"] = quote[:MAX_QUOTE_CHARS].rstrip() + "…"
            s.pop("observed_at", None)
            s.pop("weight", None)
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
            "base_score": base_score,
            "timing_ceiling": timing_ceiling,
            "hard_signals_present": sorted(hard_types_present(
                slim_signals, set(SETTINGS.get("scoring", {}).get("hard_signals", []))
            )),
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
        path = QUEUE_DIR / f"{company['ticker']}-{run_stamp}.json"
        path.write_text(json.dumps(packet, indent=2, default=str))
        gate_reason = pre_gate(
            packet["base_score"]["total"], bool(packet["hard_signals_present"])
        )
        if gate_reason:
            verdict = pregate_verdict(company["ticker"], packet["base_score"], gate_reason)
            Path(output_path).write_text(json.dumps(verdict, indent=2))
            total = sum(verdict[c] for c in ("intent", "capability_gap", "timing", "commercial_fit"))
            gated.append({"ticker": company["ticker"], "total": total, "reason": gate_reason})
            continue
        written.append(str(path))
    return written, gated


def commit(run_id: str | None = None) -> dict:
    """Validate results, write scores, transition statuses. Returns summary."""
    cfg = SETTINGS.get("scoring", {})
    threshold = float(cfg.get("qualify_threshold", 65))
    floor = float(cfg.get("disqualify_below", 45))
    hard = set(cfg.get("hard_signals", []))
    require_angle = bool(cfg.get("require_angle", True))
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")

    band = float(cfg.get("median_band", 8))

    summary = {"qualified": [], "review": [], "disqualified": [], "invalid": [], "orphan": [], "kept": [], "median_pending": []}
    archive = ARCHIVE_DIR / run_id
    archive.mkdir(parents=True, exist_ok=True)

    shared_files = sorted(QUEUE_DIR.glob("_shared*.json"))
    if shared_files:
        shutil.copy2(shared_files[-1], archive / "_shared.json")

    # median-of-3 is code-enforced, not skill prose: a single-shot verdict
    # within ±median_band of the qualify bar is HELD (files left in place)
    # until 3 replicate results exist (TICKER.run1/.run2/.run3.json); commit
    # then settles on the median total itself. Haiku verdicts jitter ±10-15,
    # so a borderline single shot is never trusted. Reply rates > volume.
    run_results: dict[str, list[Path]] = {}
    single_results: dict[str, Path] = {}
    for f in sorted(RESULTS_DIR.glob("*.json")):
        if _RUN_SUFFIX_RE.search(f.stem):
            run_results.setdefault(_RUN_SUFFIX_RE.sub("", f.stem).upper(), []).append(f)
        else:
            single_results[f.stem.upper()] = f

    for ticker in sorted(set(single_results) | set(run_results)):
        # queue filenames are run-stamped (TICKER-<stamp>.json) to defeat
        # read-priming; newest stamp wins, bare TICKER.json accepted as legacy
        candidates = sorted(QUEUE_DIR.glob(f"{ticker}-*.json"))
        legacy = QUEUE_DIR / f"{ticker}.json"
        if legacy.exists():
            candidates.insert(0, legacy)
        if not candidates:
            summary["orphan"].append(ticker)
            continue
        packet_file = candidates[-1]
        packet = json.loads(packet_file.read_text())

        def _clamp_timing(v: ScoreVerdict) -> ScoreVerdict:
            # timing_ceiling is the decayed timing math + headroom (absent in
            # packets prepared before the feature) — deterministic recency
            # wins over LLM-manufactured urgency, before any band/gate math
            ceiling = packet.get("timing_ceiling")
            if ceiling is not None and v.timing > int(ceiling):
                v.timing = int(ceiling)
            return v

        replicates = run_results.get(ticker, [])
        single_file = single_results.get(ticker)
        if replicates:
            # the documented rule is median of EXACTLY run1/run2/run3 — a
            # stale .run4 or a duplicate rerun must never silently join the
            # median (and the row would still be labeled median3)
            nums = sorted(int(_RUN_SUFFIX_RE.search(f.stem).group(0)[4:]) for f in replicates)
            if len(nums) < 3 and set(nums) <= {1, 2, 3}:
                summary["median_pending"].append(f"{ticker}: {len(nums)}/3 runs")
                continue
            if nums != [1, 2, 3]:
                summary["invalid"].append(
                    f"{ticker}: replicates must be exactly .run1/.run2/.run3 "
                    f"(found: {', '.join(f'.run{n}' for n in nums)}) — remove strays and re-commit"
                )
                continue
            parsed = []
            for f in replicates:
                try:
                    parsed.append(_clamp_timing(ScoreVerdict.model_validate(json.loads(f.read_text()))))
                except (ValidationError, json.JSONDecodeError) as exc:
                    summary["invalid"].append(f"{ticker}: {f.name}: {str(exc)[:200]}")
            if len(parsed) < 3:
                continue  # bad replicate reported above — re-spawn it, then re-commit
            parsed.sort(key=lambda v: v.total)
            verdict = parsed[len(parsed) // 2]
            is_pregate = False
            model_label = "claude-code/haiku-subagent-median3"
            result_files = replicates + ([single_file] if single_file else [])
        else:
            try:
                raw = json.loads(single_file.read_text())
                verdict = _clamp_timing(ScoreVerdict.model_validate(raw))
            except (ValidationError, json.JSONDecodeError) as exc:
                summary["invalid"].append(f"{ticker}: {str(exc)[:200]}")
                continue
            is_pregate = isinstance(raw, dict) and bool(raw.get("pregate"))
            if band > 0 and not is_pregate and abs(verdict.total - threshold) <= band:
                summary["median_pending"].append(ticker)
                continue
            model_label = "deterministic/pre-gate" if is_pregate else "claude-code/haiku-subagent"
            result_files = [single_file]
        company = db.get_company_by_ticker(ticker)
        if company is None:
            summary["orphan"].append(ticker)
            continue

        has_hard = bool(hard_types_present(packet["signals"], hard))
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
            "model": model_label,
        })

        db.set_status(company["cik"], new_status, profile=verdict.profile.value, tier=tier)
        item = {"ticker": ticker, "total": total, "profile": verdict.profile.value}
        if gate_reason:
            item["gate_reason"] = gate_reason
        summary[bucket].append(item)

        for f in result_files:
            shutil.move(str(f), archive / f.name)
        shutil.move(str(packet_file), archive / f"packet_{ticker}.json")

    if not pending_queue():  # queue fully drained — next prepare rewrites shared
        for f in QUEUE_DIR.glob("_shared*.json"):
            f.unlink()

    return summary


def pending_queue() -> list[Path]:
    return sorted(p for p in QUEUE_DIR.glob("*.json") if not p.name.startswith("_"))


def pending_results() -> list[Path]:
    return sorted(RESULTS_DIR.glob("*.json"))
