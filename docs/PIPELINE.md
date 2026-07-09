# Pipeline Runbook

## One-time setup

1. `cp .env.example .env` and fill in: `EDGAR_IDENTITY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY` (+ optionally `SUPABASE_DB_URL`, `PARALLEL_API_KEY`).
2. `uv sync`
3. Apply schema: `uv run python -m pipeline apply-schema` (needs `SUPABASE_DB_URL`)
   — or paste `sql/schema.sql` into the Supabase SQL editor once.
4. Parallel auth: `parallel-cli login` (device OAuth) or set `PARALLEL_API_KEY`.

### Upgrading to v3 (prescreen, tiers, outcomes)

Existing installs must re-run `uv run python -m pipeline apply-schema` after
pulling v3 — it adds `companies.dq_reason` / `tier` / `status_changed_at`,
`scores.tier` / `priority`, the `message_events` table, and widens
`messages.status` to the outcome ladder (`bounced`/`replied`/`positive_reply`/
`meeting`/`opted_out`). Pre-v3 rows backfill `status_changed_at` from
`updated_at` — `status --analytics` labels time-in-stage "approximate" for
those rows since `updated_at` moves on any column update, not just a status
change.

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

## Profile packs

Running against a different ICP than the shipped default? Two paths:

- **Interactive** — `/icp` (Claude Code skill) interviews you (best/worst
  customers → discriminating attributes → point values → tier cutlines →
  sector/SIC vocabulary → services + voice) and writes `profiles/<name>/`:
  `settings.yaml`, and only the other files that differ from the default
  (`services.yaml`, `personas.yaml`, `outbound_copywriter.md`, `icp.md`).
- **Manual** — copy whichever `config/*.yaml` you want to change into
  `profiles/<name>/`. Any file missing from the pack falls back to the
  default `config/` version — packs are a per-file overlay, never merged.

Select the pack with `--profile <name>` before any command, or export
`AIPT_PROFILE=<name>` once per shell. Inspect and sanity-check it:

```bash
uv run python -m pipeline profile --list                        # every pack + which is active
uv run python -m pipeline --profile <name> profile --show       # resolved settings
uv run python -m pipeline --profile <name> profile --validate   # structural checks
```

`--validate` only catches structural gaps (missing sectors/weights/caps, caps
not summing near 100, non-numeric thresholds) — it can't tell you the
thresholds are *right* for your ICP. Run `discover --dry-run` against the new
pack and eyeball the sample before spending anything real.

## Normal cycle

```bash
# 1. Get companies in (either path) — both run the L1 prescreen first
uv run python -m pipeline discover --dry-run     # preview screen + prescreen
uv run python -m pipeline discover               # seed micro-cap sector matches
uv run python -m pipeline ingest "TICK1,TICK2"   # or explicit list / --csv

# 2. Signals — free source first, paid second
uv run python -m pipeline enrich --source edgar --limit 10
uv run python -m pipeline enrich --source parallel --limit 10

# 3. Score (v1: Haiku subagents via Claude Code — see /score skill)
uv run python -m pipeline score --prepare
#   -> /score skill spawns Haiku subagents -> results land in data/scoring_results/
uv run python -m pipeline score --commit
#   -> also assigns tier (T1-T4) + a priority score — see Tiers & priority below

# 3b. Deep tier — richer evidence + outreach angles for review-band/qualified
uv run python -m pipeline enrich --source deep --dry-run   # preview selection
uv run python -m pipeline enrich --source deep --limit 15  # capped, paid
uv run python -m pipeline score --prepare --statuses scored  # rescore with angles
#   -> /score skill -> score --commit

# 4. Contacts for qualified accounts — strongest tier/priority worked first
uv run python -m pipeline people --limit 5

# 5. Outreach drafts (v2: Haiku subagents via Claude Code — see /outreach skill)
uv run python -m pipeline messages --prepare     # one packet per contact, tier/priority ordered
#   -> /outreach skill spawns Haiku subagents -> results land in data/message_results/
uv run python -m pipeline messages --commit      # QA gate -> messages table (drafts)

# 6. Hand-off
uv run python -m pipeline export --messages      # qualified.csv + messages.csv + deliverability checklist
```

**Reading prescreen (DQ) output**: `ingest`'s table prints a `DQ: <reason>`
note per rejected ticker — `excluded_ticker`, `excluded_sic:<code>`,
`shell_name:<pattern>`, `otc_listed`, `exchange_not_allowed:<exchange>`, or
`outside_cap_band` — plus a one-line count on write (`N pre-screen
disqualified — never enriched`). Rejected rows land as `status=disqualified`,
`tier=T4`, `dq_reason` set — never enriched, never worth an EDGAR/Parallel
call. `discover` runs the identical check inside the screen — failing rows
are never seeded, and show up as the `prescreen_dq` count in its discovery
funnel table. `ingest`'s user list overrides only the *sector* filter; the
prescreen's hard exclusions still apply unless you pass `--force`.

`status` shows the funnel any time. Single-company deep dive:
`enrich --ticker XYZ --dry-run` (works even if XYZ isn't in the DB yet).

## Tiers & priority

Every scored company gets a **tier**, computed once at `score --commit`
(never an LLM output) from the verdict total + qualify/disqualify bucket:

| Tier | Meaning |
|------|---------|
| T1 | qualified, total ≥ `scoring.tiers.t1_min` (default 80) |
| T2 | qualified, below that bar |
| T3 | review band (`scored`) — or any company never scored |
| T4 | disqualified, including prescreen rejections at ingest time |

Alongside tier, a **priority** score orders companies within a tier:
`total*total_weight + stacking_bonus*stacking_weight +
max_angle_strength*angle_strength_weight` (weights in `scoring.priority`).
`people` and `messages --prepare` both sort their candidate pool by `(tier
asc, priority desc)` before applying `--limit`, so when a per-run cap bites,
the strongest accounts get worked first — not whichever row the DB happened
to return first. `status` prints a tier-breakdown table (NULL/unscored counts
as T3).

## Outcomes & analytics

After a drafted sequence actually gets sent (outside this tool — export it,
send it through whatever mailbox/sequencer you use), record what happened:

```bash
uv run python -m pipeline outcome 123 --event sent
uv run python -m pipeline outcome 123 --event replied --date 2026-07-10 --note "asked for pricing"
uv run python -m pipeline outcome --ticker TWLO --contact "Jane Doe" --event meeting  # fuzzy lookup, no message_id needed
uv run python -m pipeline outcome --csv outcomes.csv    # batch: message_id,event,date,note columns (date/note optional)
```

Events: `approved | rejected | exported | sent | bounced | replied |
positive_reply | meeting | opt_out`. Every event is appended to the
append-only `message_events` log regardless of effect; `messages.status` only
ever advances forward along a fixed ladder (draft → approved → exported →
sent → replied → positive_reply → meeting), with `rejected`/`bounced`/
`opted_out` as terminal states reachable from any point but never left. An
out-of-order or repeated event is still logged, just reported "(no status
change)". `--csv` mode attempts every row and reports pass/fail per row
rather than aborting on the first bad one.

Once there's data, `pipeline status --analytics` renders:

- funnel snapshot (company counts + rate-vs-previous-stage — a rough
  bottleneck signal, not a cohort conversion rate: `ingest`'s prescreen can
  write `disqualified` straight from `new`, skipping `enriched`/`scored`)
- avg time-in-stage in days (approximate for rows that predate this feature —
  backfilled from `updated_at`)
- the sent → replied → positive_reply → meeting funnel, with a benchmark band
  on positive-reply rate (`analytics.benchmarks.positive_reply_rate` — the
  north star metric) once sends clear `analytics.min_sends_for_attribution`
  (default 10)
- the same funnel sliced by archetype / angle_family / service

Below the minimum-sends floor, a section prints "insufficient data" instead of
a rate — small samples lie.

## Status machine

`new → enriched → scored → qualified | disqualified → contacts_found`

- `scored` (between disqualify floor and qualify threshold) = human review band.
- Re-enriching is idempotent: signals are replaced per source, not duplicated.
- `ingest`'s user list overrides only the *sector* filter — companies outside
  the target sectors still get added. The L1 prescreen's hard exclusions
  (excluded ticker/SIC, cap band, exchange/OTC, shell name) still apply and
  can write a row straight to `disqualified` (`tier=T4`), bypassing
  `enriched`/`scored` entirely — `--force` is the only way around it.
- `promote TICK1,TICK2` moves a company from either `scored` or
  `disqualified` to `qualified` by hand — a human override for the review
  band or a prescreen/scoring call you disagree with.

## Costs

- Prescreen: free — pure in-process checks, no network calls, runs inside
  both `discover` and `ingest`.
- EDGAR: free. Throttled ≤8 req/s, cached in `data/cache/` (first discover run
  crawls SIC codes for the whole universe once — 15–20 min, then cached).
- Parallel: ~1 task per company per enrich, 1 per company for people. Caps:
  `enrich.parallel.max_tasks_per_run` (25), `people.max_companies_per_run` (10).
- Deep tier: 1 Parallel task per company, capped at enrich.deep.max_tasks_per_run (15).
- LLM scoring: zero API cost in v1 (Claude Code Haiku subagents). v2 flips to
  OpenRouter: `score --provider openrouter` once `OPENROUTER_API_KEY` is set.
- Message drafting: zero API cost (same Haiku-subagent mechanism, /outreach skill);
  capped at `messages.max_per_run` contact packets per prepare. Copy rules live in
  `config/outbound_copywriter.md`; deterministic QA (banned words — canonical list
  in settings.yaml `messages.banned_words` — subject shape, packet-facts-only
  checks, a `personalization N/5` warning below `messages.personalization_min`)
  runs at `messages --commit`. Companies without a fresh angle are skipped —
  never message on a stale hook. `export --messages` writes a deliverability
  checklist next to the CSVs — work through it before the first send.

## Troubleshooting

- `Missing EDGAR_IDENTITY/SUPABASE_URL…` → .env incomplete.
- Parallel 401/403 → `parallel-cli auth --json`, re-login, or set `PARALLEL_API_KEY`.
- Parallel task timeout → processor tier busy; re-run, task cost is per-run.
- Invalid scoring results at `--commit` → the listed tickers need their Haiku
  subagent re-run (schema violation reported inline).
- yfinance cap = `?` → ticker delisted/renamed; cached for 7 days, delete
  `data/cache/market_caps.json` entry to retry sooner.
