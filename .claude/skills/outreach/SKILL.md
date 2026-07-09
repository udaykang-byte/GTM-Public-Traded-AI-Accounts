---
name: outreach
description: Draft 4-step SPARK outreach sequences per contact for companies with contacts + fresh angles, via cheap Haiku subagents (no API cost). Use after /people, or when asked to write outreach, emails, or sequences.
---

# /outreach — draft sequences (v1: Haiku subagents, zero API cost)

Generation only: no sending, no CRM push. Copy rules live in
`config/outbound_copywriter.md` (Uday edits it like settings.yaml).

## Step 1 — prepare packets

```bash
uv run python -m pipeline messages --prepare
```

Writes ONE packet PER CONTACT to `data/message_queue/` (`TICKER__contact-slug.json`),
plus `_shared.json` (copywriter framework + services catalog + output schema + hard
rules). Companies without a fresh angle are skipped and reported — never message on
a stale hook. Use `--ticker X` for one company, `--force` to regenerate existing drafts.

Each packet also carries a `persona` block (pains, language, committee_role,
seniority) resolved from `config/personas.yaml` for that contact's role_bucket/title
— null if nothing matches. `_shared.json`'s hard rules tell the copywriter to use the
persona's pains/language as raw material, never to invent pains beyond it; a null
persona just means write from the packet's angles and verdict alone. Packets from
before this existed simply have no `persona` key — `messages --commit` handles both.

## Step 2 — spawn lean copywriter subagents

List queued packets (`data/message_queue/*.json`, ignore `_shared.json`), then
spawn **Agent tool subagents with `subagent_type: copywriter`** (the lean agent
in `.claude/agents/copywriter.md` — Bash+Write only, haiku, no repo baggage,
carries the full copy contract in its own system prompt), giving each subagent
a batch of **up to 3 packet paths** (prose needs smaller batches than scoring's
5). Spawn batches in parallel (single message, multiple Agent calls). The task
prompt is just:

> Write sequences for these packets. Packets:
> <absolute paths, one per line>

Rules:
- `subagent_type: copywriter` always — it pins haiku and the read-once/
  write-once contract. Never use opus/sonnet or a general-purpose agent for
  bulk copywriting, and never write copy in the main conversation.
- If a subagent fails on a packet, re-spawn just that packet.

## Step 3 — commit + QA re-spawn loop

```bash
uv run python -m pipeline messages --commit
```

Validates each result (schema + deterministic copy QA: subject shape, word counts,
banned words, one question CTA, no links in step 1, no placeholders, angle/contact
must match the packet) and upserts passing sequences to the `messages` table as
drafts. **Failed results stay in `data/message_results/`** with their packet still
queued.

If `failed QA` items are reported: re-spawn ONE `copywriter` subagent per failure, appending
the QA error text to the prompt ("Your previous draft failed QA: <errors>. Read the
packet and _shared.json again, rewrite the FULL sequence fixing these, overwrite
<output_path>."), then run `messages --commit` again. **Max 2 retry rounds** — report
survivors to the user instead of looping.

## Step 4 — report

Summarize per company: contact, archetype, service, and any QA warnings **verbatim**
— especially `unverified number` (tell the user to spot-check those bodies before
export; the digit-scan is the last automated line of defense against invented
metrics). Then suggest:

```bash
uv run python -m pipeline export --messages   # data/exports/messages.csv, one row per step
```

Never edit copy yourself in the main conversation, and never relax QA gates —
propose changes to `config/settings.yaml` (`messages:`) or
`config/outbound_copywriter.md` to the user instead.
