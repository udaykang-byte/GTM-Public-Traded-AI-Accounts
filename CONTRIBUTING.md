# Contributing

Small project, small rules. The goal: anyone can clone, run, and safely change
the pipeline without surprising costs or broken state.

## Getting set up

Follow the [README quickstart](README.md#quickstart). You need your own `.env`
(never committed) and ideally your own Supabase project for development so you
can't damage production data.

## Development workflow

1. Branch from `main`.
2. Make the change, with tests where behavior changes.
3. Run the suite — it's fast on purpose:
   ```bash
   uv run pytest
   ```
4. If you touched a signal collector, verify against a real company before any
   batch run:
   ```bash
   uv run python -m pipeline enrich --ticker TWLO --dry-run
   ```
5. Open a PR against `main` with a short description of what changed and why.

## Rules that keep the pipeline safe

- **Everything runs via `uv`** — `uv run python -m pipeline …`, `uv run pytest`.
- **Secrets live only in `.env`** (gitignored). Never commit, print, or log key
  values. `.env.example` documents the shape.
- **All database access goes through `db.py`** — no ad-hoc SQL against
  production tables. Schema changes = edit `sql/schema.sql` + `apply-schema`.
- **Respect the SEC**: `EDGAR_IDENTITY` must be set; requests are throttled
  (≤8/s) and cached under `data/cache/`. Don't strip either.
- **Respect the Parallel budget**: every Parallel call path honors the per-run
  caps in `config/settings.yaml`. Use `--dry-run` first on new batches; never
  loop Parallel calls outside the caps.
- **No paid LLM APIs in v1** — bulk scoring reasoning runs through Claude Code
  Haiku subagents (see `.claude/skills/score/`). `llm.py`'s OpenRouter provider
  is the v2 path.
- **Qualification thresholds are human decisions** — propose changes to
  `config/settings.yaml`, don't silently edit them.
- **v1 scope stops at qualified accounts + contacts** — no outreach message
  generation, no sending, no CRM pushes.

## Tests

`tests/` holds fast unit tests (sub-second, no network, no database) covering
signal detection, scoring math, packet building, and the Parallel client's
parsing. Add a test alongside any behavior change; if you're fixing a bug,
write the failing test first.

## Working with Claude Code

The repo ships its Claude Code setup in `.claude/`: stage skills
(`/status`, `/discover`, `/ingest`, `/enrich`, `/score`, `/people`) that encode
the correct orchestration per stage, a session hook that shows funnel state, and
a post-edit hook that runs the test suite automatically. Prefer the skills over
hand-rolling stage commands — especially `/score`, which manages the subagent
fan-out.
