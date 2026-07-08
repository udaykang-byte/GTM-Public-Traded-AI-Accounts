"""Profile packs: config/ is the default pack; profiles/<name>/ overlays it
per-file (a pack file fully replaces the default counterpart, never merged).
Activation must mutate SETTINGS/SERVICES in place — other modules alias the
same dict objects via `from pipeline.config import SETTINGS`."""
import pytest

from pipeline import config, scoring


@pytest.fixture(autouse=True)
def _reset_profile():
    """Every test starts from and restores the default pack."""
    yield
    config.activate_profile(None)


def test_default_pack_is_config_dir():
    assert config.PROFILE_DIR == config.DEFAULT_PROFILE_DIR == config.PROJECT_ROOT / "config"


def test_activate_profile_mutates_in_place_not_rebinds():
    settings_obj_id = id(config.SETTINGS)
    services_obj_id = id(config.SERVICES)
    config.activate_profile(None)  # no-op reload of the default pack
    assert id(config.SETTINGS) == settings_obj_id
    assert id(config.SERVICES) == services_obj_id


def test_scoring_settings_is_config_settings_after_activation():
    config.activate_profile(None)
    assert scoring.SETTINGS is config.SETTINGS


def test_activate_unknown_profile_raises():
    with pytest.raises(SystemExit):
        config.activate_profile("does-not-exist")


def test_profile_file_overlay_falls_back_to_default(tmp_path, monkeypatch):
    # pack with only settings.yaml -> services.yaml falls back to config/
    pack = tmp_path / "profiles" / "partial"
    pack.mkdir(parents=True)
    (pack / "settings.yaml").write_text("universe:\n  sectors: {}\n")
    monkeypatch.setattr(config, "PROFILES_ROOT", tmp_path / "profiles")
    config.activate_profile("partial")
    assert config.profile_file("settings.yaml") == pack / "settings.yaml"
    assert config.profile_file("services.yaml") == config.DEFAULT_PROFILE_DIR / "services.yaml"


def test_activate_profile_overrides_settings_content(tmp_path, monkeypatch):
    pack = tmp_path / "profiles" / "acme"
    pack.mkdir(parents=True)
    (pack / "settings.yaml").write_text("universe:\n  sectors:\n    widgets: {sic: ['1234']}\n")
    monkeypatch.setattr(config, "PROFILES_ROOT", tmp_path / "profiles")
    config.activate_profile("acme")
    assert "widgets" in config.SETTINGS["universe"]["sectors"]
    # per-file replace, never per-key merge
    assert "saas" not in config.SETTINGS["universe"]["sectors"]


def test_activate_default_restores_builtin_pack(tmp_path, monkeypatch):
    pack = tmp_path / "profiles" / "acme"
    pack.mkdir(parents=True)
    (pack / "settings.yaml").write_text("universe:\n  sectors:\n    widgets: {}\n")
    monkeypatch.setattr(config, "PROFILES_ROOT", tmp_path / "profiles")
    config.activate_profile("acme")
    config.activate_profile(None)
    assert config.PROFILE_DIR == config.DEFAULT_PROFILE_DIR
    assert "saas" in config.SETTINGS["universe"]["sectors"]


def test_list_profiles_includes_default_and_custom(tmp_path, monkeypatch):
    (tmp_path / "profiles" / "acme").mkdir(parents=True)
    monkeypatch.setattr(config, "PROFILES_ROOT", tmp_path / "profiles")
    assert config.list_profiles() == ["default", "acme"]


def test_list_profiles_without_profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_ROOT", tmp_path / "nonexistent")
    assert config.list_profiles() == ["default"]


# ---------- free sector vocabulary ----------

def test_company_sector_bucket_accepts_arbitrary_lowercase_string():
    from pipeline.models import Company

    c = Company(cik=1, ticker="T", name="Test", sector_bucket="widgets")
    assert c.sector_bucket == "widgets"


def test_company_sector_bucket_lowercases_input():
    from pipeline.models import Company

    c = Company(cik=1, ticker="T", name="Test", sector_bucket="SaaS")
    assert c.sector_bucket == "saas"


def test_company_sector_bucket_defaults_to_other():
    from pipeline.models import Company

    c = Company(cik=1, ticker="T", name="Test")
    assert c.sector_bucket == "other"


def test_classify_sector_supports_custom_vocabulary(monkeypatch):
    from pipeline import universe

    monkeypatch.setitem(
        universe.SETTINGS, "universe",
        {"sectors": {"widgets": {"sic": ["1234"], "keywords": []}}, "generic_tech_sic": []},
    )
    result = universe.classify_sector("1234", "Acme Widgets", "widget maker")
    assert result == "widgets"
    assert type(result) is str
    assert universe.classify_sector("9999", "Acme Other", "unrelated") == "other"


def test_classify_sector_default_pack_behavior_unchanged():
    from pipeline import universe

    # spot-check the built-in vocabulary against config/settings.yaml rules
    assert universe.classify_sector("6141", "Consumer Credit Co", "personal credit") == "fintech"
    assert universe.classify_sector("8000", "Care Corp", "health services") == "healthcare"
    assert universe.classify_sector("8200", "Learn Inc", "educational services") == "edtech"
    # generic tech SIC: keywords pick the domain, saas is the fallback claimer
    assert universe.classify_sector("7372", "Payment Software Inc", "prepackaged software") == "fintech"
    assert universe.classify_sector("7372", "Enterprise Software Inc", "prepackaged software") == "saas"
    # 7389 is generic but not claimed by saas -> other when no keywords hit
    assert universe.classify_sector("7389", "Generic Services Co", "services") == "other"
    # healthcare exclude_sic: pure pharma R&D stays out
    assert universe.classify_sector("2834", "Pharma Co", "pharmaceutical preparations") == "other"


def test_saas_never_keyword_claims_generic_sic():
    """Reviewer counterexample: saas has generic_keyword_match: false, so a
    generic SIC outside saas.sic must NOT become saas via keywords — old
    enum-era code only keyword-matched fintech/healthcare/edtech here."""
    from pipeline import universe

    assert universe.classify_sector(
        "7389", "CloudTech Software Solutions", "provides software services"
    ) == "other"


def test_generic_keyword_match_defaults_to_true(monkeypatch):
    """Custom packs stay fully data-driven: without the key, a sector may
    claim a generic SIC via keywords."""
    from pipeline import universe

    monkeypatch.setitem(
        universe.SETTINGS, "universe",
        {
            "generic_tech_sic": ["7389"],
            "sectors": {"widgets": {"sic": [], "keywords": ["widget"]}},
        },
    )
    assert universe.classify_sector("7389", "Acme Widget Co", "widget platform") == "widgets"
