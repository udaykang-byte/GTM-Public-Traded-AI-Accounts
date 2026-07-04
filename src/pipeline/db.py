"""Supabase access layer. All DB reads/writes in the pipeline go through here."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from supabase import Client, create_client

from pipeline.config import require_env
from pipeline.models import Company, Contact, Signal, Status

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            require_env("SUPABASE_URL"), require_env("SUPABASE_SERVICE_ROLE_KEY")
        )
    return _client


# ---------- companies ----------

def upsert_companies(companies: list[Company]) -> int:
    if not companies:
        return 0
    rows = [c.model_dump(mode="json") for c in companies]
    client().table("companies").upsert(rows, on_conflict="cik").execute()
    return len(rows)


def get_companies(
    status: Status | str | None = None,
    tickers: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    q = client().table("companies").select("*")
    if status is not None:
        q = q.eq("status", status.value if isinstance(status, Status) else status)
    if tickers:
        q = q.in_("ticker", [t.upper() for t in tickers])
    q = q.order("cik")
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def get_company_by_ticker(ticker: str) -> dict | None:
    rows = (
        client().table("companies").select("*").eq("ticker", ticker.upper()).limit(1).execute().data
    )
    return rows[0] if rows else None


def existing_ciks() -> set[int]:
    rows = client().table("companies").select("cik").execute().data or []
    return {r["cik"] for r in rows}


def set_status(cik: int, status: Status | str, profile: str | None = None) -> None:
    patch: dict = {"status": status.value if isinstance(status, Status) else status}
    if profile is not None:
        patch["profile"] = profile
    client().table("companies").update(patch).eq("cik", cik).execute()


def status_counts() -> Counter:
    rows = client().table("companies").select("status").execute().data or []
    return Counter(r["status"] for r in rows)


# ---------- signals ----------

def replace_signals(cik: int, source: str, signals: list[Signal]) -> int:
    """Idempotent re-enrich: wipe this company's signals from `source`, insert fresh."""
    client().table("signals").delete().eq("company_cik", cik).eq("source", source).execute()
    if not signals:
        return 0
    rows = [s.model_dump(mode="json") for s in signals]
    client().table("signals").insert(rows).execute()
    return len(rows)


def get_signals(cik: int) -> list[dict]:
    return (
        client().table("signals").select("*").eq("company_cik", cik).order("weight", desc=True)
        .execute().data or []
    )


# ---------- scores ----------

def insert_score(row: dict) -> None:
    client().table("scores").insert(row).execute()


def latest_score(cik: int) -> dict | None:
    rows = (
        client().table("scores").select("*").eq("company_cik", cik)
        .order("created_at", desc=True).limit(1).execute().data
    )
    return rows[0] if rows else None


def recent_qualified(limit: int = 10) -> list[dict]:
    rows = (
        client().table("companies").select("cik,ticker,name,sector_bucket,profile,market_cap")
        .in_("status", ["qualified", "contacts_found"]).order("updated_at", desc=True)
        .limit(limit).execute().data or []
    )
    for r in rows:
        s = latest_score(r["cik"])
        r["total"] = s["total"] if s else None
        r["service_fit"] = (s or {}).get("service_fit") or []
    return rows


# ---------- contacts ----------

def insert_contacts(contacts: list[Contact]) -> int:
    if not contacts:
        return 0
    rows = [c.model_dump(mode="json") for c in contacts]
    client().table("contacts").insert(rows).execute()
    return len(rows)


def get_contacts(cik: int) -> list[dict]:
    return client().table("contacts").select("*").eq("company_cik", cik).execute().data or []


# ---------- runs ----------

def start_run(stage: str) -> int | None:
    res = client().table("runs").insert({"stage": stage}).execute()
    try:
        return res.data[0]["id"]
    except Exception:
        return None


def finish_run(run_id: int | None, stats: dict) -> None:
    if run_id is None:
        return
    client().table("runs").update(
        {"finished_at": datetime.now(timezone.utc).isoformat(), "stats": json.loads(json.dumps(stats, default=str))}
    ).eq("id", run_id).execute()


def recent_runs(limit: int = 5) -> list[dict]:
    return (
        client().table("runs").select("*").order("started_at", desc=True).limit(limit)
        .execute().data or []
    )
