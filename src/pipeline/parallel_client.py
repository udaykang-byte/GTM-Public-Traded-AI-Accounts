"""Thin client for the Parallel Task API (https://docs.parallel.ai).

Auth resolution order:
  1. PARALLEL_API_KEY in .env
  2. credentials stored by `parallel-cli login` (token file)

All calls are structured-output task runs. Costs money — callers must respect
the caps in config/settings.yaml.
"""
from __future__ import annotations

import json
import subprocess
import time

import httpx

from pipeline.config import env

API_BASE = "https://api.parallel.ai"


class ParallelAuthError(SystemExit):
    pass


_cached_key: str | None = None


def resolve_api_key() -> str:
    global _cached_key
    if _cached_key:
        return _cached_key
    key = env("PARALLEL_API_KEY")
    if key:
        _cached_key = key
        return key
    # Fall back to parallel-cli stored credentials
    try:
        out = subprocess.run(
            ["parallel-cli", "auth", "--json"], capture_output=True, text=True, timeout=15
        )
        info = json.loads(out.stdout or "{}")
        token_file = info.get("token_file")
        if info.get("authenticated") and token_file:
            stored = json.loads(open(token_file).read())
            # documented layout: orgs[selected_org_id].api_key
            org_id = stored.get("selected_org_id")
            org = (stored.get("orgs") or {}).get(org_id) or {}
            if org.get("api_key"):
                _cached_key = org["api_key"]
                return _cached_key
            # fallback: scan for anything that looks like an API/service key
            def _find_key(node) -> str | None:
                if isinstance(node, dict):
                    for k, v in node.items():
                        lk = k.lower()
                        if isinstance(v, str) and v and any(t in lk for t in ("api_key", "service_key", "apikey")):
                            return v
                    for v in node.values():
                        found = _find_key(v)
                        if found:
                            return found
                if isinstance(node, list):
                    for v in node:
                        found = _find_key(v)
                        if found:
                            return found
                return None

            found = _find_key(stored)
            if found:
                _cached_key = found
                return found
    except FileNotFoundError:
        pass
    except Exception:
        pass
    raise ParallelAuthError(
        "No Parallel credentials. Either set PARALLEL_API_KEY in .env "
        "(platform.parallel.ai -> API keys) or run `parallel-cli login`."
    )


def _headers() -> dict:
    return {"x-api-key": resolve_api_key(), "Content-Type": "application/json"}


def create_task_run(input_text: str, output_schema: dict, processor: str = "base") -> str:
    """Start a task run, return run id."""
    body = {
        "input": input_text,
        "processor": processor,
        "task_spec": {
            "output_schema": {"type": "json", "json_schema": output_schema},
        },
    }
    resp = httpx.post(f"{API_BASE}/v1/tasks/runs", headers=_headers(), json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()["run_id"]


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


def run_task(input_text: str, output_schema: dict, processor: str = "base", timeout_s: int = 600) -> dict:
    run_id = create_task_run(input_text, output_schema, processor)
    return wait_for_result(run_id, timeout_s=timeout_s)


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
    results: list[dict | Exception | None] = [None] * len(tasks)
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
            except Exception:
                continue  # transient poll error — retry next round; the deadline bounds it
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
