# AIPT — AI-Ready Public Companies Pipeline

*Finds small US-listed companies that show public evidence they need AI help,
scores that evidence, finds the decision-makers, and drafts the first outreach —
all from SEC filings and web research.*

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Tests: pytest](https://img.shields.io/badge/tests-pytest-brightgreen)

## What this does

**In one sentence:** this tool reads public data — SEC filings and web research — to
find small US public companies that show signs they need help with AI, ranks them by
how strong that evidence is, finds the right people to talk to at each one, and drafts
the first outreach emails. Nothing is ever sent automatically; the output is a ranked
account list with contacts and email drafts you can review and use.

It was built for selling AI services, but the target profile (sectors, scoring rules,
pitch) is fully configurable — see [Adapting it to your own ICP](#adapting-it-to-your-own-icp).

## How it works, step by step

1. **Find companies** (`discover` or `ingest`) — build a list of small public
   companies in the sectors you care about, either by screening the whole SEC
   universe or by giving it tickers directly.
2. **Collect evidence** (`enrich`) — read each company's SEC filings and public web
   presence for signs they need AI help: new AI language in their annual report, a
   fresh executive hire, cost-cutting programs, hiring for sales roles, and so on.
3. **Score them** (`score`) — add the evidence up into a 0–100 score with written,
   cited reasoning. High scorers qualify, low scorers are dropped, and the middle
   band waits for a human decision.
4. **Find people** (`people`) — look up decision-makers (name, title, LinkedIn,
   public email) at the companies that qualified.
5. **Draft outreach** (`messages`) — write a short 4-step email sequence for each
   contact, grounded in that company's actual evidence. Drafts only.
6. **Learn from results** (`outcome`, `calibrate`) — once you send the emails
   yourself, record what happened (replies, meetings) and get a report on which
   kinds of evidence actually lead to conversations.

Every company moves through a simple funnel as those steps run:

```
new → enriched → scored → qualified | disqualified → contacts_found
```

- **new** — just added, from `discover` or `ingest`. Obvious non-fits (wrong size,
  wrong sector, shell companies) are filtered out at the door and marked
  `disqualified` before any money or API calls are spent on them
- **enriched** — evidence collected from SEC EDGAR (free) and Parallel.ai web
  research (paid)
- **scored** — evidence added up into a score, with LLM-written reasoning. Companies
  that are neither clearly good nor clearly bad stay here as the **human review
  band** — a person decides
- **qualified / disqualified** — the score decided: qualified needs a total of 65+
  and at least one strong ("hard") signal
- **contacts_found** — decision-makers found for qualified companies, ready to export

Qualified companies also get a **tier** (T1 best → T4 disqualified) so that when a
run can only afford to process a few companies, the strongest ones go first.

After `contacts_found`, the `messages` stage drafts a 4-step outreach sequence per
contact — drafts only; no sending. Once you actually send a sequence (outside this
tool), `pipeline outcome` records what happened — replies, meetings, opt-outs — and
`pipeline status --analytics` turns that log into conversion and reply rates.

## Architecture at a glance

```mermaid
flowchart LR
    subgraph Sources
        EDGAR[SEC EDGAR<br/>filings + XBRL]
        PAR[Parallel.ai<br/>web research]
        YF[Yahoo Finance<br/>market caps]
    end

    subgraph Pipeline["pipeline CLI (Typer)"]
        DISC["discover / ingest<br/>(+ L1 prescreen)"]
        ENR[enrich]
        SCORE["score<br/>(+ tier / priority)"]
        PEOPLE[people]
        MSG[messages]
        EXP[export]
        OUT[outcome]
    end

    DB[(Supabase<br/>companies · signals · scores ·<br/>angles · contacts · messages ·<br/>message_events · runs)]

    YF --> DISC
    EDGAR --> ENR
    PAR --> ENR
    PAR --> PEOPLE
    DISC --> DB
    ENR --> DB
    SCORE --> DB
    PEOPLE --> DB
    MSG --> DB
    OUT --> DB
    DB --> EXP
    EXP --> CSV[qualified.csv + messages.csv]
    DB -.status --analytics.-> ANALYTICS[funnel / attribution rates]
```

State lives in **Supabase** (`companies`, `signals`, `scores`, `angles`, `contacts`,
`messages`, `message_events`, plus `runs`). Everything else — EDGAR caches, scoring and
message queues, exports — is regenerable local state under `data/` (gitignored). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for
the module map and design decisions.

## Quickstart

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A [Supabase](https://supabase.com) project (free tier works)
- Optional: a [Parallel.ai](https://parallel.ai) account for web-research signals and
  contact discovery (EDGAR-only enrichment works without it)

### Setup

```bash
git clone https://github.com/udaykang-byte/GTM-Public-Traded-AI-Accounts.git
cd GTM-Public-Traded-AI-Accounts
uv sync

cp .env.example .env        # then fill in the values — see comments in the file
```

Required in `.env`: `EDGAR_IDENTITY` (your name + email — the SEC requires it, no signup),
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. Optional: `SUPABASE_DB_URL` (lets the
pipeline apply the schema itself), `PARALLEL_API_KEY` (or `parallel-cli login`).

Apply the database schema once:

```bash
uv run python -m pipeline apply-schema      # needs SUPABASE_DB_URL
# — or paste sql/schema.sql into the Supabase SQL editor
```

### First run

```bash
uv run python -m pipeline ingest "TWLO"                     # seed one company
uv run python -m pipeline enrich --source edgar --limit 1   # collect free signals
uv run python -m pipeline status                            # see the funnel move
```

A single-company deep dive works even before ingesting:
`uv run python -m pipeline enrich --ticker XYZ --dry-run`.

## Commands

All commands run through `uv run python -m pipeline <command>`. Add `--profile
<name>` (or set `AIPT_PROFILE=<name>`) before the command to run against a
different ICP pack — see [Adapting it to your own ICP](#adapting-it-to-your-own-icp):

| Command | What it does |
|---------|--------------|
| `status` | Funnel + tier counts per stage (`--brief` for one line; `--analytics` adds outcome/attribution rates) |
| `profile --list\|--show\|--validate` | Inspect or validate the active profile pack |
| `discover` | Screen the SEC universe for micro-cap sector matches (pre-screened) and seed them |
| `ingest TICK1,TICK2` | Add specific companies (or `--csv file.csv`); still runs the L1 pre-screen (`--force` bypasses it) |
| `enrich --source edgar\|parallel\|all\|deep` | Collect signals (EDGAR free; parallel/deep are paid + capped) |
| `score --prepare` / `--commit` | Build scoring packets → commit verdicts + qualify + tier |
| `people` | Find decision-makers for qualified accounts, strongest tier/priority first |
| `messages --prepare` / `--commit` | Draft per-contact outreach sequences (Haiku subagents + QA gate) |
| `export` | Write qualified accounts + contacts to `data/exports/qualified.csv` (`--messages` adds `messages.csv` + a deliverability checklist) |
| `outcome <message_id>` | Record what happened to a sent sequence (`--event ...`, or `--csv` for a batch; `--ticker`/`--contact` as a fuzzy lookup) |
| `calibrate` | Outcome → signal-weight report: which signals' messages actually get replies (report-only; weights stay a human decision) |
| `promote TICK1,TICK2` | Move review-band (or previously disqualified) companies to qualified by hand |
| `prune` | Remove stale/out-of-scope companies (`--dry-run` first) |
| `apply-schema` | Apply `sql/schema.sql` to Supabase |

The normal cycle and troubleshooting notes live in [docs/PIPELINE.md](docs/PIPELINE.md).

## Signals and scoring

A **signal** is one piece of evidence that a company might need AI help. The pipeline
looks for 15 kinds — 9 from SEC filings (E1–E9: things like new AI language in the
annual report, a recent executive hire, a cost-cutting program, sales spend growing
faster than revenue) and 6 from web research (P1–P6: AI job postings, sales-team
hiring, AI announcements, product gaps vs competitors). Every signal carries its
evidence — a URL and a quote wherever possible — so you can check the claim yourself.

Scoring adds the weighted signals up into four components:

```
total = intent(≤30) + capability_gap(≤25) + timing(≤25) + commercial_fit(≤20)
```

In plain terms: *do they want it, can they not do it themselves, is now the right
time, and can they pay for it.* An LLM reviews that base math and may adjust it, but
only with written justification.

**Qualify**: total ≥ 65 AND at least one hard signal AND at least one fresh outreach
angle (a dated, concrete event to open an email with — a funding event, a leadership
hire, or an AI move). **Disqualify**: total < 45. In between, the company waits in
the review band for a human call.

Qualified and review-band companies also get a **tier** (T1 = strongest, T4 =
disqualified) and a **priority** score, so `people` and `messages --prepare` spend
their per-run budget on the strongest accounts first.

Full detection logic and weights: [docs/SIGNALS.md](docs/SIGNALS.md) and
`config/settings.yaml`.

## Repository layout

```
src/pipeline/        # cli, config, db, models, universe, prescreen, edgar_signals,
                     # parallel_signals, parallel_client, scoring, angles,
                     # funding_events, llm, people, messages, outcomes, analytics
tests/               # pytest suite — fast unit tests, no network or DB needed
config/               # default profile pack: settings.yaml, services.yaml,
                     # personas.yaml, outbound_copywriter.md
profiles/<name>/      # profile-pack overlays written by /icp (or by hand)
sql/schema.sql       # Supabase DDL
docs/                # runbook, signal taxonomy, architecture
data/                # gitignored: caches, scoring/message queues+results, exports
.claude/             # Claude Code setup: stage skills, hooks, permissions
```

## Costs and guardrails

- **EDGAR is free** — throttled to ≤8 req/s and cached under `data/cache/`.
- **Parallel.ai is paid** — every call path respects the per-run caps in
  `config/settings.yaml`. Use `--dry-run` before new batches.
- **LLM scoring and message drafting cost nothing in v1** — reasoning and copy run
  through Claude Code Haiku subagents. The OpenRouter provider in
  `src/pipeline/llm.py` is the v2 path.

## Adapting it to your own ICP

The default configuration targets companies that fit martechs.io's ideal customer
profile (ICP) — the kind of company it wants to sell to. If you sell something else,
you can point the whole pipeline at *your* ideal customer without touching code.

Run the **`/icp`** Claude
Code skill — it interviews you (best/worst customers → discriminating
attributes → point values → tier cutlines → sector/SIC vocabulary → services +
voice) and writes the answers to a **profile pack** under `profiles/<name>/`.
No code changes, no hand-editing YAML from a blank page.

A profile pack is a directory overlay: `config/` is the default pack (an
example configuration, not a hardcoded target), and `profiles/<name>/`
replaces any subset of its files —

- `settings.yaml` — universe band, sector → SIC vocabulary (free text, not a
  fixed enum), signal weights, qualify/disqualify/tier thresholds, per-run
  spend caps
- `services.yaml` — the service catalog that drives service-fit mapping,
  decision-maker role targeting (`people`), and message drafting
- `personas.yaml` — per-role pains/language/committee-role, used for `people`
  targeting and cold-email personalization
- `outbound_copywriter.md` — voice, offer, and proof points. The
  message-drafting subagents follow this file verbatim, and its banned-words
  list pairs with the deterministic QA gate in `src/pipeline/messages.py`

Any file missing from a pack falls back to the default `config/` version.
Select a pack with `--profile <name>` (or `AIPT_PROFILE=<name>`), inspect it
with `pipeline profile --show`, and sanity-check it with
`pipeline profile --validate`.

The signal taxonomy itself ([docs/SIGNALS.md](docs/SIGNALS.md)) is
vendor-agnostic — it detects public evidence of AI need; what you pitch
against that evidence is up to your pack.

## Contributing

The suite runs in under a second (`uv run pytest`) — keep it green. Conventions,
verification steps, and the PR flow are in [CONTRIBUTING.md](CONTRIBUTING.md).
If you use Claude Code, the repo ships with stage skills (`/status`, `/discover`,
`/ingest`, `/enrich`, `/score`, `/people`, `/outreach`, …) that encode the
correct orchestration for each pipeline stage, plus `/icp` to build a profile
pack interactively instead of running a pipeline stage.

## License

[MIT](LICENSE).

---

Built at [martechs.io](https://martechs.io). Scope stops at drafted outreach
sequences (qualified accounts + contacts + per-contact message drafts) — no
sending, no CRM pushes. AIPT reads only public data (SEC filings, public web
research); nothing it produces is investment advice.
