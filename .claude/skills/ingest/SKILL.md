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

What it does: resolves each ticker against SEC's company map (CIK, name, exchange), fetches SIC + market cap, classifies the sector bucket, and upserts as status `new`. Companies outside the target sectors are still ingested (user lists override the sector screen) and tagged with their real sector so scoring can weigh fit honestly — but the L1 prescreen's hard-disqualifiers (excluded tickers/SIC, cap band, exchange/OTC, shell names) DO apply to ingest; failing companies are written as `disqualified` and only `--force` ingests them as active.

After running:
- Report resolved vs unresolved tickers (typos, delisted, foreign issuers).
- Note any outside the target sectors — they stay in, but flag it. Cap-band/exchange failures are prescreen-disqualified (see below) unless `--force`.
- Suggest `/enrich` as the next step.

Add `--dry-run` to preview without writing to Supabase.

**L1 pre-screen (v3)**: before writing, each resolved company is checked against config `prescreen:` (customer/competitor `exclude_tickers`, `exclude_sic`, exchange/OTC allowlist, shell-name patterns, cap band). Companies that fail are still written — as status `disqualified` with `dq_reason` set and `tier` `T4` — so they never re-enter the funnel and never draw EDGAR/Parallel spend. The resolved-companies table shows the DQ reason inline; pass `--force` to bypass the prescreen entirely (e.g. a known-good ticker that trips a shell-name heuristic).
