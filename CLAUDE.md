# AIPT — AI-Ready Public Companies Pipeline (martechs.io)

Finds US-listed micro-cap companies (Fintech, Edtech, Healthcare, SaaS) showing
signals they need AI services, scores them with cited reasoning, and surfaces
decision-maker contacts. State lives in Supabase; signal sources are SEC EDGAR
(edgartools) and Parallel.ai web research.

## Funnel

`new → enriched → scored → qualified | disqualified → contacts_found`
(status column on `companies`; between disqualify_below and qualify_threshold a
company stays `scored` = human review band). After `contacts_found`, the
`messages` stage drafts per-contact outreach sequences — no status change;
coverage is derived from the `messages` table.

## Commands (always via uv)

```bash
uv run python -m pipeline status            # funnel counts (--brief for one line)
uv run python -m pipeline status --analytics # + outcome funnel, reply/meeting rates, time-in-stage
uv run python -m pipeline discover          # screen SEC universe -> seed companies
uv run python -m pipeline ingest AAPL,TWLO  # or --csv path (ticker column); --force bypasses the prescreen
uv run python -m pipeline enrich --source edgar --limit 10
uv run python -m pipeline enrich --source parallel --limit 10
uv run python -m pipeline score --prepare   # packets -> data/scoring_queue/
uv run python -m pipeline score --commit    # verdicts -> Supabase + qualify
uv run python -m pipeline people --limit 5  # contacts for qualified accounts
uv run python -m pipeline messages --prepare # per-contact packets -> data/message_queue/
uv run python -m pipeline messages --commit  # QA gate -> messages table (drafts)
uv run python -m pipeline outcome 42 --event replied  # log an outcome event; --csv path for batch
uv run python -m pipeline export --messages  # qualified.csv + messages.csv
uv run python -m pipeline profile --list/--show/--validate  # inspect the active profile pack
```

Skills exist for each stage: /ingest /discover /enrich /score /people /outreach
/status. Prefer them — they encode the correct orchestration (especially /score
and /outreach).

## Profile packs

Config is a swappable pack: `config/` is the default (martechs.io) pack;
`profiles/<name>/` may override any subset of `settings.yaml`,
`services.yaml`, `personas.yaml`, `outbound_copywriter.md`, `icp.md` —
missing files fall back to `config/` (per-file replace, never merged).
Select with `--profile <name>` (before the subcommand) or `AIPT_PROFILE`;
omit both for default behavior. Inspect via
`uv run python -m pipeline profile --list/--show/--validate`. Build a new
pack interactively with `/icp`. Sector vocabulary is free-form lowercase
text — saas/fintech/edtech/healthcare/other is just the default pack's set.

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
- **LLM costs (v1)**: bulk scoring reasoning AND outreach copywriting run
  through Claude Code **Haiku subagents** (see /score and /outreach skills) —
  never call paid LLM APIs from the pipeline in v1. `llm.py` has the OpenRouter
  provider for production later.
- **Outreach copy**: rules live in `config/outbound_copywriter.md` (Uday's
  voice decisions — propose changes, don't silently edit). The QA gate in
  `messages.py` (`BANNED_WORDS`, subject shape, packet-facts-only) is never
  relaxed to make a draft pass; fix the draft. Always spot-check
  `unverified number` warnings before export.
- **Qualification**: thresholds in `config/settings.yaml` are human decisions —
  propose changes, don't silently edit.
- **Scope**: pipeline stops at drafted outreach sequences (v2 sub-project 2).
  No sending, no CRM pushes — that's sub-project 3.
- **Verification**: after changing a collector, verify with
  `enrich --ticker X --dry-run` on a known company before batch runs.

## Layout

- `src/pipeline/` — cli, db, models, universe, edgar_signals, parallel_signals,
  scoring, llm, people, messages
- `config/settings.yaml` — universe band, sector→SIC map, weights, thresholds, caps
- `config/services.yaml` — martechs.io service catalog (drives service-fit mapping)
- `config/outbound_copywriter.md` — copy framework the /outreach subagents follow
- `profiles/<name>/` — optional profile packs overriding `config/` files (see Profile packs)
- `sql/schema.sql` — Supabase DDL (apply via SUPABASE_DB_URL or SQL editor)
- `docs/SIGNALS.md` — signal taxonomy E1–E9 / P1–P6 with detection logic
- `docs/PIPELINE.md` — runbook
- `docs/ARCHITECTURE.md` — module map, data flow, design decisions
- `README.md` / `CONTRIBUTING.md` — public-facing guide + collaboration rules
- `data/` — gitignored: caches, scoring queue/results, exports
