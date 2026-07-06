# Outreach Angles — Scoring & Qualification Tightening (v2, sub-project 1 of 3)

**Date**: 2026-07-06
**Status**: Approved design, pending implementation plan
**Follows**: v1 pipeline (qualified accounts + contacts). Precedes: outreach
message generation (sub-project 2), CRM push (sub-project 3).

## Problem

v1 qualification is score-driven: total ≥ 65 + one hard signal. That produces
accounts worth *researching* but not necessarily accounts worth *messaging* —
the evidence behind a score is scoring-shaped (weights, components), not
message-shaped. Outreach copy differs fundamentally by trigger: a fundraise
message is not a new-CRO message is not an AI-launch message. Today the
pipeline neither collects funding events at all nor stores any signal in a
form outreach can consume (structured fields, per-event, independently stored,
rankable by strength).

Current funnel: 10 contacts_found, 33 in the review band (45–64), 137
disqualified out of a 170-company universe.

## Goals

1. Every qualified account carries at least one **message-ready outreach
   angle**: a fresh, evidence-cited, structured event that outreach copy can
   lead with.
2. Angles are stored **independently of signals**, one row per event, with
   typed per-family fields ("own headers") and a strength ranking so the
   strongest angle drives the message.
3. Deeper evidence collection is **score-gated and cost-capped** (two-tier):
   cheap screen for everyone, deep pass only for companies at or near the bar.

**Non-goals** (later sub-projects): message generation, CRM push, sending,
integrations beyond EDGAR/Parallel (no job-board or LinkedIn scrapers — new
signal *types* come from existing *sources*).

## Decisions locked during brainstorm

| Decision | Choice |
|----------|--------|
| Purpose of tightening | Message-ready evidence + richer signals (not threshold tuning) |
| Angle families | `funding` (new), `leadership`, `ai_move`. Cost/restructuring stays scoring-only |
| Storage | New first-class `angles` table (Approach A) — not signals columns, not verdict-only |
| Deep-pass scope | Two-tier, score-gated: status ∈ {scored, qualified, contacts_found}, auto for future crossers |
| Qualify gate | total ≥ 65 AND ≥1 hard signal AND ≥1 active fresh angle — enforced deterministically in `scoring.py` |
| Existing qualified accounts | Never auto-demoted by rescore; they gain angles + an `angle_ready` flag in export |
| LLM cost rule | Unchanged — scoring reasoning via Claude Code Haiku subagents; only new spend is capped deep Parallel tasks |

## Data model

### New table: `angles` (sql/schema.sql, additive)

```sql
create table if not exists angles (
  id uuid primary key default gen_random_uuid(),
  company_cik text not null references companies (cik) on delete cascade,
  family text not null check (family in ('funding', 'leadership', 'ai_move')),
  headline text not null,             -- "Raised $12M follow-on, Mar 2026"
  details jsonb not null,             -- typed per family, validated in models.py
  evidence_url text,
  evidence_quote text,
  event_date date not null,           -- drives freshness
  source text not null check (source in ('edgar', 'parallel')),
  strength numeric not null,          -- 0–1: recency decay × evidence quality
  status text not null default 'active' check (status in ('active', 'stale')),
  fingerprint text not null,          -- dedupe key: family + event identity
  collected_at timestamptz not null default now(),
  unique (company_cik, fingerprint)
);
create index if not exists angles_company_idx on angles (company_cik);
create index if not exists angles_family_idx on angles (family);
```

### Per-family `details` schemas (Pydantic models in `models.py`)

- **funding**: `amount_usd` (nullable — shelves may not state), `instrument`
  (`follow_on | atm | pipe | shelf | debt | other`), `announced` (date),
  `use_of_proceeds` (text, nullable), `filing_type` (e.g. `424B5`, `8-K 3.02`)
- **leadership**: `role`, `person_name` (nullable if not yet resolved),
  `start_date`, `first_in_role` (bool), `mandate_quote` (nullable)
- **ai_move**: `initiative`, `move_type`
  (`product_launch | partnership | pilot | exec_statement`), `partner`
  (nullable), `exec_quote` (nullable), `announced` (date)

### Semantics (deliberately different from `signals`)

- **Dedupe, don't wipe.** Upsert on `(company_cik, fingerprint)`;
  fingerprint = family + normalized event identity (e.g. funding:
  filing accession no; leadership: role + start_date; ai_move: initiative +
  announced month). Re-enrichment adds new events and refreshes
  `strength`/`status` on existing ones. Signals keep their
  replace-per-source-per-run semantics; angles accumulate.
- **Freshness windows** per family in `config/settings.yaml` (defaults:
  funding 365d, leadership 365d, ai_move 270d). Recomputed at collection and
  at scoring: `event_date` outside window → `status = 'stale'`. Stale angles
  never satisfy the qualify gate and never rank as primary.
- **Strength** = linear recency decay within the window (reuses the
  `scoring.recency` shape: full strength ≤ 90 days, floor 0.25 at window
  edge) × evidence quality (1.0 with quote + URL, 0.7 URL only, 0.4 neither).

## Collectors

### New: `funding_events.py` (EDGAR, free, whole active universe)

Detects capital events from filing index metadata first (cheap), then text:

- **S-3 / S-3ASR** → shelf registration (instrument `shelf`)
- **424B1–424B5** → priced offering (`follow_on` or `atm` per prospectus text)
- **8-K Item 3.02** → unregistered sale (`pipe`)
- **8-K Item 1.01** with financing phrases (credit agreement, loan facility,
  securities purchase agreement) → `debt`/`pipe`
- Amount extraction: dollar figures adjacent to "gross proceeds" /
  "aggregate offering price" / "principal amount" patterns; nullable when not
  stated. Use-of-proceeds sentence captured as `evidence_quote` when present.

Reuses the EDGAR client conventions: identity stamp, ≤8 req/s throttle,
`data/cache/` caching, item-metadata prefilter before any text download.

### Upgraded: deep Parallel task (paid, gated)

One structured research task per eligible company, returning angle-shaped
JSON alongside the existing P-signal refresh:

- **leadership**: named execs hired ≤12mo with role, start date, and a mandate
  quote from the announcement or an interview
- **ai_move**: initiatives with name, type, partner, exec quote, date
- **funding**: press-release color for events EDGAR already found (adds
  quotes/context; EDGAR remains the source of record for the event itself)

Task output is schema-validated per company; invalid angle objects are
dropped with a logged warning — one bad company never sinks the batch
(existing fan-out failure-isolation pattern).

## Orchestration: two-tier enrichment

New path: `pipeline enrich --source deep`.

- **Eligibility**: status ∈ {scored, qualified, contacts_found} — today the
  33 review-band + 10 contacts_found companies; future companies become
  eligible the moment they cross the disqualify floor. Ordered by score
  descending.
- **Cap**: new `enrich.deep.max_tasks_per_run` (default **15**) — ~3 runs to
  cover the current 43. `--dry-run` prints the selection without spend;
  `--ticker X` targets one company (same conventions as other sources).
- The EDGAR funding collector is *not* gated — it runs in the regular
  `--source edgar` pass for all active companies, because it's free.
- After a deep run, affected companies are rescored (`score --prepare` /
  `--commit` as usual).

## Scoring & qualification changes

1. **Packets** (`score --prepare`): include the company's `active` angles
   (headline, family, details, strength, evidence) alongside signals.
   Shared rubric in `_shared.json` gains an angle-ranking instruction.
2. **Verdict schema** adds:
   - `angle_ranking`: ordered list of `{fingerprint, family, message_hook}`
     — `message_hook` is a one-sentence copy angle per angle
   - `primary_angle`: `{fingerprint, family, why_this_angle}` — what outreach
     leads with
3. **Gate** (`score --commit`, deterministic in `scoring.py` — the LLM cannot
   qualify a company by itself):
   - qualified: `total ≥ qualify_threshold` AND ≥1 hard signal AND ≥1
     `active` angle
   - a company with score ≥ threshold but zero active angles stays `scored`
     (review band), with the missing-angle reason recorded in the verdict row
4. **`scores` table**: add `primary_angle jsonb` and `angle_ranking jsonb`
   columns (additive).
5. **No auto-demotion**: rescoring never moves `qualified`/`contacts_found`
   companies backward. `export` gains an `angle_ready` boolean and
   primary-angle columns so outreach can filter and sort.
6. **Config** (`config/settings.yaml`, human-owned as ever):

```yaml
angles:
  freshness_days: {funding: 365, leadership: 365, ai_move: 270}
  strength:
    full_days: 90
    floor: 0.25
enrich:
  deep:
    max_tasks_per_run: 15
scoring:
  require_angle: true     # the tightened gate; set false to fall back to v1 behavior
```

## Error handling

- Funding text parsing is best-effort: unparseable amounts → `amount_usd:
  null`, event still recorded (the filing itself is the evidence).
- Deep-task schema violations: drop the invalid angle object, keep valid ones,
  log per-company warnings into the run record.
- Angle upsert conflicts resolve by fingerprint — newest collection wins on
  `strength`/`status`, `collected_at` updated.
- `apply-schema` remains idempotent; all schema changes are additive.

## Testing

- `tests/test_funding_events.py`: fixture filing texts → instrument mapping,
  amount extraction, fingerprint stability, item-prefilter behavior.
- `tests/test_angles.py` (models + strength): per-family details validation,
  freshness/stale transitions, strength math, dedupe fingerprinting.
- `tests/test_scoring.py` additions: angle-required gate (score ≥65 without
  angle stays scored; with stale-only angles stays scored; with one active
  angle qualifies), no-auto-demotion, packet includes angles.
- Deep Parallel parsing: fixture task responses → angle rows, invalid-object
  isolation. No network in tests (existing convention).
- Live verification before batch: `enrich --ticker <known company> --dry-run`
  then one real deep task on a single company, per CLAUDE.md collector rule.

## Rollout

1. Schema + models + funding collector (free tier) → run on active universe.
2. Deep Parallel task on a 3-company probe run (real spend, small).
3. Deep runs over the 43 eligible companies (~3 capped runs).
4. Rescore review band + qualified set; review the new qualified list.
5. Then: sub-project 2 (outreach messages) consumes `angles` + `primary_angle`.

## Success criteria

- Every newly qualified account has ≥1 active angle with evidence URL + quote.
- The 33-company review band resolves into qualified (with angles) or
  remains with an explicit missing-angle/stale-angle reason — no silent limbo.
- Funding events appear for companies that raised in the last 12 months
  (spot-check against known raises).
- All existing tests keep passing; suite stays fast and offline.
