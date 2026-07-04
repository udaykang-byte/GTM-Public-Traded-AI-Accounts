"""Central config: .env + config/*.yaml + project paths."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
QUEUE_DIR = DATA_DIR / "scoring_queue"
RESULTS_DIR = DATA_DIR / "scoring_results"
ARCHIVE_DIR = DATA_DIR / "scoring_archive"
EXPORT_DIR = DATA_DIR / "exports"

for _d in (DATA_DIR, CACHE_DIR, QUEUE_DIR, RESULTS_DIR, ARCHIVE_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_yaml(name: str) -> dict:
    path = PROJECT_ROOT / "config" / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


SETTINGS: dict = _load_yaml("settings.yaml")
SERVICES: dict = _load_yaml("services.yaml").get("services", {})


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or default


def require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f"Missing {key} — add it to .env (see .env.example)")
    return value


def edgar_identity() -> str:
    return require_env("EDGAR_IDENTITY")
