---
name: score
description: Score enriched companies — deterministic base score plus LLM reasoning via cheap Haiku subagents (no API cost) — then qualify/disqualify. Use after /enrich, or when asked to score, rank, or qualify accounts.
---

# /score — score + qualify (v1: Haiku subagents, zero API cost)

## Step 1 — prepare packets

```bash
uv run python -m pipeline score --prepare
```

Writes one JSON packet per company to `data/scoring_queue/` (signals + evidence + base-score math + service catalog + rubric + required output schema). Prints how many packets are queued.

**v2 note**: Packets may also carry an `angles` list (dated, structured outreach events from deep enrichment). The subagent verdict must return `angle_ranking` (fingerprint/family/message_hook) and `primary_angle` (fingerprint/family/why_this_angle) per the packet's `output_schema` — copy fingerprints exactly from the packet, use `null` or `[]` when no angles are present.

## Step 2 — spawn Haiku subagents to reason

List the queued packets (`data/scoring_queue/*.json` — ignore `_shared.json`, it is
the shared rubric/catalog/schema, not a packet), then spawn **Agent tool subagents
with `model: haiku`**, giving each subagent a batch of **up to 5 packet file paths**.
Spawn batches in parallel (single message, multiple Agent calls). Each subagent
prompt must say:

> You are a B2B account scorer for an AI-services company. First read
> `data/scoring_queue/_shared.json` ONCE — it holds the rubric, services catalog, and
> required output schema shared by every packet. Then for EACH packet file listed
> below: (1) Read the JSON packet. (2) Follow the shared `rubric` and the packet's
> `instructions` exactly. (3) Write your verdict as JSON to the packet's
> `output_path` (match `output_schema` exactly; component scores must respect their
> max values; `reasoning` must cite specific evidence quotes/URLs from the packet;
> never invent facts not in the packet). Process every packet. Reply only with a
> one-line summary per ticker: `TICKER total profile`.
>
> Packets: <absolute paths>

Rules:
- `model: haiku` always — this is the "lower model to save costs" mechanism; never use opus/sonnet for bulk scoring.
- Don't score packets yourself in the main conversation (burns expensive context); delegate to subagents.
- If a subagent fails on a packet, re-spawn just that packet.

## Step 3 — commit + qualify

```bash
uv run python -m pipeline score --commit
```

Validates every result against the schema (invalid ones are reported — re-run their subagent), writes scores to Supabase, sets `profile`, and transitions status: `qualified` (total ≥ threshold AND ≥1 hard signal AND ≥1 active angle in v2), `disqualified` (total < floor), else stays `scored` for human review. Processed files are archived to `data/scoring_archive/`.

**v2 output**: `--commit` now includes a `kept` bucket (already-qualified/contacts_found companies, never demoted) and `[no_active_angle]` markers for companies blocked by the angle gate. Export now includes angle columns: `angle_ready`, `angle_family`, `primary_angle`, `message_hook`.

**Rescoring the review band**: After running `/enrich --source deep`, prepare packets with `score --prepare --statuses scored` to re-score companies in the review band with angles.

## Step 4 — report

Summarize: newly qualified (ticker, total, profile, top service fit, one-line reasoning), the review band, disqualified count. Suggest `/people` for qualified accounts. Never change thresholds yourself — propose to the user (`config/settings.yaml` → `scoring`).

**Tiering + priority (v3)**: `--commit` now computes a `tier` for every processed company — T1 (total ≥ `scoring.tiers.t1_min`, default 80, AND qualified), T2 (qualified below that bar), T3 (review band), T4 (disqualified) — stored on both the `scores` row and the company's `tier` column. It also stores a `priority` composite (verdict total + a deterministic-base-score stacking bonus for evidence spanning ≥3 components + the strongest fresh outreach angle). `/people` and `messages --prepare` process companies in `(tier asc, priority desc)` order, so the strongest accounts get worked first when a per-run cap bites.
