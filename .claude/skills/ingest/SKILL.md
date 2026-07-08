---
name: ingest
description: Add specific companies to the pipeline from tickers or a CSV the user provides. Use when the user hands you a list of companies/tickers to run through signal search.
---

# /ingest — add user-provided companies

For a pasted list of tickers:

```bash
uv run python -m pipeline ingest "TICK1,TICK2,TICK3"
```

For a CSV file (must have a `ticker` column; extra columns ignored):

```bash
uv run python -m pipeline ingest --csv "path/to/list.csv"
```

What it does: resolves each ticker against SEC's company map (CIK, name, exchange), fetches SIC + market cap, classifies the sector bucket, and upserts as status `new`. Companies outside the configured cap band or sectors are still ingested (user lists override the screen) but tagged with their real sector/cap so scoring can weigh fit honestly.

After running:
- Report resolved vs unresolved tickers (typos, delisted, foreign issuers).
- Note any that fall outside the micro-cap band or target sectors — they stay in, but flag it.
- Suggest `/enrich` as the next step.

Add `--dry-run` to preview without writing to Supabase.

**L1 pre-screen (v3)**: before writing, each resolved company is checked against config `prescreen:` (customer/competitor `exclude_tickers`, `exclude_sic`, exchange/OTC allowlist, shell-name patterns, cap band). Companies that fail are still written — as status `disqualified` with `dq_reason` set and `tier` `T4` — so they never re-enter the funnel and never draw EDGAR/Parallel spend. The resolved-companies table shows the DQ reason inline; pass `--force` to bypass the prescreen entirely (e.g. a known-good ticker that trips a shell-name heuristic).
