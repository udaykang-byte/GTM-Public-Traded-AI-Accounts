# AIPT — AI-Ready Public Companies Pipeline (martechs.io)

Finds US-listed micro-cap companies (Fintech, Edtech, Healthcare, SaaS) showing
signals they need AI services, scores them with cited reasoning, and surfaces
decision-maker contacts. State lives in Supabase; signal sources are SEC EDGAR
(edgartools) and Parallel.ai web research.

## Funnel

`new → enriched → scored → qualified | disqualified → contacts_found`
(status column on `companies`; between disqualify_below and qualify_threshold a
company stays `scored` = human review band)

## Commands (always via uv)

```bash
uv run python -m pipeline status            # funnel counts (--brief for one line)
uv run python -m pipeline discover          # screen SEC universe -> seed companies
uv run python -m pipeline ingest AAPL,TWLO  # or --csv path (ticker column)
uv run python -m pipeline enrich --source edgar --limit 10
uv run python -m pipeline enrich --source parallel --limit 10
uv run python -m pipeline score --prepare   # packets -> data/scoring_queue/
uv run python -m pipeline score --commit    # verdicts -> Supabase + qualify
uv run python -m pipeline people --limit 5  # contacts for qualified accounts
uv run python -m pipeline export            # qualified accounts + contacts CSV
```

Skills exist for each stage: /ingest /discover /enrich /score /people /status.
Prefer them — they encode the correct orchestration (especially /score).

## Rules

- **Paths**: this directory contains a space (`AI_Public Traded`) — always quote
  paths in shell commands.
- **Secrets**: live only in `.env` (gitignored). Never commit, print, echo, or
  paste key values into files, logs, or chat. `.env.example` documents shape.
- **Database**: all writes go through the pipeline CLI / `db.py`. No ad-hoc SQL
  against production tables. Schema changes = edit `sql/schema.sql` + apply.
- **SEC courtesy**: `EDGAR_IDENTITY` must be set; requests are throttled
  (<=8/s) and cached under `data/cache/`. Don't strip either.
- **Parallel spend**: every Parallel call path respects
  `enrich.parallel.max_tasks_per_run` and `people.max_companies_per_run` in
  `config/settings.yaml`. Use `--dry-run` first on new batches. Never loop
  Parallel calls outside those caps.
- **LLM costs (v1)**: bulk scoring reasoning runs through Claude Code **Haiku
  subagents** (see /score skill) — never call paid LLM APIs from the pipeline
  in v1. `llm.py` has the OpenRouter provider for production later.
- **Qualification**: thresholds in `config/settings.yaml` are human decisions —
  propose changes, don't silently edit.
- **Scope**: v1 stops at qualified accounts + contacts. No outreach message
  generation, no sending, no CRM pushes.
- **Verification**: after changing a collector, verify with
  `enrich --ticker X --dry-run` on a known company before batch runs.

## Layout

- `src/pipeline/` — cli, db, models, universe, edgar_signals, parallel_signals,
  scoring, llm, people
- `config/settings.yaml` — universe band, sector→SIC map, weights, thresholds, caps
- `config/services.yaml` — martechs.io service catalog (drives service-fit mapping)
- `sql/schema.sql` — Supabase DDL (apply via SUPABASE_DB_URL or SQL editor)
- `docs/SIGNALS.md` — signal taxonomy E1–E9 / P1–P6 with detection logic
- `docs/PIPELINE.md` — runbook
- `docs/ARCHITECTURE.md` — module map, data flow, design decisions
- `README.md` / `CONTRIBUTING.md` — public-facing guide + collaboration rules
- `data/` — gitignored: caches, scoring queue/results, exports
