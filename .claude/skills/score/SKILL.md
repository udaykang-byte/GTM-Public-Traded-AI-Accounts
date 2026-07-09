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

**Deterministic pre-gate (v3.1)**: `--prepare` skips LLM scoring for companies whose verdict cannot change the outcome — no hard signal, or base score + `scoring.pre_gate.max_llm_lift` below the qualify threshold. Gated companies get a synthetic deterministic verdict written straight to `data/scoring_results/` (labeled `deterministic/pre-gate` in the DB) and are listed in the `pre-gated N` line; a plain `--commit` handles them. **Only spawn subagents for the packet paths `--prepare` prints** — pre-gated packets already have their verdict.

**v2 note**: Packets may also carry an `angles` list (dated, structured outreach events from deep enrichment). The subagent verdict must return `angle_ranking` (fingerprint/family/message_hook) and `primary_angle` (fingerprint/family/why_this_angle) per the packet's `output_schema` — copy fingerprints exactly from the packet, use `null` or `[]` when no angles are present.

## Step 2 — spawn lean scorer subagents

Take the packet paths printed by `--prepare` (pre-gated packets are excluded —
never score those; their verdicts already exist) and spawn **Agent tool
subagents with `subagent_type: scorer`** (the lean agent in
`.claude/agents/scorer.md` — Read+Write only, haiku, no repo baggage), giving
each subagent a batch of **up to 5 packet file paths**. Spawn batches in
parallel (single message, multiple Agent calls). The scorer agent carries the
scoring contract in its own system prompt, so the task prompt is just:

> Score these packets. Packets:
> <absolute paths, one per line>

Rules:
- `subagent_type: scorer` always — it pins haiku and the read-once/write-once
  contract. Never use opus/sonnet or a general-purpose agent for bulk scoring.
- Don't score packets yourself in the main conversation (burns expensive context); delegate to subagents.
- If a subagent fails on a packet, re-spawn just that packet.
- ALWAYS batch: one packet per agent wastes the agent's fixed overhead. Fewer,
  fuller agents beat many small ones.

## Step 3 — commit + qualify

```bash
uv run python -m pipeline score --commit
```

Validates every result against the schema (invalid ones are reported — re-run their subagent), writes scores to Supabase, sets `profile`, and transitions status: `qualified` (total ≥ threshold AND ≥1 hard signal AND ≥1 active angle in v2), `disqualified` (total < floor), else stays `scored` for human review. Processed files are archived to `data/scoring_archive/`.

**v2 output**: `--commit` now includes a `kept` bucket (already-qualified/contacts_found companies, never demoted) and `[no_active_angle]` markers for companies blocked by the angle gate. Export now includes angle columns: `angle_ready`, `angle_family`, `primary_angle`, `message_hook`.

**Rescoring the review band**: After running `/enrich --source deep`, prepare packets with `score --prepare --statuses scored` to re-score companies in the review band with angles.

## Median-of-3 for borderline verdicts (codified)

Haiku verdicts jitter ±10–15 points. After `--commit`, any company whose total
lands within ±`scoring.median_band` (config, default 8) of
`scoring.qualify_threshold` is **borderline** and must be settled by median-of-3:

1. Collect ALL borderline tickers from the commit summary (qualified or review).
2. Re-prepare their packets (`score --prepare --statuses scored` — or
   `--statuses scored,qualified` if a borderline one qualified).
3. Spawn **3 scorer subagents in parallel; each replicate scores ALL borderline
   packets** (independence comes from separate agents, amortization from
   batching). Tell each to write to `output_path` with a distinct suffix
   (`.run1.json`, `.run2.json`, `.run3.json`).
4. For each ticker, keep the verdict file whose total is the median of the
   three, rename it to the packet's real `output_path`, delete the other two,
   then `score --commit`.

Never re-roll to pass, never cherry-pick the highest — the median is the
verdict. Reply rates > volume.

## Step 4 — report

Summarize: newly qualified (ticker, total, profile, top service fit, one-line reasoning), the review band, disqualified count. Suggest `/people` for qualified accounts. Never change thresholds yourself — propose to the user (`config/settings.yaml` → `scoring`).

**Tiering + priority (v3)**: `--commit` now computes a `tier` for every processed company — T1 (total ≥ `scoring.tiers.t1_min`, default 80, AND qualified), T2 (qualified below that bar), T3 (review band), T4 (disqualified) — stored on both the `scores` row and the company's `tier` column. It also stores a `priority` composite (verdict total + a deterministic-base-score stacking bonus for evidence spanning ≥3 components + the strongest fresh outreach angle). `/people` and `messages --prepare` process companies in `(tier asc, priority desc)` order, so the strongest accounts get worked first when a per-run cap bites.
