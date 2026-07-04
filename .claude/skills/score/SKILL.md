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

## Step 2 — spawn Haiku subagents to reason

List the queued packets, then spawn **Agent tool subagents with `model: haiku`**, giving each subagent a batch of **up to 5 packet file paths**. Spawn batches in parallel (single message, multiple Agent calls). Each subagent prompt must say:

> You are a B2B account scorer for an AI-services company. For EACH packet file listed below: (1) Read the JSON packet. (2) Follow the `rubric` and `instructions` inside it exactly. (3) Write your verdict as JSON to `data/scoring_results/<ticker>.json` (the packet tells you the exact output path and JSON schema — match it exactly; component scores must respect their max values; `reasoning` must cite specific evidence quotes/URLs from the packet; never invent facts not in the packet). Process every packet. Reply only with a one-line summary per ticker: `TICKER total profile`.
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

Validates every result against the schema (invalid ones are reported — re-run their subagent), writes scores to Supabase, sets `profile`, and transitions status: `qualified` (total ≥ threshold AND ≥1 hard signal), `disqualified` (total < floor), else stays `scored` for human review. Processed files are archived to `data/scoring_archive/`.

## Step 4 — report

Summarize: newly qualified (ticker, total, profile, top service fit, one-line reasoning), the review band, disqualified count. Suggest `/people` for qualified accounts. Never change thresholds yourself — propose to the user (`config/settings.yaml` → `scoring`).
