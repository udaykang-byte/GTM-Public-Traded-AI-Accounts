# Pipeline Speed + Context-Hygiene Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a 25-company enrichment batch finish in ~5 minutes (Parallel fan-out + EDGAR 8-K item-metadata filter), cut ~40% of Haiku-scorer input tokens (shared `_shared.json`), and bring the skill files in line with the CLI.

**Architecture:** Parallel.ai calls move from one-at-a-time blocking polls to create-all-then-poll batches (`run_tasks_batch` in `parallel_client.py`, batch wrappers in `parallel_signals.py`/`people.py`, wired into `cli.py`). EDGAR's 8-K loop filters by filing-index item metadata before downloading any document text. `score --prepare` splits shared boilerplate out of per-company packets into `data/scoring_queue/_shared.json`.

**Tech Stack:** Python 3.12, uv, httpx, edgartools, pydantic v2, typer, pytest (added by this plan), Supabase via `pipeline.db`.

Spec: `docs/superpowers/specs/2026-07-06-pipeline-speed-context-review-design.md`

## Global Constraints

- The project directory contains a space (`AI_Public Traded`) — always quote paths in shell commands.
- All commands run via uv from the repo root: `uv run python -m pipeline …`, `uv run pytest …`.
- Secrets live only in `.env`; never print or commit key values.
- SEC courtesy: `EDGAR_IDENTITY` stays set; keep throttling and `data/cache/` caching intact.
- Parallel spend: respect `enrich.parallel.max_tasks_per_run` (25) and `people.max_companies_per_run` (10); never loop Parallel calls around caps. The only intentional live spend in this plan is Task 8's 3-task fan-out check (approved in the spec).
- All DB writes go through `pipeline.db` — no ad-hoc SQL.
- v1 scoring stays on Claude Code **Haiku subagents** — no paid LLM APIs.
- Do not change scoring thresholds or weights in `config/settings.yaml`.
- No retries framework, no threads, no asyncio — plain sequential HTTP with round-robin polling.

---

### Task 1: `run_tasks_batch` in `parallel_client.py` (+ pytest setup)

**Files:**
- Modify: `pyproject.toml` (dev dependency)
- Modify: `src/pipeline/parallel_client.py:103-132` (`wait_for_result`, `run_task`; add `_run_status`, `_fetch_result`, `run_tasks_batch`)
- Test: `tests/test_parallel_client.py` (new; also creates `tests/`)

**Interfaces:**
- Consumes: existing `create_task_run(input_text, output_schema, processor) -> str`, `_headers()`, `API_BASE`.
- Produces: `run_tasks_batch(tasks: list[tuple[str, dict]], processor: str = "base", timeout_s: int = 600, poll_s: float = 5.0) -> list[dict | Exception]` — one entry per input task, in input order; a parsed result dict (`{"content": …, "basis": …}`) or the Exception that task hit. Tasks 2 and 3 call this. `wait_for_result` behavior unchanged.

- [ ] **Step 1: Add pytest as a dev dependency**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv add --dev pytest
```

Expected: `pyproject.toml` gains a `[dependency-groups]` `dev = ["pytest>=…"]` entry; `uv run pytest --version` prints a version.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_parallel_client.py`:

```python
"""run_tasks_batch orchestration tests — HTTP helpers are monkeypatched."""
import pytest

from pipeline import parallel_client as pc


def _no_sleep(monkeypatch):
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)


def test_batch_returns_results_in_input_order(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(pc, "create_task_run", lambda text, schema, processor="base": f"run-{text}")
    monkeypatch.setattr(pc, "_run_status", lambda run_id: "completed")
    monkeypatch.setattr(pc, "_fetch_result", lambda run_id: {"content": {"id": run_id}, "basis": []})

    results = pc.run_tasks_batch([("a", {}), ("b", {})], timeout_s=5)

    assert [r["content"]["id"] for r in results] == ["run-a", "run-b"]


def test_create_failure_isolated_to_its_slot(monkeypatch):
    _no_sleep(monkeypatch)

    def create(text, schema, processor="base"):
        if text == "bad":
            raise RuntimeError("boom")
        return f"run-{text}"

    monkeypatch.setattr(pc, "create_task_run", create)
    monkeypatch.setattr(pc, "_run_status", lambda run_id: "completed")
    monkeypatch.setattr(pc, "_fetch_result", lambda run_id: {"content": {}, "basis": []})

    results = pc.run_tasks_batch([("bad", {}), ("ok", {})], timeout_s=5)

    assert isinstance(results[0], RuntimeError)
    assert results[1] == {"content": {}, "basis": []}


def test_failed_run_becomes_runtime_error(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(pc, "create_task_run", lambda text, schema, processor="base": "run-x")
    monkeypatch.setattr(pc, "_run_status", lambda run_id: "failed")

    results = pc.run_tasks_batch([("a", {})], timeout_s=5)

    assert isinstance(results[0], RuntimeError)
    assert "failed" in str(results[0])


def test_timeout_becomes_timeout_error(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(pc, "create_task_run", lambda text, schema, processor="base": "run-x")
    monkeypatch.setattr(pc, "_run_status", lambda run_id: "running")

    results = pc.run_tasks_batch([("a", {})], timeout_s=0.05, poll_s=0.01)

    assert isinstance(results[0], TimeoutError)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_parallel_client.py -v
```

Expected: FAIL — `AttributeError: module 'pipeline.parallel_client' has no attribute '_run_status'` (and no `run_tasks_batch`).

- [ ] **Step 4: Implement `_run_status`, `_fetch_result`, `run_tasks_batch`; slim `wait_for_result`**

In `src/pipeline/parallel_client.py`, replace the existing `wait_for_result` (lines 103–127) with:

```python
def _run_status(run_id: str) -> str:
    resp = httpx.get(f"{API_BASE}/v1/tasks/runs/{run_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("status")


def _fetch_result(run_id: str) -> dict:
    resp = httpx.get(f"{API_BASE}/v1/tasks/runs/{run_id}/result", headers=_headers(), timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    content = (payload.get("output") or {}).get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"text": content}
    return {"content": content or {}, "basis": (payload.get("output") or {}).get("basis", [])}


def wait_for_result(run_id: str, timeout_s: int = 600, poll_s: float = 5.0) -> dict:
    """Poll until the run finishes; return the parsed structured output."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = _run_status(run_id)
        if status in ("completed", "failed", "cancelled"):
            break
        time.sleep(poll_s)
    else:
        raise TimeoutError(f"Parallel task {run_id} did not finish in {timeout_s}s")
    if status != "completed":
        raise RuntimeError(f"Parallel task {run_id} ended with status={status}")
    return _fetch_result(run_id)
```

Then add after `run_task` (end of file):

```python
def run_tasks_batch(
    tasks: list[tuple[str, dict]],
    processor: str = "base",
    timeout_s: int = 600,
    poll_s: float = 5.0,
) -> list[dict | Exception]:
    """Create every task run up front, then poll them together.

    Returns one entry per input task, in input order: the parsed result dict,
    or the Exception that task hit (creation failure, run failure, timeout).
    Wall-clock ~= the slowest single task, not the sum of all tasks.
    """
    results: list = [None] * len(tasks)
    pending: dict[int, str] = {}
    for i, (input_text, schema) in enumerate(tasks):
        try:
            pending[i] = create_task_run(input_text, schema, processor)
        except Exception as exc:
            results[i] = exc

    deadline = time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        for i, run_id in list(pending.items()):
            try:
                status = _run_status(run_id)
            except Exception as exc:
                results[i] = exc
                del pending[i]
                continue
            if status == "completed":
                try:
                    results[i] = _fetch_result(run_id)
                except Exception as exc:
                    results[i] = exc
                del pending[i]
            elif status in ("failed", "cancelled"):
                results[i] = RuntimeError(f"Parallel task {run_id} ended with status={status}")
                del pending[i]
        if pending:
            time.sleep(poll_s)
    for i, run_id in pending.items():
        results[i] = TimeoutError(f"Parallel task {run_id} did not finish in {timeout_s}s")
    return results
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_parallel_client.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add pyproject.toml uv.lock src/pipeline/parallel_client.py tests/test_parallel_client.py && git commit -m "feat: create-all-then-poll batch runner for Parallel tasks (+ pytest setup)"
```

---

### Task 2: `collect_batch` in `parallel_signals.py`

**Files:**
- Modify: `src/pipeline/parallel_signals.py:90-132` (`collect`; extract parser, add batch)
- Test: `tests/test_parallel_signals.py` (new)

**Interfaces:**
- Consumes: `run_tasks_batch` from Task 1 (exact signature above).
- Produces: `collect_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[str]]]` keyed by `int(company["cik"])` — Task 4's CLI uses this. `collect(company)` keeps its existing signature. Internal `_signals_from_result(company: dict, result: dict) -> list[Signal]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parallel_signals.py`:

```python
from pipeline import parallel_signals as ps

COMPANY = {"cik": 123, "ticker": "TST", "name": "Test Co", "sector_bucket": "saas", "market_cap": 100e6}


def _result(found_areas: dict) -> dict:
    content = {}
    for area in ps.AREA_TO_SIGNAL:
        content[area] = found_areas.get(area, {"found": False, "summary": "nothing"})
    return {"content": content, "basis": []}


def test_signals_from_result_maps_found_areas():
    result = _result({
        "ai_job_postings": {"found": True, "summary": "2 ML roles open", "roles": ["ML Engineer"],
                            "evidence_urls": ["https://x.test/jobs"]},
        "exec_ai_commentary": {"found": True, "summary": "CEO on AI", "quotes": ["We bet on AI - CEO"]},
    })
    signals = ps._signals_from_result(COMPANY, result)
    by_type = {s.type: s for s in signals}
    assert set(by_type) == {"P1", "P6"}
    assert by_type["P1"].evidence_url == "https://x.test/jobs"
    assert "ML Engineer" in by_type["P1"].detail
    assert by_type["P6"].evidence_quote == "We bet on AI - CEO"


def test_collect_batch_isolates_failures(monkeypatch):
    ok = _result({"ai_announcements": {"found": True, "summary": "launched AI pilot"}})
    monkeypatch.setattr(ps, "run_tasks_batch",
                        lambda tasks, processor="base", timeout_s=600: [ok, TimeoutError("slow")])
    other = dict(COMPANY, cik=456, ticker="OTH")

    out = ps.collect_batch([COMPANY, other])

    sigs, errs = out[123]
    assert [s.type for s in sigs] == ["P3"] and errs == []
    sigs, errs = out[456]
    assert sigs == [] and "TimeoutError" in errs[0]


def test_collect_batch_empty_input():
    assert ps.collect_batch([]) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_parallel_signals.py -v
```

Expected: FAIL — no attribute `_signals_from_result` / `collect_batch` / `run_tasks_batch` on the module.

- [ ] **Step 3: Refactor `collect` and add `collect_batch`**

In `src/pipeline/parallel_signals.py`, change the import line

```python
from pipeline.parallel_client import run_task
```

to

```python
from pipeline.parallel_client import run_task, run_tasks_batch
```

and replace the whole `collect` function (lines 90–132) with:

```python
def _signals_from_result(company: dict, result: dict) -> list[Signal]:
    """Map one Parallel structured result onto P1-P6 Signal rows."""
    content = result["content"]
    basis_urls = []
    for b in result.get("basis", []):
        for c in (b.get("citations") or []):
            if c.get("url"):
                basis_urls.append(c["url"])

    signals: list[Signal] = []
    today = date.today()
    for area, (sig_type, title) in AREA_TO_SIGNAL.items():
        data = content.get(area) or {}
        if not data.get("found"):
            continue
        urls = data.get("evidence_urls") or basis_urls[:2]
        quote = None
        if area == "exec_ai_commentary" and data.get("quotes"):
            quote = data["quotes"][0][:350]
        detail = (data.get("summary") or "").strip()
        if area == "ai_job_postings" and data.get("roles"):
            detail += f" Roles: {', '.join(data['roles'][:5])}"
        if area == "martech_stack" and data.get("maturity"):
            detail += f" (maturity: {data['maturity']})"
        signals.append(Signal(
            company_cik=company["cik"], source="parallel", type=sig_type,
            title=title, detail=detail[:1000],
            evidence_url=urls[0] if urls else None,
            evidence_quote=quote, observed_at=today,
            weight=_w(sig_type), raw=data,
        ))
    return signals


def collect(company: dict) -> tuple[list[Signal], list[str]]:
    """One Parallel task run -> P1-P6 signals for one company."""
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    try:
        result = run_task(
            _input_text(company),
            ENRICH_SCHEMA,
            processor=cfg.get("processor", "base"),
            timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
        )
    except Exception as exc:
        return [], [f"parallel task failed: {type(exc).__name__}: {exc}"]
    return _signals_from_result(company, result), []


def collect_batch(companies: list[dict]) -> dict[int, tuple[list[Signal], list[str]]]:
    """One Parallel task per company — created up front, polled together."""
    if not companies:
        return {}
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    results = run_tasks_batch(
        [(_input_text(c), ENRICH_SCHEMA) for c in companies],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    out: dict[int, tuple[list[Signal], list[str]]] = {}
    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            out[int(company["cik"])] = ([], [f"parallel task failed: {type(result).__name__}: {result}"])
        else:
            out[int(company["cik"])] = (_signals_from_result(company, result), [])
    return out
```

(The body of `_signals_from_result` is the current parsing code moved verbatim; only the wrapping changed.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_parallel_signals.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add src/pipeline/parallel_signals.py tests/test_parallel_signals.py && git commit -m "feat: batched Parallel enrichment via collect_batch"
```

---

### Task 3: `find_people_batch` in `people.py`

**Files:**
- Modify: `src/pipeline/people.py:68-107` (`find_people`; extract input/parser, add batch)
- Test: `tests/test_people.py` (new)

**Interfaces:**
- Consumes: `run_tasks_batch` from Task 1; existing `target_roles(service_fit) -> list[str]`.
- Produces: `find_people_batch(items: list[tuple[dict, list[dict]]]) -> list[tuple[list[Contact], str] | Exception]` — one entry per `(company, service_fit)` input, in order; Task 4's CLI uses this. `find_people(company, service_fit)` keeps its existing signature. Internal helpers `_people_input_text(company, roles) -> str`, `_contacts_from_result(company, roles, content) -> tuple[list[Contact], str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_people.py`:

```python
from pipeline import people

COMPANY = {"cik": 123, "ticker": "TST", "name": "Test Co", "website": None}
FIT = [{"service": "ai_lead_generation", "priority": 1, "rationale": "x"}]

CONTENT = {
    "people": [
        {"name": "Jane Roe", "title": "Chief Marketing Officer", "confidence": "high",
         "linkedin_url": "https://linkedin.com/in/jane", "source_urls": ["https://t.co/ir"]},
        {"name": "", "title": "CFO", "confidence": "low"},
    ],
    "notes": "no CRO found",
}


def test_contacts_from_result_maps_and_skips_nameless():
    roles = people.target_roles(FIT)
    contacts, notes = people._contacts_from_result(COMPANY, roles, CONTENT)
    assert len(contacts) == 1
    assert contacts[0].name == "Jane Roe"
    # existing behavior: role_bucket is a substring match of a role token in the
    # title; "CMO" is not a substring of "Chief Marketing Officer", so it's ""
    assert contacts[0].role_bucket == ""
    assert notes == "no CRO found"


def test_find_people_batch_isolates_failures(monkeypatch):
    monkeypatch.setattr(people, "run_tasks_batch",
                        lambda tasks, processor="base", timeout_s=600: [
                            {"content": CONTENT, "basis": []},
                            RuntimeError("task failed"),
                        ])
    other = dict(COMPANY, cik=456, ticker="OTH")

    results = people.find_people_batch([(COMPANY, FIT), (other, FIT)])

    contacts, notes = results[0]
    assert contacts[0].name == "Jane Roe"
    assert isinstance(results[1], RuntimeError)


def test_find_people_batch_empty_input():
    assert people.find_people_batch([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_people.py -v
```

Expected: FAIL — no attribute `_contacts_from_result` / `find_people_batch` / `run_tasks_batch`.

- [ ] **Step 3: Refactor `find_people` and add `find_people_batch`**

In `src/pipeline/people.py`, change the import

```python
from pipeline.parallel_client import run_task
```

to

```python
from pipeline.parallel_client import run_task, run_tasks_batch
```

and replace the whole `find_people` function (lines 68–107) with:

```python
def _people_input_text(company: dict, roles: list[str]) -> str:
    website = f" (website: {company['website']})" if company.get("website") else ""
    return (
        f"Find the current executives of {company['name']} (US-listed, ticker "
        f"{company['ticker']}){website}. Target roles, in priority order: "
        f"{', '.join(roles)}. For each person found: full name, exact current title, "
        "LinkedIn profile URL, and a company email address ONLY if it is publicly "
        "published somewhere you can cite (investor relations page, press release, "
        "company website) — never guess or construct emails. Note anyone who recently "
        "left or was recently appointed. Small companies may not have all these roles; "
        "report only people you can verify."
    )


def _contacts_from_result(company: dict, roles: list[str], content: dict) -> tuple[list[Contact], str]:
    contacts: list[Contact] = []
    for p in content.get("people", []):
        name = (p.get("name") or "").strip()
        title = (p.get("title") or "").strip()
        if not name or not title:
            continue
        role_bucket = next(
            (r for r in roles if r.lower() in title.lower()),
            roles[0] if any(k in title.lower() for k in ("chief executive", "ceo")) else "",
        )
        contacts.append(Contact(
            company_cik=company["cik"],
            name=name, title=title, role_bucket=role_bucket,
            linkedin_url=(p.get("linkedin_url") or None),
            email=(p.get("email") or None),
            email_source=(p.get("email_source_url") or None),
            confidence=p.get("confidence", "medium"),
            evidence={"source_urls": p.get("source_urls", [])},
        ))
    return contacts, content.get("notes", "")


def find_people(company: dict, service_fit: list[dict]) -> tuple[list[Contact], str]:
    roles = target_roles(service_fit)
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    result = run_task(
        _people_input_text(company, roles), PEOPLE_SCHEMA,
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    return _contacts_from_result(company, roles, result["content"])


def find_people_batch(items: list[tuple[dict, list[dict]]]) -> list[tuple[list[Contact], str] | Exception]:
    """items = [(company, service_fit), ...] — one Parallel task each, polled together."""
    if not items:
        return []
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    roles_per = [target_roles(fits) for _, fits in items]
    results = run_tasks_batch(
        [(_people_input_text(c, roles), PEOPLE_SCHEMA)
         for (c, _), roles in zip(items, roles_per)],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    out: list = []
    for (company, _), roles, result in zip(items, roles_per, results):
        if isinstance(result, Exception):
            out.append(result)
        else:
            out.append(_contacts_from_result(company, roles, result["content"]))
    return out
```

(`_people_input_text` and `_contacts_from_result` bodies are the current code moved verbatim — do not change the role_bucket matching logic; the test above pins its existing behavior.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_people.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add src/pipeline/people.py tests/test_people.py && git commit -m "feat: batched people search via find_people_batch"
```

---

### Task 4: Wire batches into `cli.py` (+ N+1 fix, no-spend `--dry-run`)

**Files:**
- Modify: `src/pipeline/cli.py:210-266` (enrich target selection + collection loop)
- Modify: `src/pipeline/cli.py:321-375` (people command)

**Interfaces:**
- Consumes: `parallel_signals.collect_batch(companies) -> dict[int, (signals, errors)]` (Task 2), `people.find_people_batch(items) -> list[(contacts, notes) | Exception]` and `people.target_roles(fits)` (Task 3), existing `db.all_signals() -> dict[int, list[dict]]`.
- Produces: CLI behavior only. Three deliberate behavior changes: (1) `--source parallel --dry-run` **no longer spends** — it lists which companies would get a task (matches `people --dry-run` semantics); (2) a company whose Parallel task failed keeps its previous parallel signals (no more wipe-on-failure); (3) the parallel-pool "already has parallel signals" filter uses one bulk `db.all_signals()` call instead of per-company queries.

No unit test (CLI is thin orchestration over tested modules); verification is by dry-runs in Step 3.

- [ ] **Step 1: Rewrite the enrich pool filter and collection loop**

In `src/pipeline/cli.py`, inside `enrich`, replace the `if source == "parallel":` pool block (lines 210–219) with:

```python
        if source == "parallel":
            # parallel runs after edgar; never re-spend on companies that
            # already have parallel signals unless --force
            pool = db.get_companies(status="new") + db.get_companies(status="enriched")
            if not force:
                sigs_by_cik = db.all_signals()
                pool = [
                    c for c in pool
                    if not any(s["source"] == "parallel" for s in sigs_by_cik.get(int(c["cik"]), []))
                ]
            targets = pool
```

Then replace everything from `parallel_cap = int(...)` through the end of the per-company `for` loop (lines 230–262) with:

```python
    parallel_cap = int(SETTINGS.get("enrich", {}).get("parallel", {}).get("max_tasks_per_run", 25))
    run_id = None if dry_run else db.start_run(f"enrich:{source}")
    stats = {"companies": 0, "signals": 0, "errors": 0}

    edgar_by_cik: dict[int, tuple[list, list]] = {}
    parallel_by_cik: dict[int, tuple[list, list]] = {}

    if source in ("edgar", "all"):
        from pipeline import edgar_signals
        for company in targets:
            edgar_by_cik[int(company["cik"])] = edgar_signals.collect(company)

    if source in ("parallel", "all"):
        batch = targets[:parallel_cap]
        if len(targets) > parallel_cap:
            console.print(
                f"[yellow]Parallel cap ({parallel_cap}/run) — skipping "
                f"{len(targets) - parallel_cap} companies[/yellow]"
            )
        if dry_run:
            for company in batch:
                console.print(f"[dim]{company['ticker']}: would run 1 Parallel task (P1-P6)[/dim]")
        elif batch:
            from pipeline import parallel_signals
            console.print(f"[dim]{len(batch)} Parallel tasks created up front, polled together…[/dim]")
            parallel_by_cik = parallel_signals.collect_batch(batch)

    for company in targets:
        cik = int(company["cik"])
        sigs_e, errs_e = edgar_by_cik.get(cik, ([], []))
        sigs_p, errs_p = parallel_by_cik.get(cik, ([], []))
        if not dry_run:
            if source in ("edgar", "all"):
                db.replace_signals(cik, "edgar", sigs_e)
            if cik in parallel_by_cik and not errs_p:
                # only replace on task success — a failed task must not wipe
                # previously collected parallel signals
                db.replace_signals(cik, "parallel", sigs_p)
        _print_signals(company["ticker"], sigs_e + sigs_p, errs_e + errs_p)
        stats["companies"] += 1
        stats["signals"] += len(sigs_e) + len(sigs_p)
        stats["errors"] += len(errs_e) + len(errs_p)
        if not dry_run and company.get("status") in (None, "new", "enriched"):
            db.set_status(cik, "enriched")
```

Keep the trailing `if not dry_run: db.finish_run(run_id, stats)` and `console.print(f"Done: {stats}")` unchanged.

- [ ] **Step 2: Rewrite the people command loop**

In `src/pipeline/cli.py`, inside `people`, change the import line

```python
    from pipeline.people import find_people, target_roles
```

to

```python
    from pipeline.people import find_people_batch, target_roles
```

and replace everything from `run_id = None if dry_run else db.start_run("people")` through the end of the per-company loop (the block ending with `console.print(f"[dim]{notes}[/dim]")`) with:

```python
    run_id = None if dry_run else db.start_run("people")
    items: list[tuple[dict, list[dict]]] = []
    for company in targets:
        s = db.latest_score(company["cik"])
        fits = (s or {}).get("service_fit") or []
        if dry_run:
            console.print(f"{company['ticker']}: would search roles {target_roles(fits)}")
        else:
            items.append((company, fits))
    if dry_run:
        return

    console.print(f"[dim]{len(items)} Parallel people tasks created up front, polled together…[/dim]")
    results = find_people_batch(items)
    found_total = 0
    for (company, _), res in zip(items, results):
        if isinstance(res, Exception):
            console.print(f"[red]{company['ticker']}: people search failed: {res}[/red]")
            continue
        contacts, notes = res
        db.insert_contacts(contacts)
        db.set_status(company["cik"], "contacts_found")
        found_total += len(contacts)
        table = Table(title=f"{company['ticker']} — {company['name'][:40]}")
        for col in ("name", "title", "linkedin", "email", "conf"):
            table.add_column(col)
        for c in contacts:
            table.add_row(c.name, c.title[:40], (c.linkedin_url or "—")[:44], c.email or "—", c.confidence)
        console.print(table)
        if notes:
            console.print(f"[dim]{notes}[/dim]")
    if not dry_run:
        db.finish_run(run_id, {"companies": len(items), "contacts": found_total})
```

- [ ] **Step 3: Verify by dry-runs (no spend, no writes)**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python -m pipeline enrich --ticker CCLD --source parallel --dry-run
```

Expected: `CCLD: would run 1 Parallel task (P1-P6)` and an empty signals table — **no task created, returns in seconds**.

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python -m pipeline people --dry-run
```

Expected: either "No qualified companies awaiting people search." (current funnel has 0 qualified) or `TICKER: would search roles […]` lines. No spend.

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add src/pipeline/cli.py && git commit -m "feat: CLI uses batched Parallel calls; dry-run never spends; bulk signal fetch for pool filter"
```

---

### Task 5: EDGAR 8-K item-metadata filter

**Files:**
- Create: `/private/tmp/claude-501/-Users-udaykang-AI-Public-Traded/10152224-ecce-487b-8511-ab8e3b99b7bd/scratchpad/probe_8k_items.py` (probe, NOT committed)
- Modify: `src/pipeline/edgar_signals.py:240-303` (`eightk_signals`; add `_filing_items` + `ITEM_NUM` above it)
- Test: `tests/test_edgar_signals.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks (independent of Tasks 1–4).
- Produces: `_filing_items(filing) -> set[str]` (item numbers like `{"5.02", "9.01"}` from index metadata); `eightk_signals(edgar_company, company)` keeps its existing signature and Signal output shape.

- [ ] **Step 1: Probe — confirm edgartools exposes 8-K items without downloading documents**

Write the probe to the scratchpad path above:

```python
"""Probe: does edgartools expose 8-K item numbers from the filing index?"""
from edgar import Company, set_identity
from pipeline.config import edgar_identity

set_identity(edgar_identity())
c = Company("CCLD")
for f in list(c.get_filings(form="8-K"))[:10]:
    print(f.filing_date, "items=", repr(getattr(f, "items", None)))
```

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python "/private/tmp/claude-501/-Users-udaykang-AI-Public-Traded/10152224-ecce-487b-8511-ab8e3b99b7bd/scratchpad/probe_8k_items.py"
```

Expected: each line shows an items value containing numbers like `5.02` / `9.01` (list of strings or a comma-separated string — record which). **GATE: if `items` is `None`/empty for all filings, STOP — do not implement this task; report back that the metadata route is unavailable and the spec's section 2 needs a rethink.**

- [ ] **Step 2: Write the failing tests**

Create `tests/test_edgar_signals.py`:

```python
from datetime import date, timedelta

from pipeline import edgar_signals as es

COMPANY = {"cik": 999, "ticker": "TST"}
RECENT = date.today() - timedelta(days=30)


class FakeFiling:
    def __init__(self, items, text="", filing_date=RECENT):
        self.form = "8-K"
        self.filing_date = filing_date
        self.items = items
        self._text = text
        self.text_calls = 0
        self.accession_no = "0000000000-26-000001"

    def text(self):
        self.text_calls += 1
        return self._text


class FakeCompany:
    def __init__(self, filings):
        self._filings = filings

    def get_filings(self, form=None):
        return self._filings


def test_filing_items_handles_list_and_string():
    assert es._filing_items(FakeFiling(["5.02", "9.01"])) == {"5.02", "9.01"}
    assert es._filing_items(FakeFiling("Items 2.05, 9.01")) == {"2.05", "9.01"}
    assert es._filing_items(FakeFiling(None)) == set()


def test_irrelevant_items_skip_download():
    f = FakeFiling(["7.01", "9.01"], text="press release about a conference")
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert signals == []
    assert f.text_calls == 0


def test_item_502_yields_e3():
    text = ("On June 1, 2026 the board appointed Jane Roe as the company's "
            "chief financial officer, effective immediately.")
    f = FakeFiling(["5.02", "9.01"], text=text)
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert [s.type for s in signals] == ["E3"]
    assert "CFO" in signals[0].title
    assert f.text_calls == 1


def test_item_205_yields_e4():
    text = "The company committed to a restructuring plan to reduce operating costs."
    f = FakeFiling(["2.05"], text=text)
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert [s.type for s in signals] == ["E4"]


def test_old_filing_stops_scan():
    old = FakeFiling(["5.02"], text="appointed chief executive officer",
                     filing_date=date.today() - timedelta(days=400))
    signals = es.eightk_signals(FakeCompany([old]), COMPANY)
    assert signals == []
    assert old.text_calls == 0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_edgar_signals.py -v
```

Expected: FAIL — `AttributeError: module 'pipeline.edgar_signals' has no attribute '_filing_items'`.

- [ ] **Step 4: Implement the filter**

In `src/pipeline/edgar_signals.py`, insert directly above `def eightk_signals` (line 240):

```python
ITEM_NUM = re.compile(r"\d+\.\d+")


def _filing_items(filing) -> set[str]:
    """Item numbers (e.g. {'5.02'}) from filing-index metadata — no document download."""
    raw = getattr(filing, "items", None)
    if raw is None:
        return set()
    if isinstance(raw, str):
        return set(ITEM_NUM.findall(raw))
    try:
        return {m for part in raw for m in ITEM_NUM.findall(str(part))}
    except TypeError:
        return set()
```

Then replace the body of `eightk_signals` (lines 240–303) with:

```python
def eightk_signals(edgar_company, company: dict) -> list[Signal]:
    cik = company["cik"]
    lookback = int(SETTINGS.get("enrich", {}).get("edgar", {}).get("eightk_lookback_days", 365))
    cutoff = date.today() - timedelta(days=lookback)
    signals: list[Signal] = []
    seen_types: set[str] = set()

    for filing in edgar_company.get_filings(form="8-K"):
        fdate = getattr(filing, "filing_date", None)
        if fdate is None or fdate < cutoff:
            break  # filings are newest-first
        items = _filing_items(filing)
        # strict item filter (spec decision): 5.02 = exec change, 2.05 =
        # restructuring charge. Only these are worth a document download.
        want_e3 = "E3" not in seen_types and "5.02" in items
        want_e4 = "E4" not in seen_types and "2.05" in items
        if not (want_e3 or want_e4):
            continue
        try:
            text = filing.text()
        except Exception:
            continue
        if not text:
            continue
        lower = text.lower()

        if want_e3:
            hit_titles = [
                abbrev for phrase, abbrev in EXEC_TITLES.items() if phrase in lower
            ]
            is_appointment = any(w in lower for w in APPOINT_WORDS)
            if hit_titles and is_appointment:
                # anchor the quote at the appointment word nearest an exec
                # title — first-in-document lands on cover-page boilerplate
                appoint_positions = [
                    m.start() for w in APPOINT_WORDS for m in re.finditer(re.escape(w), lower)
                ]
                title_positions = [
                    m.start() for p in EXEC_TITLES for m in re.finditer(re.escape(p), lower)
                ]
                pos = min(
                    appoint_positions,
                    key=lambda ap: min(abs(ap - tp) for tp in title_positions),
                ) if appoint_positions and title_positions else 0
                window = lower[max(0, pos - 250): pos + 450]
                near = sorted({a for p, a in EXEC_TITLES.items() if p in window})
                if near:
                    hit_titles = near
                quote = " ".join(text[max(0, pos - 150): pos + 250].split())
                signals.append(Signal(
                    company_cik=cik, source="edgar", type="E3",
                    title=f"Leadership change ≤12mo: {', '.join(sorted(set(hit_titles)))}",
                    detail=f"8-K Item 5.02 filed {fdate}",
                    evidence_url=_filing_url(cik, filing), evidence_quote=quote,
                    observed_at=fdate, weight=_w("E3"),
                    raw={"titles": sorted(set(hit_titles))},
                ))
                seen_types.add("E3")

        if want_e4:
            phrase_hit = next((p for p in RESTRUCTURING_PHRASES if p in lower), None)
            pos = lower.find(phrase_hit) if phrase_hit else max(lower.find("item 2.05"), 0)
            quote = " ".join(text[max(0, pos - 100): pos + 300].split())
            signals.append(Signal(
                company_cik=cik, source="edgar", type="E4",
                title="Restructuring / cost-reduction program announced",
                detail=f"8-K Item 2.05 filed {fdate}" + (f' ("{phrase_hit}")' if phrase_hit else ""),
                evidence_url=_filing_url(cik, filing), evidence_quote=quote,
                observed_at=fdate, weight=_w("E4"), raw={},
            ))
            seen_types.add("E4")

        if {"E3", "E4"} <= seen_types:
            break
    return signals
```

Behavior deltas vs current code, all intended by the spec: no more full-text download for irrelevant 8-Ks; E4 requires Item 2.05 in metadata (phrase-only press releases under 7.01/8.01 no longer fire E4); E4 fires on the 2.05 item even when no known phrase matches (the item itself IS the restructuring-charge disclosure); no more false E3 from a stray "5.02" string in unrelated text.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_edgar_signals.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Live verification against a known-E3 company**

Find a company whose DB signals include a recent E3, then dry-run it (CLAUDE.md collector-change rule):

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python -c "
from pipeline import db
comps = {int(c['cik']): c['ticker'] for c in db.get_companies()}
rows = []
for cik, sigs in db.all_signals().items():
    for s in sigs:
        if s['type'] == 'E3' and s.get('observed_at'):
            rows.append((str(s['observed_at'])[:10], comps.get(cik, '?'), s['title']))
for r in sorted(rows, reverse=True)[:5]:
    print(*r)
"
```

Pick the freshest ticker printed (call it `<T>`), then:

```bash
cd "/Users/udaykang/AI_Public Traded" && time uv run python -m pipeline enrich --ticker "<T>" --dry-run
```

Expected: the signals table still shows the E3 with the **same filing date** as the DB row, and the run is visibly fast (seconds of 8-K work; the 10-K/proxy parsing dominates). No DB writes (dry run).

- [ ] **Step 7: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add src/pipeline/edgar_signals.py tests/test_edgar_signals.py && git commit -m "perf: filter 8-Ks by index item metadata before downloading text"
```

---

### Task 6: Scoring packet dedupe (`_shared.json`)

**Files:**
- Modify: `src/pipeline/scoring.py:147-197` (`prepare`), `:199-263` (`commit`), `:266-267` (`pending_queue`)
- Test: `tests/test_scoring.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks (independent of Tasks 1–5).
- Produces: `data/scoring_queue/_shared.json` with keys `services_catalog`, `rubric`, `output_schema`, `instructions`; slim packets with new keys `shared_file` (posix path string) and `output_path` (posix path string) and WITHOUT `services_catalog`/`rubric`/`output_schema`. `prepare`/`commit`/`pending_queue` keep their signatures. Task 7's /score skill text depends on exactly these key names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scoring.py`:

```python
import json
from pathlib import Path

import pytest

from pipeline import scoring

COMPANY = {
    "cik": 1, "ticker": "TST", "name": "Test Co", "exchange": "Nasdaq",
    "sector_bucket": "saas", "market_cap": 1e8, "sic_description": "software",
    "website": None, "hq_state": "CA", "status": "enriched",
}
SIGNAL = {
    "type": "E1", "source": "edgar", "title": "AI language", "detail": "d",
    "evidence_url": None, "evidence_quote": None, "observed_at": "2026-06-01",
    "weight": 15.0,
}


class FakeDB:
    def __init__(self):
        self.scores = []
        self.statuses = []

    def get_companies(self, status=None):
        return [dict(COMPANY)] if status in (None, "enriched") else []

    def all_signals(self):
        return {1: [dict(SIGNAL)]}

    def get_company_by_ticker(self, ticker):
        return dict(COMPANY) if ticker == "TST" else None

    def insert_score(self, row):
        self.scores.append(row)

    def set_status(self, cik, status, profile=None):
        self.statuses.append((cik, str(status), profile))


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    q, r, a = tmp_path / "queue", tmp_path / "results", tmp_path / "archive"
    for d in (q, r, a):
        d.mkdir()
    monkeypatch.setattr(scoring, "QUEUE_DIR", q)
    monkeypatch.setattr(scoring, "RESULTS_DIR", r)
    monkeypatch.setattr(scoring, "ARCHIVE_DIR", a)
    monkeypatch.setattr(scoring, "db", FakeDB())
    return q, r, a


def test_prepare_writes_shared_file_and_slim_packets(dirs):
    q, r, a = dirs
    written = scoring.prepare()

    shared = json.loads((q / "_shared.json").read_text())
    assert set(shared) == {"services_catalog", "rubric", "output_schema", "instructions"}

    assert written == [str(q / "TST.json")]
    packet = json.loads((q / "TST.json").read_text())
    for heavy in ("services_catalog", "rubric", "output_schema"):
        assert heavy not in packet
    assert packet["shared_file"] == (q / "_shared.json").as_posix()
    assert packet["output_path"] == (r / "TST.json").as_posix()
    assert "_shared.json" in packet["instructions"]
    assert packet["base_score"]["total"] > 0


def test_pending_queue_ignores_shared_file(dirs):
    q, r, a = dirs
    scoring.prepare()
    names = [p.name for p in scoring.pending_queue()]
    assert names == ["TST.json"]


# a real archived verdict doubles as a schema-valid result fixture
_ARCHIVED = sorted(
    p for p in Path("data/scoring_archive").rglob("*.json")
    if not p.name.startswith(("packet_", "_"))
)


@pytest.mark.skipif(not _ARCHIVED, reason="no archived verdicts on this machine")
def test_commit_archives_shared_file_and_drains_queue(dirs):
    q, r, a = dirs
    scoring.prepare()
    (r / "TST.json").write_text(_ARCHIVED[0].read_text())

    summary = scoring.commit(run_id="testrun")

    assert not summary["invalid"] and not summary["orphan"]
    buckets = [b for b in ("qualified", "review", "disqualified")
               if any(i["ticker"] == "TST" for i in summary[b])]
    assert len(buckets) == 1
    run_dir = a / "testrun"
    assert (run_dir / "_shared.json").exists()
    assert (run_dir / "TST.json").exists()
    assert (run_dir / "packet_TST.json").exists()
    assert not (q / "_shared.json").exists()  # queue drained -> shared file removed
    assert scoring.db.scores and scoring.db.statuses
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_scoring.py -v
```

Expected: FAIL — `KeyError`/`AssertionError` (packets still embed rubric; no `_shared.json`).

- [ ] **Step 3: Implement the dedupe**

In `src/pipeline/scoring.py`, inside `prepare()`, replace from `schema = ScoreVerdict.model_json_schema()` down to (and including) the `packet = { ... }` block with:

```python
    schema = ScoreVerdict.model_json_schema()
    shared_path = QUEUE_DIR / "_shared.json"
    shared_path.write_text(json.dumps({
        "services_catalog": SERVICES,
        "rubric": RUBRIC,
        "output_schema": schema,
        "instructions": (
            "This file is identical for every packet in the queue — read it ONCE. "
            "For each packet: score the company against `rubric` using "
            "`services_catalog`, and write a verdict JSON matching `output_schema` "
            "EXACTLY to the packet's `output_path`."
        ),
    }, indent=2, default=str))

    peers = db.get_companies()
    signals_by_cik = db.all_signals()
    written: list[str] = []
    for company in companies:
        signals = signals_by_cik.get(int(company["cik"]), [])
        slim_signals = [
            {k: s.get(k) for k in ("type", "source", "title", "detail", "evidence_url", "evidence_quote", "observed_at", "weight")}
            for s in signals
        ]
        for s in slim_signals:
            s["age_days"] = _signal_age_days(s)
            s["effective_weight"] = effective_weight(s)
        derived = _derived_cohort_signal(company, slim_signals, peers, signals_by_cik)
        if derived:
            slim_signals.append(derived)
        output_path = (RESULTS_DIR / (company["ticker"] + ".json")).as_posix()
        packet = {
            "ticker": company["ticker"],
            "company": {
                k: company.get(k)
                for k in ("cik", "ticker", "name", "exchange", "sector_bucket", "market_cap", "sic_description", "website", "hq_state")
            },
            "signals": slim_signals,
            "base_score": base_components(slim_signals),
            "hard_signals_present": sorted(
                {s["type"] for s in slim_signals}
                & set(SETTINGS.get("scoring", {}).get("hard_signals", []))
            ),
            "shared_file": shared_path.as_posix(),
            "output_path": output_path,
            "instructions": (
                f"First read {shared_path.as_posix()} ONCE per batch — it holds the "
                f"rubric, services_catalog, and output_schema shared by every packet. "
                f"Then write your verdict as JSON matching output_schema EXACTLY to: "
                f"{output_path} . Component scores are integers within their maximums. "
                "reasoning must cite packet evidence. Do not add fields. Do not wrap "
                "in markdown."
            ),
        }
```

(The `path = QUEUE_DIR / f"{company['ticker']}.json"` / `write_text` / `written.append` lines stay as they are. Note `peers`/`signals_by_cik` simply move above the loop start if they aren't already — keep the existing single fetch, don't fetch twice.)

In `commit()`, after `archive.mkdir(parents=True, exist_ok=True)`, add:

```python
    shared_path = QUEUE_DIR / "_shared.json"
    if shared_path.exists():
        shutil.copy2(shared_path, archive / "_shared.json")
```

and at the very end of `commit()`, before `return summary`, add:

```python
    if shared_path.exists() and not pending_queue():
        shared_path.unlink()  # queue fully drained — next prepare rewrites it
```

Replace `pending_queue`:

```python
def pending_queue() -> list[Path]:
    return sorted(p for p in QUEUE_DIR.glob("*.json") if not p.name.startswith("_"))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest tests/test_scoring.py -v
```

Expected: 3 passed (the commit test skips only on a machine with no `data/scoring_archive/` verdicts).

- [ ] **Step 5: End-to-end packet check with real code paths (no DB writes)**

`score --prepare` reads real Supabase data; the funnel currently has 0 `enriched` companies, so exercise `prepare` against the review band without committing anything:

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python -c "
from pipeline import scoring
paths = scoring.prepare(limit=2, statuses=('scored',))
print(paths)
" && ls "/Users/udaykang/AI_Public Traded/data/scoring_queue/"
```

Expected: `_shared.json` plus 2 slim ticker packets. Inspect one packet — no `rubric`/`services_catalog`/`output_schema` keys; `wc -c` shows it well under half the old ~10KB. Then clean up so no stale queue lingers:

```bash
rm "/Users/udaykang/AI_Public Traded/data/scoring_queue/"*.json
```

- [ ] **Step 6: Commit**

```bash
cd "/Users/udaykang/AI_Public Traded" && git add src/pipeline/scoring.py tests/test_scoring.py && git commit -m "feat: dedupe scoring packets — shared rubric/catalog/schema in _shared.json"
```

---

### Task 7: Skill-file updates

**Files:**
- Modify: `.claude/skills/enrich/SKILL.md`
- Modify: `.claude/skills/people/SKILL.md`
- Modify: `.claude/skills/score/SKILL.md`
- Modify: `.claude/skills/status/SKILL.md`

**Interfaces:**
- Consumes: Task 4's CLI behavior (no-spend dry-run, per-company failure isolation), Task 6's `_shared.json` + `output_path` packet keys.
- Produces: skill text only. No tests — verify by reading the diffs.

- [ ] **Step 1: Update `.claude/skills/enrich/SKILL.md`**

Replace the paragraph starting `Both: \`--source all\`.` with:

```markdown
Both: `--source all`. Single company (works even before it's in the DB): `--ticker XYZ`.
Preview with `--dry-run` — for Parallel this lists which companies WOULD get a task
and **never spends**. Re-enrich already-enriched companies: `--force`.
```

Replace the `Ground rules:` list with:

```markdown
Ground rules:
- EDGAR before Parallel (free before paid); don't run Parallel on companies the user hasn't asked to prioritize unless batch is small.
- Respect the config caps; never loop Parallel calls around them.
- One company failing must not stop the batch — failures are logged per company; report them at the end. A failed/timed-out Parallel task keeps the company's previous parallel signals; retry it alone with `--ticker X --source parallel --force`.
- **Run multi-company batches in the background** (Bash `run_in_background`) and report from the final `Done: {...}` stats line plus DB counts — don't stream dozens of per-company tables into the conversation.
- Timing: Parallel tasks are created up front and polled together, so a full 25-company batch ≈ its slowest single task (~3–5 min). EDGAR batches run at SEC-polite rates — 8-Ks are filtered by index metadata, so expect seconds per company plus 10-K parsing.
```

- [ ] **Step 2: Update `.claude/skills/people/SKILL.md`**

After the "How targeting works" paragraph, insert:

```markdown
Run mechanics:
- **Run multi-account batches in the background** and report from the final stats + DB — tasks are created up front and polled together, so a 10-account batch ≈ one task's duration (~2–4 min).
- If one account's task fails or times out, the batch continues without it and the company keeps its `qualified` status; retry just that account with `--ticker X`.
```

- [ ] **Step 3: Update `.claude/skills/score/SKILL.md`**

Replace the whole "## Step 2 — spawn Haiku subagents to reason" section body (intro paragraph + blockquote) with:

```markdown
List the queued packets (`data/scoring_queue/*.json` — ignore `_shared.json`, it is
the shared rubric/catalog/schema, not a packet), then spawn **Agent tool subagents
with `model: haiku`**, giving each subagent a batch of **up to 5 packet file paths**.
Spawn batches in parallel (single message, multiple Agent calls). Each subagent
prompt must say:

> You are a B2B account scorer for an AI-services company. First read
> `data/scoring_queue/_shared.json` ONCE — it holds the rubric, services catalog, and
> required output schema shared by every packet. Then for EACH packet file listed
> below: (1) Read the JSON packet. (2) Follow the shared `rubric` and the packet's
> `instructions` exactly. (3) Write your verdict as JSON to the packet's
> `output_path` (match `output_schema` exactly; component scores must respect their
> max values; `reasoning` must cite specific evidence quotes/URLs from the packet;
> never invent facts not in the packet). Process every packet. Reply only with a
> one-line summary per ticker: `TICKER total profile`.
>
> Packets: <absolute paths>
```

Keep the existing `Rules:` list unchanged.

- [ ] **Step 4: Update `.claude/skills/status/SKILL.md`**

In the "Then summarize for the user" list, add two bullets after the existing ones:

```markdown
- The **review band** (status `scored`: total between `disqualify_below` and `qualify_threshold`) is a human decision queue — when it's non-empty, list its companies with scores and ask whether any should be promoted.
- Promote review-band companies the user approves with `uv run python -m pipeline promote TICK1,TICK2` (this is the human review-band decision — never promote without the user saying so).
```

- [ ] **Step 5: Review and commit**

Read each modified skill file end-to-end once — commands must match the CLI exactly (`--force`, `--dry-run`, `promote`), and the /score prompt must reference `_shared.json` and `output_path` (Task 6's key names).

```bash
cd "/Users/udaykang/AI_Public Traded" && git add .claude/skills && git commit -m "docs: skill files — background batches, no-spend dry-run, _shared.json scoring, promote command"
```

---

### Task 8: Live fan-out verification + final checks

**Files:**
- Create: `/private/tmp/claude-501/-Users-udaykang-AI-Public-Traded/10152224-ecce-487b-8511-ab8e3b99b7bd/scratchpad/verify_fanout.py` (NOT committed)

**Interfaces:**
- Consumes: `collect_batch` (Task 2), everything else already merged.
- Produces: timing evidence for the spec's ~5-min target. **This step spends real money: exactly 3 Parallel base-processor tasks (approved in the spec §Testing item 3). Do not increase the count.**

- [ ] **Step 1: Write the verification script**

```python
"""Live fan-out check: 3 Parallel tasks created up front, polled together.

Reads 3 real companies from the DB; NO db writes — results printed only.
"""
import time

from pipeline import db
from pipeline import parallel_signals

tickers = ["LNSR", "SSTI", "CHGG"]  # any 3 companies present in the DB
companies = [c for t in tickers if (c := db.get_company_by_ticker(t))]
assert len(companies) == 3, f"resolve failed: {[c['ticker'] for c in companies]}"

t0 = time.time()
out = parallel_signals.collect_batch(companies)
dt = time.time() - t0

print(f"\n{dt:.0f}s wall-clock for {len(companies)} companies (sequential would be ~3x a single task)")
for company in companies:
    sigs, errs = out[int(company["cik"])]
    print(f"{company['ticker']}: {len(sigs)} signals, errors={errs}")
```

- [ ] **Step 2: Run it**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run python "/private/tmp/claude-501/-Users-udaykang-AI-Public-Traded/10152224-ecce-487b-8511-ab8e3b99b7bd/scratchpad/verify_fanout.py"
```

Expected: total wall-clock in the 1–5 minute range and clearly below 3× a single task; 0–6 signals per company; no errors (a single timeout is acceptable — note it, don't rerun).

- [ ] **Step 3: Full suite + funnel sanity**

```bash
cd "/Users/udaykang/AI_Public Traded" && uv run pytest -v && uv run python -m pipeline status --brief
```

Expected: all tests pass; funnel counts unchanged from before this plan (`0 new | 0 enriched | 33 scored | 0 qualified | 137 disqualified | 10 contacts_found`).

- [ ] **Step 4: Report**

Summarize to the user: measured fan-out wall-clock vs the old sequential estimate, packet size before/after (~10KB → roughly half), and the strict-E4 behavior change now live. No commit (nothing tracked changed in this task).
