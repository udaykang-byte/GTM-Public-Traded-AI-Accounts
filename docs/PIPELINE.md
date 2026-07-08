# Pipeline Runbook

## One-time setup

1. `cp .env.example .env` and fill in: `EDGAR_IDENTITY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY` (+ optionally `SUPABASE_DB_URL`, `PARALLEL_API_KEY`).
2. `uv sync`
3. Apply schema: `uv run python -m pipeline apply-schema` (needs `SUPABASE_DB_URL`)
   — or paste `sql/schema.sql` into the Supabase SQL editor once.
4. Parallel auth: `parallel-cli login` (device OAuth) or set `PARALLEL_API_KEY`.

### Upgrading to outreach-messages

Existing installs must re-run `uv run python -m pipeline apply-schema` after
pulling the outreach-messages feature — it adds the `messages` table, and
`messages --commit` fails without it.

### Upgrading to outreach-angles

Existing installs must re-run `uv run python -m pipeline apply-schema` after
pulling the outreach-angles feature — it adds the new `angles` table and new
`scores` columns, and `score --commit` fails without them. Discard and
re-prepare any scoring-queue packets built before the upgrade: pre-upgrade
packets have no `angles` key and will be gate-blocked as `no_active_angle`.

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

# 3b. Deep tier — richer evidence + outreach angles for review-band/qualified
uv run python -m pipeline enrich --source deep --dry-run   # preview selection
uv run python -m pipeline enrich --source deep --limit 15  # capped, paid
uv run python -m pipeline score --prepare --statuses scored  # rescore with angles
#   -> /score skill -> score --commit

# 4. Contacts for qualified accounts
uv run python -m pipeline people --limit 5

# 5. Outreach drafts (v2: Haiku subagents via Claude Code — see /outreach skill)
uv run python -m pipeline messages --prepare     # one packet per contact
#   -> /outreach skill spawns Haiku subagents -> results land in data/message_results/
uv run python -m pipeline messages --commit      # QA gate -> messages table (drafts)

# 6. Hand-off
uv run python -m pipeline export --messages      # qualified.csv + messages.csv
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
- Deep tier: 1 Parallel task per company, capped at enrich.deep.max_tasks_per_run (15).
- LLM scoring: zero API cost in v1 (Claude Code Haiku subagents). v2 flips to
  OpenRouter: `score --provider openrouter` once `OPENROUTER_API_KEY` is set.
- Message drafting: zero API cost (same Haiku-subagent mechanism, /outreach skill);
  capped at `messages.max_per_run` contact packets per prepare. Copy rules live in
  `config/outbound_copywriter.md`; deterministic QA (banned words, subject shape,
  packet-facts-only checks) runs at `messages --commit`. Companies without a fresh
  angle are skipped — never message on a stale hook.

## Troubleshooting

- `Missing EDGAR_IDENTITY/SUPABASE_URL…` → .env incomplete.
- Parallel 401/403 → `parallel-cli auth --json`, re-login, or set `PARALLEL_API_KEY`.
- Parallel task timeout → processor tier busy; re-run, task cost is per-run.
- Invalid scoring results at `--commit` → the listed tickers need their Haiku
  subagent re-run (schema violation reported inline).
- yfinance cap = `?` → ticker delisted/renamed; cached for 7 days, delete
  `data/cache/market_caps.json` entry to retry sooner.
