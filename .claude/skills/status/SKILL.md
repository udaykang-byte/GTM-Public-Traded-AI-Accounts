---
name: status
description: Show the AIPT funnel — company counts per stage, recent qualifications, run history. Use when asked "where are we", "pipeline status", or before deciding which stage to run next.
---

# /status — pipeline funnel report

Run:

```bash
uv run python -m pipeline status
```

This prints counts per status (`new / enriched / scored / qualified / disqualified / contacts_found`), the most recent qualified companies with total scores, and the last few runs.

Then summarize for the user:
- Funnel shape and where the bottleneck is (e.g., "42 enriched but only 5 scored → run /score next").
- Any recently qualified companies worth attention (name, ticker, score, top service fit).
- Suggest the single next command.
- The **review band** (status `scored`: total between `disqualify_below` and `qualify_threshold`) is a human decision queue — when it's non-empty, list its companies with scores and ask whether any should be promoted.
- Promote review-band companies the user approves with `uv run python -m pipeline promote TICK1,TICK2` (this is the human review-band decision — never promote without the user saying so).

Failure modes:
- `Missing SUPABASE_URL` → .env not configured; point the user at `.env.example`.
- Connection errors → Supabase project paused or wrong key; ask user to check dashboard.

## Outcome analytics (v3 phase 4)

`uv run python -m pipeline status --analytics` adds funnel conversion (rate vs previous stage), avg time-in-stage (labelled approximate for rows predating `companies.status_changed_at`), and — once outcomes have been recorded — a sent → replied → positive_reply → meeting funnel with a benchmark-band callout on positive reply rate (the north star metric), plus attribution tables by archetype / angle family / service. Every section guards against thin samples by printing "insufficient data" below `analytics.min_sends_for_attribution` sends, and degrades cleanly (same PGRST205 pattern as the tier breakdown) on a database that hasn't run `apply-schema` for this feature yet.

Outcomes get recorded with `uv run python -m pipeline outcome <message_id> --event replied [--date YYYY-MM-DD] [--note "..."]` (events: `approved/rejected/exported/sent/bounced/replied/positive_reply/meeting/opt_out`; append-only — status only ever advances forward, never backward, and never past a terminal state like `bounced`/`opted_out`). Use `--csv path` for batch imports (columns `message_id,event,date,note`), or `--ticker X --contact "name"` as a fuzzy lookup when the message_id isn't on hand — ambiguous matches print candidates and exit nonzero rather than guessing.
