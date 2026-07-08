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
# message generation gets its own dirs so /score and /outreach can be in
# flight at the same time without one commit eating the other's results
MSG_QUEUE_DIR = DATA_DIR / "message_queue"
MSG_RESULTS_DIR = DATA_DIR / "message_results"
MSG_ARCHIVE_DIR = DATA_DIR / "message_archive"

for _d in (DATA_DIR, CACHE_DIR, QUEUE_DIR, RESULTS_DIR, ARCHIVE_DIR, EXPORT_DIR,
           MSG_QUEUE_DIR, MSG_RESULTS_DIR, MSG_ARCHIVE_DIR):
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


def normalize_pg_dsn(dsn: str) -> str:
    """Tolerate raw special characters (e.g. '@') in the password part of a
    postgres URI — common with Supabase-generated passwords."""
    import re
    from urllib.parse import quote

    m = re.match(r"^(postgres(?:ql)?://)(.*)@([^@]+)$", dsn.strip())
    if not m:
        return dsn.strip()
    scheme, userinfo, hostpart = m.groups()
    if ":" not in userinfo:
        return dsn.strip()
    user, password = userinfo.split(":", 1)
    return f"{scheme}{user}:{quote(password, safe='')}@{hostpart}"
