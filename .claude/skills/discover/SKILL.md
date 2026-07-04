---
name: discover
description: Screen the whole SEC universe for micro-cap Fintech/Edtech/Healthcare/SaaS companies and seed them into the pipeline. Use when the user wants to find new candidate companies rather than providing a list.
---

# /discover — universe screen

Preview first (no DB writes, prints the funnel of filters):

```bash
uv run python -m pipeline discover --dry-run
```

Then seed for real:

```bash
uv run python -m pipeline discover
```

How it works: SEC company list (~10k) → exchange filter → SIC sector buckets (config `universe.sectors`) → market-cap band via yfinance ($50M–$300M default) → upsert as `new`.

Important:
- **First run is slow** (one-time SIC crawl over the universe at SEC-polite rates, ~15–20 min; cached forever under `data/cache/`). Subsequent runs are minutes. Run it in the background and report progress.
- Use `--limit N` to cap how many new companies get seeded in one go.
- Band/sectors are config knobs in `config/settings.yaml` — if the user wants a wider band or different sectors, edit config, don't hack code.

After running: report universe → sector → band counts, list a sample of seeded companies (ticker, name, sector, cap), suggest `/enrich`.
