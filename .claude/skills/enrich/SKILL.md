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

Both: `--source all`. Single company (works even before it's in the DB): `--ticker XYZ`.
Preview with `--dry-run` — for Parallel this lists which companies WOULD get a task
and **never spends**. Re-enrich already-enriched companies: `--force`.

What gets collected (details in `docs/SIGNALS.md`):
- **EDGAR E1–E9**: AI-mention analysis in 10-K/10-Q (strategy vs risk-factor placement, YoY delta), 8-K exec changes and restructuring, S&M-spend efficiency from XBRL, tech-leadership gap from proxy, recent-IPO flag, cash capacity.
- **Parallel P1–P6**: AI job postings, SDR/marketing hiring, AI announcements, product AI gap, martech stack, exec AI commentary — one structured research task per company.

Ground rules:
- EDGAR before Parallel (free before paid); don't run Parallel on companies the user hasn't asked to prioritize unless batch is small.
- Respect the config caps; never loop Parallel calls around them.
- One company failing must not stop the batch — failures are logged per company; report them at the end. A failed/timed-out Parallel task keeps the company's previous parallel signals; retry it alone with `--ticker X --source parallel --force`.
- **Run multi-company batches in the background** (Bash `run_in_background`) and report from the final `Done: {...}` stats line plus DB counts — don't stream dozens of per-company tables into the conversation.
- Timing: Parallel tasks are created up front and polled together, so a full 25-company batch ≈ its slowest single task (~3–5 min). EDGAR batches run at SEC-polite rates — 8-Ks are filtered by index metadata, so expect seconds per company plus 10-K parsing.

After running: show a compact table (ticker → signals found, strongest signal, evidence snippet), note failures, suggest `/score` when a decent batch is enriched.
