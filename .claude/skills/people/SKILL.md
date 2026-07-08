---
name: people
description: Find decision-makers (name, title, LinkedIn, public email) for qualified accounts via Parallel research. Use after /score qualifies companies, or when asked for contacts at an account.
---

# /people — decision-maker search for qualified accounts

Preview which companies/roles would be searched (no spend):

```bash
uv run python -m pipeline people --dry-run
```

Run (capped by config `people.max_companies_per_run`, default 10):

```bash
uv run python -m pipeline people --limit 5
# or a single account:
uv run python -m pipeline people --ticker XYZ
```

How targeting works: each qualified company's latest `service_fit` picks target roles — from `config/personas.yaml`'s `services` mapping when the active pack has one (e.g. lead-gen fit → CMO/CRO/VP Marketing; custom agents → CTO/CIO), else the legacy flat `people.roles_by_service` list — plus `always_include_roles` (CEO — micro-caps often buy top-down). One Parallel task per company researches names, exact titles, LinkedIn URLs, and **publicly listed** emails only (source URL + confidence stored; no guessing, no pattern-inventing).

Personas also carry pains/language per role (not just target titles) — `/outreach` attaches the matched persona's pains and language to each contact's message packet for personalization, so a contact's `role_bucket` (even a legacy value like "CEO" already in the DB) or title is enough to pull role-specific copy material later.

Run mechanics:
- **Run multi-account batches in the background** and report from the final stats + DB — tasks are created up front and polled together, so a 10-account batch ≈ one task's duration (~2–4 min).
- If one account's task fails or times out, the batch continues without it and the company keeps its `qualified` status; retry just that account with `--ticker X`.

After running:
- Table per company: name, title, role bucket, LinkedIn, email (or —), confidence.
- Companies move to `contacts_found`.
- Flag companies where key roles came back empty (tiny micro-caps often have no CMO — the CEO is the buyer).
- Suggest `uv run python -m pipeline export` for a CSV of qualified accounts + contacts.

Next stage: `/outreach` drafts per-contact message sequences from angles (v2). Still no sending or CRM push.
