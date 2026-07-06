# Outreach Angles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every qualified account carries at least one fresh, structured, evidence-cited outreach angle (funding event, leadership hire, or AI move), stored independently in a new `angles` table, with a deterministic angle-required qualify gate.

**Architecture:** A new `angles` entity (separate from `signals`, deduped by fingerprint, never bulk-wiped) is fed by two collectors — a free EDGAR funding-events scanner and an upgraded "deep" Parallel task gated to companies at/near the qualify bar. Scoring packets include active angles; the Haiku scorer ranks them and picks a `primary_angle`; `score --commit` enforces the tightened gate deterministically.

**Tech Stack:** Python 3.12, uv, Pydantic v2, Typer, Supabase (postgrest client), edgartools, Parallel.ai Task API, pytest.

**Spec:** `docs/superpowers/specs/2026-07-06-outreach-angles-design.md` — read it before starting.

## Global Constraints

- Every command runs via `uv` — `uv run python -m pipeline …`, `uv run pytest`. The project directory contains a space: **always quote paths** in shell commands.
- Secrets live only in `.env` (gitignored). Never commit, print, or log key values.
- All DB access goes through `src/pipeline/db.py`. Schema changes = edit `sql/schema.sql` (idempotent DDL only) + `apply-schema`. All schema changes in this plan are **additive**.
- EDGAR requests: identity-stamped, throttled ≤8 req/s, cached under `data/cache/` — the edgartools client already does this; don't bypass it. Filter by filing-index metadata before downloading any document text.
- Parallel.ai costs money: the deep tier is capped by `enrich.deep.max_tasks_per_run` (15). Never call Parallel outside a cap. `--dry-run` must never spend.
- LLM scoring stays on Claude Code Haiku subagents (file-based packets). No paid LLM APIs.
- Tests are offline and fast (no network, no DB, suite <1s). A PostToolUse hook runs `uv run pytest -q` automatically after every edit to `src/pipeline/*.py` or `tests/*.py` — a failing suite will interrupt you; fix before moving on.
- Qualification thresholds and angle freshness windows in `config/settings.yaml` are human decisions — implement exactly the values in this plan; don't tune them.
- EDGAR one-signal-per-type dedupe applies to `signals` only. Angles keep one row **per event** (fingerprint-deduped).
- Commit after every task with the message given in its final step. Co-author line: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Config keys + `angles` schema

**Files:**
- Modify: `config/settings.yaml`
- Modify: `sql/schema.sql`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `SETTINGS["angles"]["freshness_days"][family]`, `SETTINGS["angles"]["strength"]{full_days,floor}`, `SETTINGS["enrich"]["deep"]{max_tasks_per_run,processor,poll_timeout_seconds}`, `SETTINGS["scoring"]["require_angle"]`; DB tables/columns: `angles` (with `unique (company_cik, fingerprint)`), `scores.angle_ranking`, `scores.primary_angle`, `scores.gate_reason`.

- [ ] **Step 1: Add config keys to `config/settings.yaml`**

Add a top-level `angles:` block (after the `scoring:` block), a `deep:` sub-block under `enrich:`, and `require_angle` under `scoring:`. Exact insertions:

```yaml
# under the existing `enrich:` key, alongside edgar:/parallel:
  deep:
    max_tasks_per_run: 15    # deep Parallel tasks per run (score-gated tier)
    processor: base
    poll_timeout_seconds: 900
```

```yaml
# inside the existing `scoring:` block, right under disqualify_below:
  require_angle: true        # qualified needs >=1 active outreach angle (v2 gate); false = v1 behavior
```

```yaml
# new top-level block, placed after `scoring:` and before `people:`
angles:
  # outreach-angle freshness windows: event older than this -> stale, never
  # satisfies the qualify gate, never ranks as primary
  freshness_days:
    funding: 365
    leadership: 365
    ai_move: 270
  # strength = recency decay x evidence quality (angles.py)
  strength:
    full_days: 90
    floor: 0.25
```

- [ ] **Step 2: Add DDL to `sql/schema.sql`**

Insert after the `scores` table block (keep the file's idempotent style; codebase uses identity bigint PKs, not uuid — follow that):

```sql
create table if not exists angles (
  id             bigint generated always as identity primary key,
  company_cik    bigint not null references companies (cik) on delete cascade,
  family         text not null check (family in ('funding','leadership','ai_move')),
  headline       text not null,
  details        jsonb not null default '{}'::jsonb,
  evidence_url   text,
  evidence_quote text,
  event_date     date not null,
  source         text not null check (source in ('edgar','parallel')),
  strength       numeric not null default 0,
  status         text not null default 'active' check (status in ('active','stale')),
  fingerprint    text not null,
  collected_at   timestamptz not null default now(),
  unique (company_cik, fingerprint)
);
create index if not exists angles_company_idx on angles (company_cik);
create index if not exists angles_family_idx on angles (family);

-- outreach-angle columns on scores (idempotent for pre-existing installs)
alter table scores add column if not exists angle_ranking jsonb not null default '[]'::jsonb;
alter table scores add column if not exists primary_angle jsonb;
alter table scores add column if not exists gate_reason text not null default '';
```

Also add `alter table angles enable row level security;` next to the other RLS lines at the bottom of the file.

- [ ] **Step 3: Verify the suite still passes and YAML parses**

Run: `cd "/Users/udaykang/AI_Public Traded" && uv run python -c "from pipeline.config import SETTINGS; print(SETTINGS['angles']['freshness_days'], SETTINGS['enrich']['deep']['max_tasks_per_run'], SETTINGS['scoring']['require_angle'])"`
Expected: `{'funding': 365, 'leadership': 365, 'ai_move': 270} 15 True`

Run: `uv run pytest -q`
Expected: `25 passed`

- [ ] **Step 4: Apply the schema**

Run: `uv run python -m pipeline apply-schema`
Expected: `Schema applied ✔`
(If `SUPABASE_DB_URL` is missing from `.env`, STOP and ask the user to paste `sql/schema.sql` into the Supabase SQL editor instead.)

- [ ] **Step 5: Commit**

```bash
git add config/settings.yaml sql/schema.sql
git commit -m "feat: angles config + schema (angles table, scores angle columns)"
```

---

### Task 2: Angle models + ScoreVerdict extension

**Files:**
- Modify: `src/pipeline/models.py`
- Test: `tests/test_angles.py` (create)

**Interfaces:**
- Consumes: Task 1 config keys (not directly — models are config-free).
- Produces (exact, used by every later task):
  - `AngleFamily(str, Enum)` with members `funding`, `leadership`, `ai_move`
  - `FundingDetails(amount_usd: float | None, instrument: Literal["follow_on","atm","pipe","shelf","debt","other"], announced: date | None, use_of_proceeds: str | None, filing_type: str | None)`
  - `LeadershipDetails(role: str, person_name: str | None, start_date: date | None, first_in_role: bool, mandate_quote: str | None)`
  - `AiMoveDetails(initiative: str, move_type: Literal["product_launch","partnership","pilot","exec_statement"], partner: str | None, exec_quote: str | None, announced: date | None)`
  - `Angle(company_cik: int, family: AngleFamily, headline: str, details: dict, evidence_url: str | None, evidence_quote: str | None, event_date: date, source: str, strength: float, status: str, fingerprint: str)` — `details` is validated against the family's details model on construction
  - `AngleRef(fingerprint: str, family: AngleFamily, message_hook: str)`
  - `PrimaryAngle(fingerprint: str, family: AngleFamily, why_this_angle: str)`
  - `ScoreVerdict` gains `angle_ranking: list[AngleRef] = []` and `primary_angle: PrimaryAngle | None = None` (both optional → old verdict JSON still validates)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_angles.py`:

```python
from datetime import date

import pytest
from pydantic import ValidationError

from pipeline.models import Angle, ScoreVerdict


def make_angle(**overrides):
    base = dict(
        company_cik=999, family="funding", headline="Offering priced ~$12M — 424B5 filed 2026-05-01",
        details={"amount_usd": 12_000_000, "instrument": "follow_on", "filing_type": "424B5"},
        evidence_url="https://www.sec.gov/x", evidence_quote="use the net proceeds for growth",
        event_date=date(2026, 5, 1), source="edgar", strength=1.0,
        fingerprint="funding:0001234-26-000042",
    )
    base.update(overrides)
    return Angle(**base)


def test_angle_validates_funding_details():
    a = make_angle()
    assert a.details["instrument"] == "follow_on"
    assert a.details["amount_usd"] == 12_000_000


def test_angle_rejects_bad_instrument():
    with pytest.raises(ValidationError):
        make_angle(details={"instrument": "ico"})


def test_leadership_details_require_role():
    with pytest.raises(ValidationError):
        make_angle(family="leadership", details={"person_name": "Jane Roe"})
    a = make_angle(family="leadership", details={"role": "CRO", "start_date": "2026-04-01"})
    assert a.details["role"] == "CRO"


def test_ai_move_details_require_initiative():
    with pytest.raises(ValidationError):
        make_angle(family="ai_move", details={"partner": "Google"})


def test_score_verdict_backward_compatible_without_angle_fields():
    v = ScoreVerdict(
        ticker="TST", intent=10, capability_gap=10, timing=10, commercial_fit=10,
        profile="laggard", service_fit=[], reasoning="r", why_now="w",
    )
    assert v.angle_ranking == []
    assert v.primary_angle is None


def test_score_verdict_accepts_angle_fields():
    v = ScoreVerdict(
        ticker="TST", intent=10, capability_gap=10, timing=10, commercial_fit=10,
        profile="adopter", service_fit=[], reasoning="r", why_now="w",
        angle_ranking=[{"fingerprint": "f1", "family": "funding", "message_hook": "hook"}],
        primary_angle={"fingerprint": "f1", "family": "funding", "why_this_angle": "freshest"},
    )
    assert v.primary_angle.fingerprint == "f1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_angles.py -v`
Expected: FAIL — `ImportError: cannot import name 'Angle' from 'pipeline.models'`

- [ ] **Step 3: Implement in `src/pipeline/models.py`**

Add `Literal` to the typing imports and `model_validator` to the pydantic imports at the top:

```python
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator
```

Add after the `Signal` class:

```python
class AngleFamily(str, Enum):
    funding = "funding"
    leadership = "leadership"
    ai_move = "ai_move"


class FundingDetails(BaseModel):
    amount_usd: float | None = None
    instrument: Literal["follow_on", "atm", "pipe", "shelf", "debt", "other"] = "other"
    announced: date | None = None
    use_of_proceeds: str | None = None
    filing_type: str | None = None


class LeadershipDetails(BaseModel):
    role: str
    person_name: str | None = None
    start_date: date | None = None
    first_in_role: bool = False
    mandate_quote: str | None = None


class AiMoveDetails(BaseModel):
    initiative: str
    move_type: Literal["product_launch", "partnership", "pilot", "exec_statement"] = "product_launch"
    partner: str | None = None
    exec_quote: str | None = None
    announced: date | None = None


ANGLE_DETAILS_MODELS: dict[str, type[BaseModel]] = {
    "funding": FundingDetails,
    "leadership": LeadershipDetails,
    "ai_move": AiMoveDetails,
}


class Angle(BaseModel):
    """One dated outreach event. Deduped by fingerprint; never bulk-wiped
    (unlike signals). Families and semantics: docs/SIGNALS.md."""

    company_cik: int
    family: AngleFamily
    headline: str
    details: dict = Field(default_factory=dict)
    evidence_url: str | None = None
    evidence_quote: str | None = None
    event_date: date
    source: str  # edgar | parallel
    strength: float = 0
    status: str = "active"  # active | stale
    fingerprint: str

    @model_validator(mode="after")
    def _validate_details(self):
        model = ANGLE_DETAILS_MODELS[self.family.value]
        self.details = model.model_validate(self.details).model_dump(mode="json")
        return self
```

Add after the `ServiceFit` class (before `ScoreVerdict`):

```python
class AngleRef(BaseModel):
    fingerprint: str
    family: AngleFamily
    message_hook: str = Field(description="One-sentence opening line a seller could use for this angle")


class PrimaryAngle(BaseModel):
    fingerprint: str
    family: AngleFamily
    why_this_angle: str
```

Add two fields to `ScoreVerdict`, after `confidence`:

```python
    angle_ranking: list[AngleRef] = Field(
        default_factory=list,
        description="All packet angles ranked by outreach power, strongest first; [] if the packet has no angles",
    )
    primary_angle: PrimaryAngle | None = Field(
        default=None, description="The single angle outreach should lead with; null if no angles"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_angles.py -v`
Expected: 6 PASS. Then `uv run pytest -q` → `31 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/models.py tests/test_angles.py
git commit -m "feat: Angle model with per-family typed details; verdict angle fields"
```

---

### Task 3: Angle logic (`angles.py`) + DB access

**Files:**
- Create: `src/pipeline/angles.py`
- Modify: `src/pipeline/db.py`
- Test: `tests/test_angles.py` (extend)

**Interfaces:**
- Consumes: `SETTINGS["angles"]` (Task 1), `Angle` (Task 2).
- Produces:
  - `angles.freshness_days(family: str) -> int`
  - `angles.is_fresh(family: str, event_date, today: date | None = None) -> bool` — accepts date or ISO string (DB rows carry strings)
  - `angles.compute_strength(family: str, event_date, has_quote: bool, has_url: bool, today: date | None = None) -> float`
  - `angles.make_fingerprint(family: str, *parts) -> str` — e.g. `"funding:0001234-26-000042"`
  - `angles.slim(row: dict, today: date | None = None) -> dict` — packet-shaped subset with `age_days`
  - `angles.select_deep_targets(candidates: list[dict], totals: dict[int, float], cap: int) -> list[dict]`
  - `db.upsert_angles(angles: list[Angle]) -> int`, `db.get_angles(cik: int) -> list[dict]`, `db.all_angles() -> dict[int, list[dict]]`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_angles.py`)

```python
from datetime import timedelta

from pipeline import angles


def test_fresh_within_window():
    assert angles.is_fresh("funding", date.today() - timedelta(days=100))
    assert angles.is_fresh("ai_move", (date.today() - timedelta(days=100)).isoformat())


def test_stale_beyond_window():
    assert not angles.is_fresh("funding", date.today() - timedelta(days=400))
    assert not angles.is_fresh("ai_move", date.today() - timedelta(days=300))  # 270d window


def test_strength_full_when_recent_with_full_evidence():
    d = date.today() - timedelta(days=30)
    assert angles.compute_strength("funding", d, has_quote=True, has_url=True) == 1.0


def test_strength_decays_to_floor_at_window_edge():
    d = date.today() - timedelta(days=365)
    s = angles.compute_strength("funding", d, has_quote=True, has_url=True)
    assert abs(s - 0.25) < 0.01


def test_strength_zero_when_stale():
    d = date.today() - timedelta(days=400)
    assert angles.compute_strength("funding", d, has_quote=True, has_url=True) == 0.0


def test_strength_evidence_quality_tiers():
    d = date.today() - timedelta(days=10)
    assert angles.compute_strength("funding", d, has_quote=False, has_url=True) == 0.7
    assert angles.compute_strength("funding", d, has_quote=False, has_url=False) == 0.4


def test_fingerprint_prefixed_and_stable():
    fp = angles.make_fingerprint("leadership", "CRO", "2026-04-01")
    assert fp == "leadership:cro:2026-04-01"
    assert fp == angles.make_fingerprint("leadership", "CRO", "2026-04-01")


def test_slim_includes_age_days():
    row = make_angle().model_dump(mode="json")
    s = angles.slim(row)
    assert set(s) == {"fingerprint", "family", "headline", "details", "event_date",
                      "strength", "evidence_url", "evidence_quote", "age_days"}
    assert isinstance(s["age_days"], int)


def test_select_deep_targets_orders_by_total_and_caps():
    cands = [{"cik": 1, "ticker": "A"}, {"cik": 2, "ticker": "B"}, {"cik": 3, "ticker": "C"}]
    totals = {1: 50, 2: 70, 3: 60}
    picked = angles.select_deep_targets(cands, totals, cap=2)
    assert [c["ticker"] for c in picked] == ["B", "C"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_angles.py -v`
Expected: new tests FAIL — `ModuleNotFoundError: No module named 'pipeline.angles'` (or ImportError).

- [ ] **Step 3: Implement `src/pipeline/angles.py`**

```python
"""Outreach-angle logic: freshness, strength, fingerprints, deep-tier selection.

An angle is a dated, structured outreach event (funding / leadership / ai_move)
stored in the `angles` table — see docs/SIGNALS.md and the 2026-07-06
outreach-angles spec. Signals feed scoring; angles feed outreach copy.
"""
from __future__ import annotations

from datetime import date, datetime

from pipeline.config import SETTINGS

DEFAULT_WINDOWS = {"funding": 365, "leadership": 365, "ai_move": 270}


def freshness_days(family: str) -> int:
    cfg = SETTINGS.get("angles", {}).get("freshness_days", {})
    return int(cfg.get(family, DEFAULT_WINDOWS.get(family, 365)))


def _age_days(event_date, today: date | None = None) -> int | None:
    if event_date is None:
        return None
    if isinstance(event_date, str):
        try:
            event_date = (
                datetime.fromisoformat(event_date).date()
                if "T" in event_date else date.fromisoformat(event_date[:10])
            )
        except ValueError:
            return None
    return max(((today or date.today()) - event_date).days, 0)


def is_fresh(family: str, event_date, today: date | None = None) -> bool:
    age = _age_days(event_date, today)
    return age is not None and age <= freshness_days(family)


def compute_strength(
    family: str, event_date, has_quote: bool, has_url: bool, today: date | None = None
) -> float:
    """Recency decay x evidence quality. Stale -> 0."""
    cfg = SETTINGS.get("angles", {}).get("strength", {})
    full = int(cfg.get("full_days", 90))
    floor = float(cfg.get("floor", 0.25))
    window = freshness_days(family)
    age = _age_days(event_date, today)
    if age is None or age > window:
        return 0.0
    if age <= full:
        recency = 1.0
    else:
        frac = (age - full) / max(window - full, 1)
        recency = 1.0 - frac * (1.0 - floor)
    quality = 1.0 if (has_quote and has_url) else 0.7 if has_url else 0.4
    return round(recency * quality, 3)


def make_fingerprint(family: str, *parts) -> str:
    norm = [str(p).strip().lower().replace(" ", "-") for p in parts if p is not None]
    return ":".join([family, *norm])


def slim(row: dict, today: date | None = None) -> dict:
    """Packet-shaped angle: what the scorer needs, nothing else."""
    keys = ("fingerprint", "family", "headline", "details", "event_date",
            "strength", "evidence_url", "evidence_quote")
    out = {k: row.get(k) for k in keys}
    out["age_days"] = _age_days(row.get("event_date"), today)
    return out


def select_deep_targets(candidates: list[dict], totals: dict[int, float], cap: int) -> list[dict]:
    """Deep-tier selection: highest latest score first, capped."""
    ranked = sorted(candidates, key=lambda c: totals.get(int(c["cik"]), 0), reverse=True)
    return ranked[: max(cap, 0)]
```

- [ ] **Step 4: Add DB functions to `src/pipeline/db.py`**

Add `Angle` to the models import line:

```python
from pipeline.models import Angle, Company, Contact, Signal, Status
```

Add a new section after the signals section:

```python
# ---------- angles ----------

def upsert_angles(angles: list[Angle]) -> int:
    """Dedupe by (company_cik, fingerprint): new events insert, known events
    refresh strength/status. Never bulk-deletes — angles accumulate."""
    if not angles:
        return 0
    rows = [a.model_dump(mode="json") for a in angles]
    client().table("angles").upsert(rows, on_conflict="company_cik,fingerprint").execute()
    return len(rows)


def get_angles(cik: int) -> list[dict]:
    return (
        client().table("angles").select("*").eq("company_cik", cik)
        .order("strength", desc=True).execute().data or []
    )


def all_angles() -> dict[int, list[dict]]:
    """Every angle, grouped by company_cik — mirrors all_signals()."""
    grouped: dict[int, list[dict]] = {}
    page, offset = 1000, 0
    while True:
        rows = (
            client().table("angles").select("*")
            .order("id").range(offset, offset + page - 1).execute().data
        ) or []
        for r in rows:
            grouped.setdefault(int(r["company_cik"]), []).append(r)
        if len(rows) < page:
            return grouped
        offset += page
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_angles.py -v`
Expected: all PASS. Then `uv run pytest -q` → `40 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/angles.py src/pipeline/db.py tests/test_angles.py
git commit -m "feat: angle freshness/strength logic + angles DB access"
```

---

### Task 4: EDGAR funding-events collector

**Files:**
- Create: `src/pipeline/funding_events.py`
- Test: `tests/test_funding_events.py` (create)

**Interfaces:**
- Consumes: `Angle`, `angles.make_fingerprint/compute_strength/freshness_days` (Tasks 2–3), `edgar_signals._filing_items/_filing_url` (existing).
- Produces:
  - `funding_events.funding_angles(edgar_company, company: dict) -> list[Angle]` (testable core; takes any object with `.get_filings(form=)`)
  - `funding_events.collect(company: dict) -> tuple[list[Angle], list[str]]` (CLI entrypoint, mirrors `edgar_signals.collect`)
  - `funding_events._extract_amount(text: str) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_funding_events.py` (same fake-object style as `tests/test_edgar_signals.py`):

```python
from datetime import date, timedelta

from pipeline import funding_events as fe

COMPANY = {"cik": 999, "ticker": "TST"}
RECENT = date.today() - timedelta(days=30)


class FakeFiling:
    def __init__(self, form, text="", filing_date=RECENT, items=None, accession="0001234-26-000042"):
        self.form = form
        self.filing_date = filing_date
        self.items = items
        self._text = text
        self.text_calls = 0
        self.accession_no = accession

    def text(self):
        self.text_calls += 1
        return self._text


class FakeCompany:
    def __init__(self, filings_by_form):
        self._by_form = filings_by_form

    def get_filings(self, form=None):
        return self._by_form.get(form, [])


def test_extract_amount_million_words():
    assert fe._extract_amount("gross proceeds of approximately $12.5 million") == 12_500_000


def test_extract_amount_full_digits():
    assert fe._extract_amount("aggregate offering price of $50,000,000") == 50_000_000


def test_extract_amount_ignores_par_value_noise():
    assert fe._extract_amount("par value $0.001 per share; no proceeds language") is None


def test_424b5_yields_follow_on_angle():
    f = FakeFiling("424B5", text="We estimate gross proceeds of $12.0 million. "
                                 "We intend to use the net proceeds for sales expansion.")
    out = fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY)
    assert len(out) == 1
    a = out[0]
    assert a.family.value == "funding"
    assert a.details["instrument"] == "follow_on"
    assert a.details["amount_usd"] == 12_000_000
    assert a.fingerprint == "funding:0001234-26-000042"
    assert a.event_date == RECENT
    assert "use the net proceeds" in (a.evidence_quote or "")


def test_atm_detected_from_prospectus_text():
    f = FakeFiling("424B5", text="This prospectus relates to our at-the-market offering program "
                                 "with aggregate offering price of up to $25 million.")
    out = fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY)
    assert out[0].details["instrument"] == "atm"


def test_s3_shelf_recorded_even_without_amount():
    f = FakeFiling("S-3", text="")
    out = fe.funding_angles(FakeCompany({"S-3": [f]}), COMPANY)
    assert out[0].details["instrument"] == "shelf"
    assert out[0].details["amount_usd"] is None


def test_8k_302_yields_pipe():
    f = FakeFiling("8-K", items=["3.02", "9.01"],
                   text="entered into a securities purchase agreement for gross proceeds of $8 million")
    out = fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY)
    assert out[0].details["instrument"] == "pipe"


def test_8k_101_credit_agreement_yields_debt():
    f = FakeFiling("8-K", items=["1.01"],
                   text="entered into a credit agreement providing a term loan with principal amount of $20 million")
    out = fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY)
    assert out[0].details["instrument"] == "debt"


def test_8k_101_without_financing_language_skipped():
    f = FakeFiling("8-K", items=["1.01"], text="entered into a lease agreement for office space")
    assert fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY) == []


def test_8k_other_items_skip_download():
    f = FakeFiling("8-K", items=["7.01"], text="conference presentation")
    assert fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY) == []
    assert f.text_calls == 0


def test_old_filing_beyond_window_skipped():
    f = FakeFiling("424B5", text="gross proceeds of $12 million",
                   filing_date=date.today() - timedelta(days=400))
    assert fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_funding_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.funding_events'`

- [ ] **Step 3: Implement `src/pipeline/funding_events.py`**

```python
"""EDGAR funding-event collector -> outreach angles (family 'funding').

Free tier: filing-index metadata decides what to download; amount and
use-of-proceeds extraction is best-effort text parsing (nullable — the filing
itself is the evidence). Angle semantics: pipeline/angles.py, docs/SIGNALS.md.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from pipeline import angles as angles_mod
from pipeline.edgar_signals import _filing_items, _filing_url, _init_edgar
from pipeline.models import Angle

OFFERING_FORMS = ("424B1", "424B2", "424B3", "424B4", "424B5")
SHELF_FORMS = ("S-3", "S-3ASR")
DEBT_PHRASES = ["credit agreement", "loan and security agreement", "term loan", "revolving credit"]
PIPE_PHRASES = ["securities purchase agreement", "private placement", "note purchase agreement"]
ATM_PHRASE = "at-the-market"

AMOUNT_KEYWORDS = ["gross proceeds", "aggregate offering price", "principal amount", "aggregate purchase price"]
AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion)?", re.IGNORECASE)
PROCEEDS_RE = re.compile(r"(?i)(?:intend to )?use\s+the\s+net\s+proceeds[^.]{0,400}\.")

INSTRUMENT_LABEL = {
    "shelf": "Shelf registration", "atm": "ATM program", "follow_on": "Offering priced",
    "pipe": "PIPE/private placement", "debt": "Debt facility",
}


def _extract_amount(text: str) -> float | None:
    """Largest dollar figure within 300 chars of a proceeds keyword."""
    if not text:
        return None
    lower = text.lower()
    best: float | None = None
    for kw in AMOUNT_KEYWORDS:
        for m in re.finditer(re.escape(kw), lower):
            window = text[max(0, m.start() - 300): m.start() + 300]
            for am in AMOUNT_RE.finditer(window):
                val = float(am.group(1).replace(",", ""))
                unit = (am.group(2) or "").lower()
                if unit == "million":
                    val *= 1e6
                elif unit == "billion":
                    val *= 1e9
                if val < 100_000:  # "$0.001 par value" noise
                    continue
                if best is None or val > best:
                    best = val
    return best


def _use_of_proceeds(text: str) -> str | None:
    m = PROCEEDS_RE.search(text or "")
    return " ".join(m.group(0).split())[:350] if m else None


def _accession(filing) -> str:
    return str(getattr(filing, "accession_no", "") or getattr(filing, "accession_number", ""))


def _angle(company: dict, filing, form: str, instrument: str, text: str) -> Angle:
    cik = int(company["cik"])
    fdate = filing.filing_date
    amount = _extract_amount(text)
    quote = _use_of_proceeds(text)
    url = _filing_url(cik, filing)
    amt = f" ~${amount / 1e6:.0f}M" if amount else ""
    return Angle(
        company_cik=cik, family="funding",
        headline=f"{INSTRUMENT_LABEL[instrument]}{amt} — {form} filed {fdate}",
        details={
            "amount_usd": amount, "instrument": instrument, "announced": fdate,
            "use_of_proceeds": quote, "filing_type": form,
        },
        evidence_url=url, evidence_quote=quote, event_date=fdate, source="edgar",
        strength=angles_mod.compute_strength("funding", fdate, bool(quote), bool(url)),
        fingerprint=angles_mod.make_fingerprint("funding", _accession(filing)),
    )


def funding_angles(edgar_company, company: dict) -> list[Angle]:
    cutoff = date.today() - timedelta(days=angles_mod.freshness_days("funding"))
    out: list[Angle] = []
    seen: set[str] = set()

    for form in OFFERING_FORMS + SHELF_FORMS:
        try:
            filings = list(edgar_company.get_filings(form=form))
        except Exception:
            continue
        for filing in filings:
            fdate = getattr(filing, "filing_date", None)
            if fdate is None or fdate < cutoff:
                break  # newest-first
            try:
                text = filing.text() or ""
            except Exception:
                text = ""
            if form in SHELF_FORMS:
                instrument = "shelf"
            else:
                instrument = "atm" if ATM_PHRASE in text.lower() else "follow_on"
            a = _angle(company, filing, form, instrument, text)
            if a.fingerprint not in seen:
                seen.add(a.fingerprint)
                out.append(a)

    try:
        eightks = list(edgar_company.get_filings(form="8-K"))
    except Exception:
        eightks = []
    for filing in eightks:
        fdate = getattr(filing, "filing_date", None)
        if fdate is None or fdate < cutoff:
            break
        items = _filing_items(filing)
        if not ({"3.02", "1.01"} & items):
            continue
        try:
            text = filing.text() or ""
        except Exception:
            continue
        lower = text.lower()
        if "3.02" in items:
            instrument = "pipe"
        elif any(p in lower for p in DEBT_PHRASES):
            instrument = "debt"
        elif any(p in lower for p in PIPE_PHRASES):
            instrument = "pipe"
        else:
            continue  # Item 1.01 with no financing language — not a funding event
        a = _angle(company, filing, "8-K " + "/".join(sorted({"3.02", "1.01"} & items)), instrument, text)
        if a.fingerprint not in seen:
            seen.add(a.fingerprint)
            out.append(a)
    return out


def collect(company: dict) -> tuple[list[Angle], list[str]]:
    """Run the funding collector for one company. Mirrors edgar_signals.collect."""
    _init_edgar()
    from edgar import Company as EdgarCompany

    try:
        ec = EdgarCompany(int(company["cik"]))
    except Exception as exc:
        return [], [f"edgar company lookup failed: {exc}"]
    try:
        return funding_angles(ec, company), []
    except Exception as exc:
        return [], [f"funding: {type(exc).__name__}: {exc}"]
```

Note: `Angle(...)` with `details={"announced": fdate, ...}` works because `FundingDetails.announced` is a `date` and the validator dumps back to JSON mode. The `filing_type` for 8-Ks reads e.g. `"8-K 3.02"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_funding_events.py -v`
Expected: 11 PASS. Then `uv run pytest -q` → `51 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/funding_events.py tests/test_funding_events.py
git commit -m "feat: EDGAR funding-events collector (S-3/424B/8-K -> funding angles)"
```

---

### Task 5: Deep Parallel task (leadership + AI-move angles)

**Files:**
- Modify: `src/pipeline/parallel_signals.py`
- Test: `tests/test_parallel_signals.py` (extend)

**Interfaces:**
- Consumes: `ENRICH_SCHEMA`, `_input_text`, `_signals_from_result`, `run_tasks_batch` (existing); `Angle`, `angles.make_fingerprint/compute_strength` (Tasks 2–3).
- Produces:
  - `DEEP_SCHEMA` (superset of `ENRICH_SCHEMA` with `leadership_hires`, `ai_moves`, `funding_news` arrays)
  - `_deep_input_text(company: dict) -> str`
  - `_angles_from_result(company: dict, result: dict) -> tuple[list[Angle], list[str]]`
  - `collect_deep_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[Angle], list[str]]]`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_parallel_signals.py`; reuse its existing company/result fixture style)

```python
from pipeline.parallel_signals import DEEP_SCHEMA, _angles_from_result

DEEP_COMPANY = {"cik": 999, "ticker": "TST", "name": "Test Co", "sector_bucket": "saas", "market_cap": 1e8}


def _deep_result(extra):
    content = {k: {"found": False, "summary": "n/a"} for k in (
        "ai_job_postings", "gtm_hiring", "ai_announcements",
        "product_ai_gap", "martech_stack", "exec_ai_commentary")}
    content.update(extra)
    return {"content": content, "basis": []}


def test_deep_schema_extends_enrich_schema():
    assert "leadership_hires" in DEEP_SCHEMA["properties"]
    assert "ai_job_postings" in DEEP_SCHEMA["properties"]


def test_leadership_hire_maps_to_angle():
    result = _deep_result({"leadership_hires": [{
        "role": "Chief Revenue Officer", "person_name": "Jane Roe",
        "start_date": "2026-05-01", "mandate_quote": "My mandate is pipeline efficiency",
        "source_url": "https://news.example/cro",
    }]})
    angles_out, warnings = _angles_from_result(DEEP_COMPANY, result)
    assert warnings == []
    a = angles_out[0]
    assert a.family.value == "leadership"
    assert a.details["person_name"] == "Jane Roe"
    assert a.fingerprint == "leadership:chief-revenue-officer:2026-05-01"
    assert str(a.event_date) == "2026-05-01"


def test_ai_move_maps_to_angle():
    result = _deep_result({"ai_moves": [{
        "initiative": "Acme AI Copilot", "move_type": "product_launch",
        "partner": "Google", "announced": "2026-04-15", "source_url": "https://pr.example/x",
    }]})
    angles_out, warnings = _angles_from_result(DEEP_COMPANY, result)
    assert angles_out[0].family.value == "ai_move"
    assert angles_out[0].details["partner"] == "Google"


def test_undated_item_dropped_with_warning():
    result = _deep_result({"leadership_hires": [{"role": "CRO"}]})
    angles_out, warnings = _angles_from_result(DEEP_COMPANY, result)
    assert angles_out == []
    assert any("no date" in w for w in warnings)


def test_invalid_item_isolated_not_fatal():
    result = _deep_result({"ai_moves": [
        {"move_type": "product_launch", "announced": "2026-04-15"},  # missing initiative
        {"initiative": "Real Thing", "announced": "2026-04-15"},
    ]})
    angles_out, warnings = _angles_from_result(DEEP_COMPANY, result)
    assert len(angles_out) == 1
    assert len(warnings) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parallel_signals.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'DEEP_SCHEMA'`

- [ ] **Step 3: Implement in `src/pipeline/parallel_signals.py`**

Add imports at top:

```python
from pydantic import ValidationError

from pipeline import angles as angles_mod
from pipeline.models import Angle, Signal
```

(replace the existing `from pipeline.models import Signal` line.)

Add after `ENRICH_SCHEMA` / `AREA_TO_SIGNAL`:

```python
DEEP_EXTRA_PROPS = {
    "leadership_hires": {
        "type": "array",
        "description": "C-level/VP executives who joined in the last 12 months",
        "items": {"type": "object", "properties": {
            "role": {"type": "string", "description": "Title, e.g. 'Chief Revenue Officer'"},
            "person_name": {"type": "string"},
            "start_date": {"type": "string", "description": "YYYY-MM-DD; announcement date if start unknown"},
            "mandate_quote": {"type": "string", "description": "Verbatim quote about their mandate/priorities"},
            "source_url": {"type": "string"},
        }, "required": ["role"]},
    },
    "ai_moves": {
        "type": "array",
        "description": "AI initiatives: product launches, partnerships, pilots, notable exec statements",
        "items": {"type": "object", "properties": {
            "initiative": {"type": "string", "description": "Name of the product/initiative/partnership"},
            "move_type": {"type": "string", "description": "product_launch | partnership | pilot | exec_statement"},
            "partner": {"type": "string"},
            "exec_quote": {"type": "string"},
            "announced": {"type": "string", "description": "YYYY-MM-DD"},
            "source_url": {"type": "string"},
        }, "required": ["initiative"]},
    },
    "funding_news": {
        "type": "array",
        "description": "Press coverage of capital raises in the last 12 months (context; SEC filings are the source of record)",
        "items": {"type": "object", "properties": {
            "description": {"type": "string"},
            "amount_usd": {"type": "number"},
            "instrument": {"type": "string", "description": "follow_on | atm | pipe | shelf | debt | other"},
            "announced": {"type": "string", "description": "YYYY-MM-DD"},
            "quote": {"type": "string"},
            "source_url": {"type": "string"},
        }, "required": ["description"]},
    },
}

DEEP_SCHEMA = {
    "type": "object",
    "properties": {**ENRICH_SCHEMA["properties"], **DEEP_EXTRA_PROPS},
    "required": ENRICH_SCHEMA["required"],
    "additionalProperties": False,
}
```

Add after `_input_text`:

```python
def _deep_input_text(company: dict) -> str:
    return _input_text(company) + (
        " ADDITIONALLY, dig for dated outreach events from the last 12 months: "
        "(1) named C-level/VP hires with exact roles, start dates, and a verbatim quote about "
        "their mandate from the announcement or an interview; "
        "(2) AI initiatives with the initiative name, type (product launch / partnership / pilot / "
        "exec statement), partner if any, announcement date, and an exec quote; "
        "(3) news coverage of capital raises (amount, instrument, date, a quote). "
        "Every item needs a date and a source URL. Omit items you cannot date."
    )
```

Add after `_signals_from_result`:

```python
def _parse_date(s) -> str | None:
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(s)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _angles_from_result(company: dict, result: dict) -> tuple[list[Angle], list[str]]:
    """Map deep-task arrays onto Angle rows. Invalid/undated items drop with a
    warning; one bad item never sinks the company."""
    content = result["content"]
    cik = int(company["cik"])
    out: list[Angle] = []
    warnings: list[str] = []

    def add(family: str, headline: str, details: dict, event_date: str | None,
            url: str | None, quote: str | None, fingerprint: str):
        if not event_date:
            warnings.append(f"{family}: dropped item with no date ({headline[:60]})")
            return
        try:
            out.append(Angle(
                company_cik=cik, family=family, headline=headline, details=details,
                evidence_url=url, evidence_quote=quote, event_date=event_date,
                source="parallel",
                strength=angles_mod.compute_strength(family, event_date, bool(quote), bool(url)),
                fingerprint=fingerprint,
            ))
        except ValidationError as exc:
            warnings.append(f"{family}: invalid item dropped ({str(exc)[:120]})")

    for item in content.get("leadership_hires") or []:
        d = _parse_date(item.get("start_date"))
        role = (item.get("role") or "").strip()
        if not role:
            warnings.append("leadership: dropped item with no role")
            continue
        name = item.get("person_name")
        add("leadership",
            f"New {role}" + (f": {name}" if name else "") + (f" (started {d})" if d else ""),
            {"role": role, "person_name": name, "start_date": d,
             "mandate_quote": item.get("mandate_quote")},
            d, item.get("source_url"), item.get("mandate_quote"),
            angles_mod.make_fingerprint("leadership", role, d))

    for item in content.get("ai_moves") or []:
        d = _parse_date(item.get("announced"))
        move_type = item.get("move_type") if item.get("move_type") in (
            "product_launch", "partnership", "pilot", "exec_statement") else "product_launch"
        add("ai_move",
            f"AI move: {item.get('initiative', '?')} ({move_type}, {d})",
            {"initiative": item.get("initiative"), "move_type": move_type,
             "partner": item.get("partner"), "exec_quote": item.get("exec_quote"), "announced": d},
            d, item.get("source_url"), item.get("exec_quote"),
            angles_mod.make_fingerprint("ai_move", item.get("initiative"), (d or "")[:7]))

    for item in content.get("funding_news") or []:
        d = _parse_date(item.get("announced"))
        instrument = item.get("instrument") if item.get("instrument") in (
            "follow_on", "atm", "pipe", "shelf", "debt", "other") else "other"
        add("funding",
            f"Funding news: {(item.get('description') or '?')[:80]} ({d})",
            {"amount_usd": item.get("amount_usd"), "instrument": instrument,
             "announced": d, "use_of_proceeds": None, "filing_type": None},
            d, item.get("source_url"), item.get("quote"),
            angles_mod.make_fingerprint("funding-news", (d or "")[:7], instrument))

    return out, warnings


def collect_deep_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[Angle], list[str]]]:
    """Deep tier: one richer task per company -> (P-signals, angles, warnings)."""
    if not companies:
        return {}
    cfg = SETTINGS.get("enrich", {}).get("deep", {})
    results = run_tasks_batch(
        [(_deep_input_text(c), DEEP_SCHEMA) for c in companies],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 900)),
    )
    out: dict[int, tuple[list[Signal], list[Angle], list[str]]] = {}
    for company, result in zip(companies, results):
        cik = int(company["cik"])
        if isinstance(result, Exception):
            out[cik] = ([], [], [f"deep task failed: {type(result).__name__}: {result}"])
            continue
        try:
            sigs = _signals_from_result(company, result)
            angles_found, warnings = _angles_from_result(company, result)
            out[cik] = (sigs, angles_found, warnings)
        except Exception as exc:
            out[cik] = ([], [], [f"deep result parse failed: {type(exc).__name__}: {exc}"])
    return out
```

Note the funding-news fingerprint prefix is `funding-news` (month+instrument), so Parallel color never collides with the EDGAR filing-accession fingerprints — EDGAR stays the source of record.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parallel_signals.py -v`
Expected: all PASS (old + 5 new). Then `uv run pytest -q` → `56 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/parallel_signals.py tests/test_parallel_signals.py
git commit -m "feat: deep Parallel task returns leadership/ai_move/funding angles"
```

---

### Task 6: CLI wiring — funding angles in edgar pass, `--source deep`

**Files:**
- Modify: `src/pipeline/cli.py` (the `enrich` command, `src/pipeline/cli.py:176` area)

**Interfaces:**
- Consumes: `funding_events.collect`, `parallel_signals.collect_deep_batch`, `angles.select_deep_targets`, `db.upsert_angles/latest_score/get_companies`.
- Produces: `enrich --source deep [--limit N] [--ticker X] [--dry-run]`; the edgar pass now also upserts funding angles. No behavior change for existing sources otherwise.

- [ ] **Step 1: Update the `--source` validation and add the deep branch**

In `enrich()`, change the validation line to:

```python
    if source not in ("edgar", "parallel", "all", "deep"):
        raise typer.BadParameter("--source must be edgar | parallel | all | deep")
```

Immediately after validation, delegate deep to its own function (deep has different target selection and flow):

```python
    if source == "deep":
        _enrich_deep(limit=limit, ticker=ticker, dry_run=dry_run)
        return
```

- [ ] **Step 2: Add `_enrich_deep` above the `enrich` command**

```python
def _enrich_deep(limit: int | None, ticker: str | None, dry_run: bool):
    """Deep tier: score-gated, cost-capped. Deep Parallel task (paid) + EDGAR
    funding scan (free) for companies at/near the qualify bar."""
    from pipeline import angles as angles_mod
    from pipeline import db, funding_events
    from pipeline.config import SETTINGS

    if ticker:
        row = db.get_company_by_ticker(ticker)
        if not row:
            raise typer.BadParameter(f"{ticker} not in pipeline — deep tier needs a scored company")
        targets = [row]
    else:
        pool: list[dict] = []
        for st in ("scored", "qualified", "contacts_found"):
            pool.extend(db.get_companies(status=st))
        totals = {int(c["cik"]): float((db.latest_score(c["cik"]) or {}).get("total") or 0) for c in pool}
        cap = int(SETTINGS.get("enrich", {}).get("deep", {}).get("max_tasks_per_run", 15))
        targets = angles_mod.select_deep_targets(pool, totals, min(limit or cap, cap))

    if not targets:
        console.print("No deep-tier candidates (statuses scored/qualified/contacts_found).")
        return
    if dry_run:
        for c in targets:
            console.print(f"[dim]{c['ticker']}: would run 1 deep Parallel task + EDGAR funding scan[/dim]")
        console.print(f"[dim]dry run — {len(targets)} companies selected, nothing spent[/dim]")
        return

    from pipeline import parallel_signals
    run_id = db.start_run("enrich:deep")
    console.print(f"[dim]{len(targets)} deep Parallel tasks created up front, polled together…[/dim]")
    deep_results = parallel_signals.collect_deep_batch(targets)

    stats = {"companies": 0, "signals": 0, "angles": 0, "errors": 0}
    for company in targets:
        cik = int(company["cik"])
        f_angles, f_errs = funding_events.collect(company)
        sigs, p_angles, warns = deep_results.get(cik, ([], [], ["no deep result"]))
        task_failed = any(w.startswith("deep task failed") or w.startswith("deep result parse failed") for w in warns)
        if not task_failed:
            # only replace on success — a failed task must not wipe prior parallel signals
            db.replace_signals(cik, "parallel", sigs)
        db.upsert_angles(f_angles + p_angles)
        _print_signals(company["ticker"], sigs, f_errs + warns)
        for a in f_angles + p_angles:
            console.print(f"[dim]  angle [{a.family.value}] {a.headline[:70]} (strength {a.strength})[/dim]")
        stats["companies"] += 1
        stats["signals"] += len(sigs)
        stats["angles"] += len(f_angles) + len(p_angles)
        stats["errors"] += len(f_errs) + (1 if task_failed else 0)

    db.finish_run(run_id, stats)
    console.print(f"Done: {stats}")
```

- [ ] **Step 3: Wire funding angles into the edgar pass**

In `enrich()`, the edgar collection block becomes:

```python
    angles_by_cik: dict[int, tuple[list, list]] = {}
    if source in ("edgar", "all"):
        from pipeline import edgar_signals, funding_events
        for company in targets:
            edgar_by_cik[int(company["cik"])] = edgar_signals.collect(company)
            angles_by_cik[int(company["cik"])] = funding_events.collect(company)
```

And inside the per-company write loop, after the `db.replace_signals` block:

```python
        f_angles, f_errs = angles_by_cik.get(cik, ([], []))
        if not dry_run and f_angles:
            db.upsert_angles(f_angles)
        for a in f_angles:
            console.print(f"[dim]  angle [funding] {a.headline[:70]}[/dim]")
        stats["errors"] += len(f_errs)
```

Also add `"angles": 0` to the `stats` dict initializer and `stats["angles"] += len(f_angles)` in the loop.

- [ ] **Step 4: Verify — suite + dry runs (no spend, no writes)**

Run: `uv run pytest -q`
Expected: `56 passed`

Run: `cd "/Users/udaykang/AI_Public Traded" && uv run python -m pipeline enrich --source deep --dry-run`
Expected: up to 15 lines `TICKER: would run 1 deep Parallel task + EDGAR funding scan`, then `dry run — N companies selected, nothing spent` (N ≤ 15; highest-scored first).

Run: `uv run python -m pipeline enrich --source edgar --ticker CCLD --dry-run`
Expected: the usual signal table, possibly `angle [funding] …` lines, no DB writes.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cli.py
git commit -m "feat: enrich --source deep (score-gated, capped) + funding angles in edgar pass"
```

---

### Task 7: Scoring — angles in packets, rubric, deterministic gate, no-demotion

**Files:**
- Modify: `src/pipeline/scoring.py`
- Test: `tests/test_scoring.py` (extend)

**Interfaces:**
- Consumes: `db.all_angles` (Task 3), `angles.slim/is_fresh` (Task 3), verdict angle fields (Task 2), `SETTINGS["scoring"]["require_angle"]` (Task 1).
- Produces:
  - packets gain `"angles": [slim…]` (active only)
  - `commit()` summary gains a `"kept"` bucket; review items may carry `"gate_reason": "no_active_angle"`
  - scores rows gain `angle_ranking`, `primary_angle`, `gate_reason`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scoring.py`)

```python
from datetime import date, timedelta

ANGLE_ROW = {
    "fingerprint": "funding:0001234-26-000042", "family": "funding",
    "headline": "Offering priced ~$12M — 424B5", "details": {"instrument": "follow_on"},
    "event_date": (date.today() - timedelta(days=20)).isoformat(),
    "strength": 1.0, "evidence_url": "https://sec.gov/x", "evidence_quote": "proceeds",
    "company_cik": 1, "source": "edgar", "status": "active",
}
STALE_ANGLE_ROW = {**ANGLE_ROW, "fingerprint": "funding:old",
                   "event_date": (date.today() - timedelta(days=400)).isoformat()}


def make_verdict(**overrides):
    v = {
        "ticker": "TST", "intent": 25, "capability_gap": 20, "timing": 15,
        "commercial_fit": 15, "profile": "adopter",
        "service_fit": [{"service": "ai_outreach", "priority": 1, "rationale": "r"}],
        "reasoning": "cited", "why_now": "fresh event", "evidence_cited": [],
        "confidence": "high", "angle_ranking": [], "primary_angle": None,
    }
    v.update(overrides)
    return v


def _run_commit(dirs, angles_rows, verdict, company_status="enriched"):
    q, r, a = dirs
    scoring.db.angles_rows = angles_rows
    scoring.db.company_status = company_status
    scoring.prepare()
    (r / "TST.json").write_text(json.dumps(verdict))
    return scoring.commit(run_id="t")


def test_packet_includes_only_active_angles(dirs):
    q, r, a = dirs
    scoring.db.angles_rows = [dict(ANGLE_ROW), dict(STALE_ANGLE_ROW)]
    scoring.prepare()
    packet = json.loads((q / "TST.json").read_text())
    assert [x["fingerprint"] for x in packet["angles"]] == ["funding:0001234-26-000042"]
    assert packet["angles"][0]["age_days"] == 20


def test_qualifies_with_active_angle(dirs):
    summary = _run_commit(dirs, [dict(ANGLE_ROW)], make_verdict())
    assert [x["ticker"] for x in summary["qualified"]] == ["TST"]


def test_blocked_without_angle_goes_review_with_reason(dirs):
    summary = _run_commit(dirs, [], make_verdict())
    assert summary["qualified"] == []
    assert summary["review"][0]["gate_reason"] == "no_active_angle"
    assert scoring.db.scores[0]["gate_reason"] == "no_active_angle"


def test_stale_only_angles_also_blocked(dirs):
    summary = _run_commit(dirs, [dict(STALE_ANGLE_ROW)], make_verdict())
    assert summary["qualified"] == []
    assert summary["review"][0]["gate_reason"] == "no_active_angle"


def test_require_angle_false_restores_v1_gate(dirs, monkeypatch):
    monkeypatch.setitem(scoring.SETTINGS.setdefault("scoring", {}), "require_angle", False)
    summary = _run_commit(dirs, [], make_verdict())
    assert [x["ticker"] for x in summary["qualified"]] == ["TST"]


def test_no_demotion_for_contacts_found(dirs):
    low = make_verdict(intent=5, capability_gap=5, timing=5, commercial_fit=5)
    summary = _run_commit(dirs, [dict(ANGLE_ROW)], low, company_status="contacts_found")
    assert summary["kept"][0]["ticker"] == "TST"
    # FakeDB stores str(Status.x) which renders as "Status.contacts_found"
    assert scoring.db.statuses[-1][1].endswith("contacts_found")


def test_hallucinated_primary_angle_stripped(dirs):
    v = make_verdict(primary_angle={"fingerprint": "made:up", "family": "funding",
                                    "why_this_angle": "x"},
                     angle_ranking=[{"fingerprint": "made:up", "family": "funding",
                                     "message_hook": "h"}])
    _run_commit(dirs, [dict(ANGLE_ROW)], v)
    assert scoring.db.scores[0]["primary_angle"] is None
    assert scoring.db.scores[0]["angle_ranking"] == []
```

Also extend `FakeDB` in the same file so the tests above work — add to `__init__`: `self.angles_rows = []` and `self.company_status = "enriched"`; add methods:

```python
    def all_angles(self):
        return {1: [dict(r) for r in self.angles_rows]} if self.angles_rows else {}

    def get_angles(self, cik):
        return [dict(r) for r in self.angles_rows]
```

and change `get_company_by_ticker` to return `{**dict(COMPANY), "status": self.company_status}` for "TST".

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scoring.py -v`
Expected: new tests FAIL — `KeyError: 'angles'` / `KeyError: 'kept'` / missing `gate_reason`.

- [ ] **Step 3: Implement in `src/pipeline/scoring.py`**

Import the angle module at top:

```python
from pipeline import angles as angles_mod
from pipeline import db
```

In `prepare()`, after `signals_by_cik = db.all_signals()`, add:

```python
    angles_by_cik = db.all_angles()
```

In the per-company loop, right before `packet = {`, add:

```python
        active_angles = [
            angles_mod.slim(a)
            for a in angles_by_cik.get(int(company["cik"]), [])
            if angles_mod.is_fresh(a["family"], a["event_date"])
        ]
        active_angles.sort(key=lambda a: -(a.get("strength") or 0))
```

and add to the packet dict, after `"signals": slim_signals,`:

```python
            "angles": active_angles,
```

Append to `RUBRIC` (add to the end of the existing string):

```python
RUBRIC += """
angle_ranking / primary_angle: the packet's `angles` list holds dated, structured
outreach events (families: funding, leadership, ai_move) with a pre-computed
strength. Rank ALL of them by outreach power — strength, specificity, and fit
with your service_fit — strongest first, each with a one-sentence message_hook
(the opening line a seller could use). Set primary_angle to the single best one
with why_this_angle. Copy fingerprint and family EXACTLY from the packet. If
`angles` is empty, return angle_ranking: [] and primary_angle: null — do not
invent angles from signals.
"""
```

In `commit()`, read the flag with the other config at the top:

```python
    require_angle = bool(cfg.get("require_angle", True))
```

and initialize the summary with the new bucket:

```python
    summary = {"qualified": [], "review": [], "disqualified": [], "invalid": [], "orphan": [], "kept": []}
```

After `has_hard = ...`, add the hallucination guard and gate inputs:

```python
        packet_fps = {a["fingerprint"] for a in packet.get("angles", [])}
        if verdict.primary_angle and verdict.primary_angle.fingerprint not in packet_fps:
            verdict.primary_angle = None
        verdict.angle_ranking = [r for r in verdict.angle_ranking if r.fingerprint in packet_fps]
        has_angle = bool(packet.get("angles"))
```

Extend the `db.insert_score({...})` dict with three entries (after `"confidence"`):

```python
            "angle_ranking": [r.model_dump(mode="json") for r in verdict.angle_ranking],
            "primary_angle": verdict.primary_angle.model_dump(mode="json") if verdict.primary_angle else None,
            "gate_reason": "",
```

Replace the status-decision block (`if total >= threshold and has_hard:` … `summary[bucket].append(...)`) with:

```python
        gate_reason = ""
        if total >= threshold and has_hard and (has_angle or not require_angle):
            new_status, bucket = Status.qualified, "qualified"
        elif total < floor:
            new_status, bucket = Status.disqualified, "disqualified"
        else:
            new_status, bucket = Status.scored, "review"
            if total >= threshold and has_hard and not has_angle:
                gate_reason = "no_active_angle"

        # tightened gate never demotes accounts already past qualification
        if company.get("status") in ("qualified", "contacts_found"):
            new_status, bucket = Status(company["status"]), "kept"

        db.set_status(company["cik"], new_status, profile=verdict.profile.value)
        item = {"ticker": ticker, "total": total, "profile": verdict.profile.value}
        if gate_reason:
            item["gate_reason"] = gate_reason
        summary[bucket].append(item)
```

Because `insert_score` runs before the gate decision, move the `db.insert_score({...})` call to AFTER the gate block and use the computed `gate_reason` in the row (`"gate_reason": gate_reason,`). Keep everything else in the row identical.

Finally, update the module docstring's flow comment to mention angles (one line: `Packets carry active outreach angles; commit enforces the angle-required gate.`).

- [ ] **Step 4: Update the `score --commit` output in `src/pipeline/cli.py`**

The bucket print loop becomes:

```python
        for bucket in ("qualified", "review", "disqualified", "kept"):
            items = summary[bucket]
            console.print(f"[bold]{bucket}[/bold] ({len(items)}): " + ", ".join(
                f"{i['ticker']}={i['total']}({i['profile']})"
                + (f" [{i['gate_reason']}]" if i.get("gate_reason") else "")
                for i in items))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py -v`
Expected: all PASS (old + 7 new). Then `uv run pytest -q` → `63 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/scoring.py src/pipeline/cli.py tests/test_scoring.py
git commit -m "feat: angle-aware packets + deterministic angle-required qualify gate"
```

---

### Task 8: `score --statuses` (rescore) + export angle columns

**Files:**
- Modify: `src/pipeline/cli.py` (the `score` and `export` commands)

**Interfaces:**
- Consumes: `scoring.prepare(limit, statuses)` (existing param), `db.get_angles`, `angles.is_fresh`, scores rows' `primary_angle`/`angle_ranking` (Task 7).
- Produces: `score --prepare --statuses enriched,scored` (rescore the review band); `export` CSV gains `angle_ready`, `angle_family`, `primary_angle`, `message_hook` columns.

- [ ] **Step 1: Add `--statuses` to the `score` command**

Add the option:

```python
    statuses: str = typer.Option(
        "enriched", "--statuses",
        help="Comma-separated company statuses to (re)score with --prepare, e.g. 'enriched,scored'",
    ),
```

Parse once at the top of the function body:

```python
    status_tuple = tuple(s.strip() for s in statuses.split(",") if s.strip())
```

and pass `statuses=status_tuple` to BOTH `scoring.prepare(...)` calls (the openrouter path and the `--prepare` path).

- [ ] **Step 2: Add angle columns to `export`**

At the top of `export()`, add imports:

```python
    from pipeline import angles as angles_mod
    from pipeline import db
```

Inside the company loop, after `fits = s.get("service_fit") or []`, add:

```python
            active = [a for a in db.get_angles(company["cik"])
                      if angles_mod.is_fresh(a["family"], a["event_date"])]
            pa = s.get("primary_angle") or {}
            pa_headline = next((a["headline"] for a in active
                                if a["fingerprint"] == pa.get("fingerprint")), "")
            hook = next((r.get("message_hook", "") for r in (s.get("angle_ranking") or [])
                         if r.get("fingerprint") == pa.get("fingerprint")), "")
```

and extend the `base` dict (after `"why_now"`):

```python
                "angle_ready": bool(active),
                "angle_family": pa.get("family") or "",
                "primary_angle": pa_headline,
                "message_hook": hook,
```

- [ ] **Step 3: Verify**

Run: `uv run pytest -q`
Expected: `63 passed`

Run: `uv run python -m pipeline score` (no flags)
Expected: the usage hint line, unchanged (`Use --prepare or --commit …`) — proves the new option doesn't break the command.

- [ ] **Step 4: Commit**

```bash
git add src/pipeline/cli.py
git commit -m "feat: score --statuses for rescoring; export angle columns"
```

---

### Task 9: Docs + skills

**Files:**
- Modify: `docs/SIGNALS.md`, `docs/ARCHITECTURE.md`, `docs/PIPELINE.md`, `README.md`
- Modify: `.claude/skills/enrich/SKILL.md`, `.claude/skills/score/SKILL.md`

**Interfaces:** none (documentation of Tasks 1–8).

- [ ] **Step 1: `docs/SIGNALS.md`** — append a section:

```markdown
## Outreach angles (v2)

Angles are dated, structured outreach events stored in the `angles` table —
separate from signals (signals feed scoring; angles feed outreach copy). One
row per event, deduped by fingerprint, never bulk-wiped. Freshness windows and
strength decay: `config/settings.yaml` → `angles`.

| Family | Sources | Typed fields | Copy angle |
|--------|---------|--------------|------------|
| funding | 8-K 3.02/1.01, S-3, 424B (EDGAR); news color (Parallel) | amount_usd, instrument, announced, use_of_proceeds, filing_type | "You just raised — deploy it on growth efficiently" |
| leadership | deep Parallel | role, person_name, start_date, first_in_role, mandate_quote | "New exec's first-100-days agenda" |
| ai_move | deep Parallel | initiative, move_type, partner, exec_quote, announced | "You're investing in AI — accelerate with specialists" |

**Qualify gate (v2)**: total ≥ 65 AND ≥1 hard signal AND ≥1 active (fresh)
angle. Blocked companies stay in the review band with `gate_reason:
no_active_angle` on the score row. Toggle: `scoring.require_angle`.
```

- [ ] **Step 2: `docs/ARCHITECTURE.md`** — three edits:
  1. Module map table: add rows `| angles.py | Angle freshness/strength/fingerprints, deep-tier selection |` and `| funding_events.py | EDGAR funding-event collector → funding angles |`.
  2. State-owners table: add `| Outreach angles (dated structured events) | Supabase (angles) | Durable — deduped by fingerprint, never bulk-wiped |`.
  3. In "Stage-by-stage data flow" under **enrich**, append: `A deep tier (enrich --source deep) runs for companies at/near the qualify bar: one richer Parallel task (capped by enrich.deep.max_tasks_per_run) plus a free EDGAR funding scan, producing angle rows. The qualify gate (scoring) then requires ≥1 active angle.`

- [ ] **Step 3: `docs/PIPELINE.md`** — in "Normal cycle", after step 3 (score), insert:

```markdown
# 3b. Deep tier — richer evidence + outreach angles for review-band/qualified
uv run python -m pipeline enrich --source deep --dry-run   # preview selection
uv run python -m pipeline enrich --source deep --limit 15  # capped, paid
uv run python -m pipeline score --prepare --statuses scored  # rescore with angles
#   -> /score skill -> score --commit
```

and in "Costs", append: `Deep tier: 1 Parallel task per company, capped at enrich.deep.max_tasks_per_run (15).`

- [ ] **Step 4: `README.md`** — in the Commands table, change the enrich row to `enrich --source edgar|parallel|deep` with description `Collect signals (EDGAR free; parallel/deep are paid + capped)`. In "Signals and scoring", append one sentence: `v2 tightens the gate: qualified also requires at least one fresh, structured outreach angle (funding event, leadership hire, or AI move) — see docs/SIGNALS.md.`

- [ ] **Step 5: `.claude/skills/enrich/SKILL.md`** — add a "Deep tier" subsection: when to run (`after scoring, for review band / before outreach`), the two commands from Step 3, the cap, and the rule that `--dry-run` must precede any paid batch.

- [ ] **Step 6: `.claude/skills/score/SKILL.md`** — note that packets now carry an `angles` list; the subagent must return `angle_ranking` + `primary_angle` per the packet's output_schema (fingerprints copied exactly, null/[] when no angles); `--commit` output now includes a `kept` bucket and `[no_active_angle]` markers; rescoring the review band uses `score --prepare --statuses scored`.

- [ ] **Step 7: Verify and commit**

Run: `uv run pytest -q` → `63 passed` (docs shouldn't break anything).

```bash
git add docs/SIGNALS.md docs/ARCHITECTURE.md docs/PIPELINE.md README.md .claude/skills/enrich/SKILL.md .claude/skills/score/SKILL.md
git commit -m "docs: outreach angles — taxonomy, architecture, runbook, skills"
```

---

### Task 10: Live verification (spend-gated)

**Files:** none (runtime verification per CLAUDE.md collector rule).

- [ ] **Step 1: Free EDGAR verification on a known company**

Run: `cd "/Users/udaykang/AI_Public Traded" && uv run python -m pipeline enrich --source edgar --ticker CCLD --dry-run`
Expected: signal table renders; if CareCloud filed any S-3/424B/8-K financings in the last 12 months, `angle [funding] …` lines appear. No exceptions, no DB writes.

- [ ] **Step 2: Deep-tier selection dry run**

Run: `uv run python -m pipeline enrich --source deep --dry-run`
Expected: ≤15 companies listed, highest latest-score first, `nothing spent` footer.

- [ ] **Step 3: STOP — ask the user before real spend**

One real deep task (single company) verifies the Parallel schema end-to-end:
`uv run python -m pipeline enrich --source deep --ticker CCLD`
This costs one Parallel task. **Do not run it without the user's explicit go-ahead.** After the user approves and it runs: expect a signal table, `angle […]` lines, and a `Done: {…}` stats line with `angles ≥ 0`; then `uv run python -m pipeline status` should still show the same funnel counts (deep never changes status).

- [ ] **Step 4: Report**

Summarize to the user: funding angles found in Step 1, deep-task angles in Step 3, and the recommended next commands (3 capped deep runs over the 43 eligible, then `score --prepare --statuses scored` → /score skill → `score --commit`).
