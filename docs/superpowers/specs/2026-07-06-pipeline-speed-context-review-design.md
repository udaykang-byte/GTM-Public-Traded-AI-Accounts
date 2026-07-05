# Pipeline speed + context-hygiene pass — design

Date: 2026-07-06
Status: approved (brainstorm review of v1 pipeline prompts, skills, and research time)

## Goal

Three outcomes, decided with the user:

1. **Research time**: an enrichment batch of 25 companies completes in ~5 minutes
   wall-clock (Parallel and EDGAR both).
2. **Subagent context/cost**: scoring packets stop duplicating ~5.4KB of shared
   boilerplate per packet (~40% of a Haiku batch's input tokens).
3. **Skill accuracy + context hygiene**: skill files match the CLI exactly and
   keep bulk stdout out of the main conversation.

The review found the prompts themselves (Parallel enrich schema, people-search
prompt, scoring rubric) accurate — no wording changes needed there. Caps,
anti-guessing rules, and the Haiku-only scoring rule are all correctly enforced.

## 1. Parallel.ai fan-out (enrich + people)

**Problem**: `run_task()` creates one task run and blocks polling it (up to
600s) before the next company starts. 10 companies ≈ 13 min; 25 ≈ 30–45 min.

**Design** (create-all-then-poll; no threads, no asyncio):

- `parallel_client.py` gains
  `run_tasks_batch(tasks: list[tuple[str, dict]], processor, timeout_s) -> list[dict | Exception]`
  — phase 1 creates every task run sequentially (fast HTTP POSTs, ~1s each),
  phase 2 polls all pending run_ids round-robin (5s interval) until each
  completes, fails, or hits the shared deadline. Per-task failures/timeouts
  become `Exception` entries in the result list; they never abort the batch.
- `parallel_signals.py` gains `collect_batch(companies) -> dict[cik, (signals, errors)]`
  built on `run_tasks_batch`; the existing single-company `collect()` remains
  for `--ticker`.
- `people.py` gains the equivalent `find_people_batch`.
- `cli.py` uses the batch entry points for multi-company runs. The
  `max_tasks_per_run` cap is applied to the batch size before creation —
  spend semantics unchanged.

**Expected**: batch of 25 ≈ slowest single task (~3–5 min).

## 2. EDGAR 8-K item-metadata filter

**Problem**: `eightk_signals()` downloads the full text of every 8-K in the
last 365 days (often 10–30 filings per company) just to substring-match
"5.02"/"2.05". This dominates EDGAR enrichment time (hours for a full sweep)
and can false-positive on stray "5.02" strings.

**Design**:

- Filter 8-K filings by **item metadata from the filing index** (edgartools
  exposes items without downloading documents). Download text only for filings
  whose items include **5.02** (→ E3 exec change) or **2.05** (→ E4
  restructuring) — usually 0–3 per company.
- Decision (user): **strict {5.02, 2.05}**. E4 no longer phrase-scans
  press-release-only 8-Ks (Items 7.01/8.01); charge-less cost announcements are
  accepted as a miss since material restructurings must file Item 2.05.
  Phrase matching still runs *within* the downloaded 2.05 filings to build the
  evidence quote.
- E3 quote-anchoring logic and all other collectors (10-K sections, DEF 14A,
  XBRL companyfacts, IPO forms) unchanged.
- **Verification gate**: before wiring in, confirm edgartools item metadata on
  a company with a known archived E3 signal; then
  `enrich --ticker <known> --dry-run` must still produce the E3/E4 signals it
  produced before (per CLAUDE.md collector-change rule).

**Expected**: EDGAR per-company time drops to seconds + 10-K parse; batch of 25
in a few minutes; full-universe sweep well under an hour.

## 3. Scoring packet dedupe

**Problem**: every packet embeds `services_catalog` + `rubric` +
`output_schema` + instructions (~5.4KB of an ~10KB packet). A batch-of-5 Haiku
subagent reads the same boilerplate five times.

**Design**:

- `scoring.prepare()` writes **`data/scoring_queue/_shared.json`** once per
  queue: `{services_catalog, rubric, output_schema, instructions}`.
- Packets keep only per-company data: `ticker`, `company`, `signals`,
  `base_score`, `hard_signals_present`, plus an `instructions` string that
  names the shared file and the exact output path.
- `pending_queue()` and `commit()` ignore underscore-prefixed files; `commit()`
  archives `_shared.json` into the run's archive dir for reproducibility.
- `/score` skill step 2 subagent prompt becomes: read `_shared.json` once, then
  each packet; everything else (model: haiku, ≤5 packets per subagent, parallel
  spawns, re-spawn failures per packet) unchanged.
- `ScoreVerdict` schema and commit validation unchanged.

## 4. Skill-file updates

- **/enrich**: multi-company batches run with `run_in_background`; report from
  the final stats line + DB instead of streaming per-company tables. Document
  `--force`. Update timing expectations post-speedup.
- **/people**: same background + reporting guidance; document the
  retry-on-timeout pattern (re-run `--ticker X` for a timed-out company).
- **/score**: subagent prompt updated for `_shared.json` (above).
- **/status**: surface the review band (`scored` between thresholds) as an
  actionable stage and document the `promote` command — currently no skill
  mentions it.
- **/discover**, **/ingest**: no changes needed (already accurate; /discover
  already mandates background runs).

## 5. Minor code hygiene

- `cli.py` parallel-pool filter: replace the per-company `db.get_signals()`
  N+1 with the existing `db.all_signals()` bulk fetch.

## Error handling

- Batch creation failure for one company → recorded as that company's error;
  batch continues.
- Poll timeout/failure → per-company error in the final report (existing CLI
  pattern); the /people and /enrich skills tell the agent to retry failed
  tickers individually.
- `_shared.json` missing when a subagent runs → subagent reports it; commit
  validation would also catch malformed verdicts. `score --prepare` always
  rewrites it, so the failure mode requires manually deleting the file.

## Testing / verification

1. edgartools item-metadata probe on a known-E3 company (script in scratchpad,
   not committed).
2. `enrich --ticker <known-E3-company> --dry-run` before/after — same E3/E4
   signals, far fewer HTTP fetches.
3. Real Parallel fan-out check on a **2–3 company** batch (small spend,
   respects caps; `--dry-run` first per project rules).
4. `score --prepare --limit 3` → `_shared.json` + slim packets on disk; one
   Haiku subagent batch scores them; `score --commit` validates.

## Out of scope

- No outreach/CRM features (v1 boundary unchanged).
- No threshold or weight changes (human decision, per project rules).
- No prompt-wording changes to Parallel schemas or the rubric.
- No EDGAR multi-threading — the item filter alone meets the speed target
  without SEC rate-limit risk.
