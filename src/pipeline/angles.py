"""Outreach-angle logic: freshness, strength, fingerprints, deep-tier selection.

An angle is a dated, structured outreach event (funding / leadership / ai_move)
stored in the `angles` table — see docs/SIGNALS.md and the 2026-07-06
outreach-angles spec. Signals feed scoring; angles feed outreach copy.
"""
from __future__ import annotations

from datetime import date, datetime

from pipeline.config import SETTINGS

DEFAULT_WINDOWS = {"funding": 365, "leadership": 365, "ai_move": 270}


def freshness_days(family: str) -> int:
    cfg = SETTINGS.get("angles", {}).get("freshness_days", {})
    return int(cfg.get(family, DEFAULT_WINDOWS.get(family, 365)))


def _age_days(event_date, today: date | None = None) -> int | None:
    if event_date is None:
        return None
    if isinstance(event_date, str):
        try:
            event_date = (
                datetime.fromisoformat(event_date).date()
                if "T" in event_date else date.fromisoformat(event_date[:10])
            )
        except ValueError:
            return None
    return max(((today or date.today()) - event_date).days, 0)


def is_fresh(family: str, event_date, today: date | None = None) -> bool:
    age = _age_days(event_date, today)
    return age is not None and age <= freshness_days(family)


def compute_strength(
    family: str, event_date, has_quote: bool, has_url: bool, today: date | None = None
) -> float:
    """Recency decay x evidence quality. Stale -> 0."""
    cfg = SETTINGS.get("angles", {}).get("strength", {})
    full = int(cfg.get("full_days", 90))
    floor = float(cfg.get("floor", 0.25))
    window = freshness_days(family)
    age = _age_days(event_date, today)
    if age is None or age > window:
        return 0.0
    if age <= full:
        recency = 1.0
    else:
        frac = (age - full) / max(window - full, 1)
        recency = 1.0 - frac * (1.0 - floor)
    quality = 1.0 if (has_quote and has_url) else 0.7 if has_url else 0.4
    return round(recency * quality, 3)


def make_fingerprint(family: str, *parts) -> str:
    norm = [str(p).strip().lower().replace(" ", "-") for p in parts if p is not None]
    return ":".join([family, *norm])


def slim(row: dict, today: date | None = None) -> dict:
    """Packet-shaped angle: what the scorer needs, nothing else."""
    keys = ("fingerprint", "family", "headline", "details", "event_date",
            "strength", "evidence_url", "evidence_quote")
    out = {k: row.get(k) for k in keys}
    out["age_days"] = _age_days(row.get("event_date"), today)
    return out


def select_deep_targets(candidates: list[dict], totals: dict[int, float], cap: int) -> list[dict]:
    """Deep-tier selection: highest latest score first, capped."""
    ranked = sorted(candidates, key=lambda c: totals.get(int(c["cik"]), 0), reverse=True)
    return ranked[: max(cap, 0)]
