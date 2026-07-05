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
