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


def test_collect_batch_isolates_parse_failures(monkeypatch):
    ok = _result({"ai_announcements": {"found": True, "summary": "launched AI pilot"}})
    monkeypatch.setattr(ps, "run_tasks_batch",
                        lambda tasks, processor="base", timeout_s=600: [{}, ok])
    other = dict(COMPANY, cik=456, ticker="OTH")

    out = ps.collect_batch([COMPANY, other])

    sigs, errs = out[123]
    assert sigs == [] and "parse failed" in errs[0]
    assert [s.type for s in out[456][0]] == ["P3"]


def test_collect_batch_empty_input():
    assert ps.collect_batch([]) == {}


DEEP_COMPANY = {"cik": 999, "ticker": "TST", "name": "Test Co", "sector_bucket": "saas", "market_cap": 1e8}


def _deep_result(extra):
    content = {k: {"found": False, "summary": "n/a"} for k in (
        "ai_job_postings", "gtm_hiring", "ai_announcements",
        "product_ai_gap", "martech_stack", "exec_ai_commentary")}
    content.update(extra)
    return {"content": content, "basis": []}


def test_deep_schema_extends_enrich_schema():
    assert "leadership_hires" in ps.DEEP_SCHEMA["properties"]
    assert "ai_job_postings" in ps.DEEP_SCHEMA["properties"]


def test_leadership_hire_maps_to_angle():
    result = _deep_result({"leadership_hires": [{
        "role": "Chief Revenue Officer", "person_name": "Jane Roe",
        "start_date": "2026-05-01", "mandate_quote": "My mandate is pipeline efficiency",
        "source_url": "https://news.example/cro",
    }]})
    angles_out, warnings = ps._angles_from_result(DEEP_COMPANY, result)
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
    angles_out, warnings = ps._angles_from_result(DEEP_COMPANY, result)
    assert angles_out[0].family.value == "ai_move"
    assert angles_out[0].details["partner"] == "Google"


def test_undated_item_dropped_with_warning():
    result = _deep_result({"leadership_hires": [{"role": "CRO"}]})
    angles_out, warnings = ps._angles_from_result(DEEP_COMPANY, result)
    assert angles_out == []
    assert any("no date" in w for w in warnings)


def test_invalid_item_isolated_not_fatal():
    result = _deep_result({"ai_moves": [
        {"move_type": "product_launch", "announced": "2026-04-15"},  # missing initiative
        {"initiative": "Real Thing", "announced": "2026-04-15"},
    ]})
    angles_out, warnings = ps._angles_from_result(DEEP_COMPANY, result)
    assert len(angles_out) == 1
    assert len(warnings) == 1
