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

Failure modes:
- `Missing SUPABASE_URL` → .env not configured; point the user at `.env.example`.
- Connection errors → Supabase project paused or wrong key; ask user to check dashboard.
