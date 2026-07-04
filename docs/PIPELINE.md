# Pipeline Runbook

## One-time setup

1. `cp .env.example .env` and fill in: `EDGAR_IDENTITY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY` (+ optionally `SUPABASE_DB_URL`, `PARALLEL_API_KEY`).
2. `uv sync`
3. Apply schema: `uv run python -m pipeline apply-schema` (needs `SUPABASE_DB_URL`)
   — or paste `sql/schema.sql` into the Supabase SQL editor once.
4. Parallel auth: `parallel-cli login` (device OAuth) or set `PARALLEL_API_KEY`.

## Normal cycle

```bash
# 1. Get companies in (either path)
uv run python -m pipeline discover --dry-run     # preview screen
uv run python -m pipeline discover               # seed micro-cap sector matches
uv run python -m pipeline ingest "TICK1,TICK2"   # or explicit list / --csv

# 2. Signals — free source first, paid second
uv run python -m pipeline enrich --source edgar --limit 10
uv run python -m pipeline enrich --source parallel --limit 10

# 3. Score (v1: Haiku subagents via Claude Code — see /score skill)
uv run python -m pipeline score --prepare
#   -> /score skill spawns Haiku subagents -> results land in data/scoring_results/
uv run python -m pipeline score --commit

# 4. Contacts for qualified accounts
uv run python -m pipeline people --limit 5

# 5. Hand-off
uv run python -m pipeline export                 # data/exports/qualified.csv
```

`status` shows the funnel any time. Single-company deep dive:
`enrich --ticker XYZ --dry-run` (works even if XYZ isn't in the DB yet).

## Status machine

`new → enriched → scored → qualified | disqualified → contacts_found`

- `scored` (between disqualify floor and qualify threshold) = human review band.
- Re-enriching is idempotent: signals are replaced per source, not duplicated.
- User-ingested companies outside band/sectors stay in — the screen only gates
  `discover`.

## Costs

- EDGAR: free. Throttled ≤8 req/s, cached in `data/cache/` (first discover run
  crawls SIC codes for the whole universe once — 15–20 min, then cached).
- Parallel: ~1 task per company per enrich, 1 per company for people. Caps:
  `enrich.parallel.max_tasks_per_run` (25), `people.max_companies_per_run` (10).
- LLM scoring: zero API cost in v1 (Claude Code Haiku subagents). v2 flips to
  OpenRouter: `score --provider openrouter` once `OPENROUTER_API_KEY` is set.

## Troubleshooting

- `Missing EDGAR_IDENTITY/SUPABASE_URL…` → .env incomplete.
- Parallel 401/403 → `parallel-cli auth --json`, re-login, or set `PARALLEL_API_KEY`.
- Parallel task timeout → processor tier busy; re-run, task cost is per-run.
- Invalid scoring results at `--commit` → the listed tickers need their Haiku
  subagent re-run (schema violation reported inline).
- yfinance cap = `?` → ticker delisted/renamed; cached for 7 days, delete
  `data/cache/market_caps.json` entry to retry sooner.
