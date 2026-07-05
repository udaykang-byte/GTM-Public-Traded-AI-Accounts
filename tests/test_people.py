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


def test_find_people_batch_isolates_parse_failures(monkeypatch):
    monkeypatch.setattr(people, "run_tasks_batch",
                        lambda tasks, processor="base", timeout_s=600: [
                            {},  # malformed: no "content" key
                            {"content": CONTENT, "basis": []},
                        ])
    other = dict(COMPANY, cik=456, ticker="OTH")

    results = people.find_people_batch([(COMPANY, FIT), (other, FIT)])

    assert isinstance(results[0], Exception)
    contacts, _ = results[1]
    assert contacts[0].name == "Jane Roe"


def test_find_people_batch_empty_input():
    assert people.find_people_batch([]) == []
