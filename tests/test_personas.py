"""personas.yaml: persona-driven role targeting + cold-email personalization.

The default pack (config/personas.yaml) must be behavior-identical to the
legacy flat config people.roles_by_service lookup for target_roles() — this
file locks that equality (see test_target_roles_identical_to_legacy_path).
match_persona() is the new lookup messages.py uses to attach a persona block
to a message packet; it must accept legacy role_bucket values already in the
DB (e.g. "CEO") as well as full titles that the old substring test never
matched (e.g. "Chief Marketing Officer" for role token "CMO").
"""
import pytest

from pipeline import config, people

FIT_CASES = [
    [],
    [{"service": "ai_lead_generation", "priority": 1}],
    [{"service": "ai_outreach", "priority": 1}],
    [{"service": "ai_marketing", "priority": 1}],
    [{"service": "custom_ai_agents", "priority": 1}],
    [{"service": "ai_consultation", "priority": 1}],
    [{"service": "ai_outreach", "priority": 1}, {"service": "ai_consultation", "priority": 2}],
    [{"service": "custom_ai_agents", "priority": 1}, {"service": "ai_lead_generation", "priority": 2},
     {"service": "ai_marketing", "priority": 3}],
]


# ---------- default pack shape ----------

def test_default_pack_loads_personas_and_services_mapping():
    assert config.PERSONAS  # non-empty for the default pack
    assert "services" in config.PERSONAS
    persona_keys = {k for k in config.PERSONAS if k != "services"}
    assert {"ceo", "cmo", "cto", "cfo"} <= persona_keys
    for key, persona in config.PERSONAS.items():
        if key == "services":
            continue
        assert set(persona) == {"role_bucket", "titles", "seniority", "committee_role", "pains", "language"}
        assert len(persona["pains"]) == 3
        assert set(persona["language"]) == {"their_words", "avoid"}


def test_default_pack_services_mapping_only_references_known_personas():
    persona_keys = {k for k in config.PERSONAS if k != "services"}
    for service, keys in config.PERSONAS["services"].items():
        for key in keys:
            assert key in persona_keys, f"{service} references unknown persona '{key}'"


# ---------- target_roles(): personas path vs legacy path ----------

@pytest.mark.parametrize("fits", FIT_CASES)
def test_target_roles_identical_to_legacy_path(fits, monkeypatch):
    """Locks target_roles() output equality: personas path (default pack,
    real PERSONAS) vs legacy path (PERSONAS={})."""
    with_personas = people.target_roles(fits)
    monkeypatch.setattr(people, "PERSONAS", {})
    legacy = people.target_roles(fits)
    assert with_personas == legacy


def test_target_roles_uses_legacy_roles_by_service_when_personas_empty(monkeypatch):
    monkeypatch.setattr(people, "PERSONAS", {})
    monkeypatch.setitem(people.SETTINGS, "people", {
        "always_include_roles": ["CEO"],
        "roles_by_service": {"widget_service": ["Widget Lead"]},
    })
    result = people.target_roles([{"service": "widget_service", "priority": 1}])
    assert result == ["CEO", "Widget Lead"]


def test_target_roles_personas_path_caps_at_six():
    fits = [{"service": "custom_ai_agents", "priority": 1}, {"service": "ai_lead_generation", "priority": 2}]
    result = people.target_roles(fits)
    assert len(result) <= 6


# ---------- match_persona ----------

def test_match_persona_accepts_legacy_role_bucket_value():
    """"CEO" is the exact role_bucket the legacy keyword match already
    produces for CEO contacts — must still resolve a persona."""
    persona = people.match_persona("CEO", "CEO")
    assert persona is not None
    assert persona["role_bucket"] == "CEO"


def test_match_persona_role_bucket_match_is_case_insensitive():
    persona = people.match_persona("cmo", "")
    assert persona is not None
    assert persona["role_bucket"] == "CMO"


def test_match_persona_falls_back_to_title_variant_when_role_bucket_empty():
    """Real-world case the legacy matcher mishandles: 'cmo' is not a
    substring of 'chief marketing officer', so many real contacts land with
    role_bucket == "". match_persona must still resolve via titles."""
    persona = people.match_persona("", "Chief Marketing Officer")
    assert persona is not None
    assert persona["role_bucket"] == "CMO"


def test_match_persona_title_fallback_matches_either_direction_substring():
    # a slightly embellished title ("VP of Marketing, Growth") still hits
    # the "VP of Marketing" variant via substring-in-either-direction
    persona = people.match_persona("", "VP of Marketing, Growth")
    assert persona is not None
    assert persona["role_bucket"] == "VP Marketing"


def test_match_persona_returns_none_when_nothing_matches():
    assert people.match_persona("", "Regional Facilities Manager") is None


def test_match_persona_returns_none_when_role_bucket_and_title_both_empty():
    assert people.match_persona("", "") is None


def test_match_persona_returns_none_when_personas_empty(monkeypatch):
    monkeypatch.setattr(people, "PERSONAS", {})
    assert people.match_persona("CEO", "CEO") is None
