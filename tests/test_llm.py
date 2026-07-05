import json

from pipeline.llm import packet_shared_fields


def test_packet_shared_fields_prefers_embedded_keys():
    packet = {"rubric": "R", "services_catalog": {"s": 1}, "output_schema": {"o": 2}}
    assert packet_shared_fields(packet) == ("R", {"s": 1}, {"o": 2})


def test_packet_shared_fields_reads_shared_file(tmp_path):
    shared = tmp_path / "_shared.json"
    shared.write_text(json.dumps({"rubric": "R2", "services_catalog": {"s": 3},
                                  "output_schema": {"o": 4}, "instructions": "x"}))
    packet = {"shared_file": str(shared), "ticker": "TST"}
    assert packet_shared_fields(packet) == ("R2", {"s": 3}, {"o": 4})
