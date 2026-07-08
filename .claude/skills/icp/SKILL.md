---
name: icp
description: Interactively define a custom ICP (Ideal Customer Profile) and write it as a profile pack under profiles/<name>/ — sector vocabulary, scoring weights, tier cutlines, services, and voice. Use when a user wants to run AIPT for a different business or vertical than the default martechs.io pack, or wants to build or edit a profile pack.
---

# /icp — build a profile pack (6-step interview)

Pure conversation — no API calls, no subagents, no cost. Ends by writing
`profiles/<name>/settings.yaml` (and only the other files that differ from the
default pack: `services.yaml`, `personas.yaml`, `outbound_copywriter.md`,
`icp.md`) and validating the result. Profile packs are directory overlays —
any file missing from the pack falls back to `config/`.

## Step 1 — best/worst customers

Ask for 3-5 of the user's best customers (bought, renewed, or expanded) and
2-3 worst-fit ones (churned, never closed, wrong fit). Capture what each side
has in common — this contrast drives everything that follows.

## Step 2 — discriminating attributes

From the best/worst contrast, extract 3-6 attributes that actually separate
good-fit from bad-fit (e.g. "recent leadership change", "raised money in the
last 12mo", "no in-house AI/eng team") — not generic firmographics. Map each
onto the signal taxonomy shape in `docs/SIGNALS.md` (E1-E9 / P1-P6) where it
fits; flag genuinely new attributes as candidate signal types.

## Step 3 — point values

For each attribute, ask how strongly it should count relative to the others.
Translate into `scoring.weights` entries and `scoring.component_caps`
(intent / capability_gap / timing / commercial_fit), following the structure
in `config/settings.yaml`. Caps conventionally sum to 100 — the validator
warns (non-fatally) if they don't.

## Step 4 — tier cutlines

Ask for the qualify/disqualify thresholds, or infer them by asking where the
user's best/worst customers would land on the point scale from step 3. Write
`scoring.qualify_threshold` and `scoring.disqualify_below` (the gap between
them is the human-review band).

## Step 5 — sector/SIC vocabulary

Ask what verticals this ICP targets. Sector vocabulary is free text — any
lowercase key works; it does not need to match the built-in
saas/fintech/edtech/healthcare set. For each sector, collect SIC codes (if
known) and/or name/description keywords, matching the
`universe.sectors.<name>.sic` / `.keywords` / `.exclude_sic` shape in
`config/settings.yaml`. Also confirm `universe.exchanges` and the
`market_cap_min`/`market_cap_max` band.

## Step 6 — services + voice

Ask what services/offers this business sells → `services.yaml`, same shape as
`config/services.yaml` (per service: name, description, ideal_signals,
pitch_angle). Then ask about voice/tone for outreach copy — banned words, CTA
style, sign-off — → `outbound_copywriter.md`, modeled on
`config/outbound_copywriter.md`. Skip either file if the default pack's
version already fits (overlay fallback covers it).

## Write + validate

Ask for the pack `<name>` if not given, write the files to `profiles/<name>/`,
and optionally capture the interview's raw ICP narrative in
`profiles/<name>/icp.md` for future reference. Then validate:

```bash
uv run python -m pipeline --profile <name> profile --validate
```

Report errors back to the user and iterate — never leave an invalid pack on
disk silently. Close by showing how to use it:
`uv run python -m pipeline --profile <name> discover --dry-run` (or
`AIPT_PROFILE=<name>`).
