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
