---
name: enrich
description: Run signal collectors (SEC EDGAR filings analysis + Parallel.ai web research) for companies in the pipeline. Use after ingest/discover, or when asked to "run signals" for companies.
---

# /enrich — collect signals

EDGAR signals (free, run first):

```bash
uv run python -m pipeline enrich --source edgar --limit 10
```

Parallel web signals (paid per task — capped by config `enrich.parallel.max_tasks_per_run`):

```bash
uv run python -m pipeline enrich --source parallel --limit 10
```

Both: `--source all`. Single company (works even before it's in the DB): `--ticker XYZ`. Preview without DB writes: `--dry-run`.

What gets collected (details in `docs/SIGNALS.md`):
- **EDGAR E1–E9**: AI-mention analysis in 10-K/10-Q (strategy vs risk-factor placement, YoY delta), 8-K exec changes and restructuring, S&M-spend efficiency from XBRL, tech-leadership gap from proxy, recent-IPO flag, cash capacity.
- **Parallel P1–P6**: AI job postings, SDR/marketing hiring, AI announcements, product AI gap, martech stack, exec AI commentary — one structured research task per company.

Ground rules:
- EDGAR before Parallel (free before paid); don't run Parallel on companies the user hasn't asked to prioritize unless batch is small.
- Respect the config caps; never loop Parallel calls around them.
- One company failing must not stop the batch — failures are logged per company; report them at the end.

After running: show a compact table (ticker → signals found, strongest signal, evidence snippet), note failures, suggest `/score` when a decent batch is enriched.
