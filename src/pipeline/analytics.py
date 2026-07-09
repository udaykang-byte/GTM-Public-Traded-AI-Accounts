"""Outcome analytics foundation (v3 phase 4): funnel conversion,
time-in-stage, and message-outcome attribution (sent -> replied ->
positive_reply -> meeting), by archetype / angle_family / service.

Pure computation lives in the top-level functions below — no DB calls, unit
testable on fixture data. `render()` is the only function that talks to
db.py; it's what `pipeline status --analytics` calls (see cli.status()).
Every DB read is guarded the same way cli.status() guards tier_counts(): an
unmigrated database (missing table/column) degrades to a one-line message
instead of a traceback, both for a bare `status_changed_at` column-not-found
and a `message_events` table-not-found.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from pipeline import db
from pipeline.config import SETTINGS

STATUS_ORDER = ["new", "enriched", "scored", "qualified", "disqualified", "contacts_found"]
OUTCOME_EVENTS = ["sent", "replied", "positive_reply", "meeting"]
ATTRIBUTION_DIMENSIONS = [("archetype", "Archetype"), ("angle_family", "Angle family"), ("service", "Service")]


def _safe_div(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _is_unmigrated(exc: Exception) -> bool:
    """Same degrade signal cli.status() uses for tier_counts(): PostgREST's
    schema-cache-miss code, or a bare 'column/relation does not exist'."""
    return "does not exist" in str(exc) or "PGRST205" in str(exc)


# ---------- company funnel ----------

def funnel_conversion(counts: dict, order: list[str] = STATUS_ORDER) -> list[dict]:
    """Snapshot ratio of each stage's current count to the previous stage's.
    This is NOT a cohort/ever-reached conversion rate — statuses aren't
    strictly sequential (e.g. ingest's L1 prescreen can write 'disqualified'
    straight from 'new', bypassing 'enriched'/'scored') — so treat this as a
    rough bottleneck signal, not a precise funnel."""
    rows = []
    prev_count = None
    for s in order:
        n = counts.get(s, 0)
        rate = _safe_div(n, prev_count) if prev_count is not None else None
        rows.append({"status": s, "count": n, "rate_vs_prev": rate})
        prev_count = n
    return rows


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def avg_time_in_stage(companies: list[dict], now: datetime | None = None) -> dict[str, dict]:
    """companies: rows with 'status' + 'status_changed_at' (db.get_companies()
    output). Rows with no status_changed_at (shouldn't happen post-migration
    backfill, but tolerate it) are skipped rather than crashing. Returns
    {status: {"n": count, "avg_days": float}} — callers should label this
    "approximate" for any pre-v3 row, since status_changed_at was backfilled
    from updated_at there (which moves on ANY column update, not just a
    status change)."""
    now = now or datetime.now(timezone.utc)
    by_status: dict[str, list[float]] = defaultdict(list)
    for c in companies:
        raw = c.get("status_changed_at")
        if not raw:
            continue
        days = max((now - _parse_dt(raw)).total_seconds() / 86400, 0.0)
        by_status[c.get("status") or ""].append(days)
    return {status: {"n": len(days), "avg_days": sum(days) / len(days)} for status, days in by_status.items()}


# ---------- outcome funnel + benchmark bands ----------

def benchmark_band(rate: float, bands: dict) -> str:
    """rate vs settings analytics.benchmarks.positive_reply_rate
    {poor, avg, good} (ascending lower bounds) -> 'good' | 'avg' | 'poor' |
    'below poor'."""
    if rate >= bands.get("good", float("inf")):
        return "good"
    if rate >= bands.get("avg", float("inf")):
        return "avg"
    if rate >= bands.get("poor", float("inf")):
        return "poor"
    return "below poor"


def outcome_funnel(events: list[dict], min_sends: int) -> dict:
    """Aggregate message_events rows into sent/replied/positive_reply/meeting
    counts (DISTINCT message_id per event type — a duplicate/re-imported
    event for the same message must not double-count) + rates over sends.
    Returns {"insufficient": True, ...} without computing rates at all when
    n_sent < min_sends — small samples produce misleading rates."""
    by_event: dict[str, set] = defaultdict(set)
    for e in events:
        by_event[e["event"]].add(e["message_id"])
    n_sent = len(by_event.get("sent", set()))
    if n_sent < min_sends:
        return {"insufficient": True, "min_sends": min_sends, "n_sent": n_sent}
    n_replied = len(by_event.get("replied", set()))
    n_positive = len(by_event.get("positive_reply", set()))
    n_meeting = len(by_event.get("meeting", set()))
    return {
        "insufficient": False,
        "n_sent": n_sent,
        "n_replied": n_replied,
        "n_positive_reply": n_positive,
        "n_meeting": n_meeting,
        "reply_rate": _safe_div(n_replied, n_sent),
        "positive_reply_rate": _safe_div(n_positive, n_sent),
        "meeting_rate": _safe_div(n_meeting, n_sent),
    }


def attribution_table(messages: list[dict], events: list[dict], dimension: str, min_sends: int) -> dict[str, dict]:
    """Group messages by `dimension` (e.g. 'archetype') and run
    outcome_funnel on each group's own events. Every observed value is kept
    in the output (even below min_sends, flagged insufficient) so the
    caller renders a stable table rather than silently dropping thin rows."""
    groups: dict[str, list[int]] = defaultdict(list)
    for m in messages:
        groups[m.get(dimension) or "unknown"].append(m["id"])
    result: dict[str, dict] = {}
    for key, msg_ids in groups.items():
        id_set = set(msg_ids)
        group_events = [e for e in events if e["message_id"] in id_set]
        result[key] = outcome_funnel(group_events, min_sends)
    return result


# ---------- render(): the `pipeline status --analytics` entry point ----------

def render(console) -> None:
    from rich.table import Table

    try:
        companies = db.get_companies()
    except Exception as exc:
        if _is_unmigrated(exc):
            console.print(
                "[dim]Analytics needs the v3 schema (companies.status_changed_at) — "
                "run `uv run python -m pipeline apply-schema`.[/dim]"
            )
            return
        raise

    console.print("\n[bold]Outcome analytics[/bold] (v3 phase 4)")

    counts = Counter(c.get("status") for c in companies)
    funnel_table = Table(title="Funnel snapshot (rate vs previous stage — approximate, not cohort-tracked)")
    funnel_table.add_column("status")
    funnel_table.add_column("count", justify="right")
    funnel_table.add_column("rate vs prev", justify="right")
    for row in funnel_conversion(counts, STATUS_ORDER):
        rate = "—" if row["rate_vs_prev"] is None else f"{row['rate_vs_prev']:.0%}"
        funnel_table.add_row(row["status"], str(row["count"]), rate)
    console.print(funnel_table)

    time_rows = avg_time_in_stage(companies)
    if time_rows:
        time_table = Table(title="Avg time-in-stage, days (approximate for rows created before this feature)")
        time_table.add_column("status")
        time_table.add_column("n", justify="right")
        time_table.add_column("avg days", justify="right")
        for status in STATUS_ORDER:
            r = time_rows.get(status)
            if r:
                time_table.add_row(status, str(r["n"]), f"{r['avg_days']:.1f}")
        console.print(time_table)
    elif not companies:
        console.print("[dim]time-in-stage: insufficient data (no companies)[/dim]")
    else:
        console.print(
            "[dim]time-in-stage: insufficient data (companies.status_changed_at not "
            "populated yet — run `apply-schema`)[/dim]"
        )

    try:
        msg_by_cik = db.all_messages()
        events = db.all_message_events()
    except Exception as exc:
        if _is_unmigrated(exc):
            console.print(
                "[dim]Message-outcome analytics need the v3 phase-4 schema "
                "(message_events, widened messages.status) — run `apply-schema`.[/dim]"
            )
            return
        raise

    messages = [m for msgs in msg_by_cik.values() for m in msgs]
    an = SETTINGS.get("analytics", {})
    min_sends = int(an.get("min_sends_for_attribution", 10))
    bands = an.get("benchmarks", {}).get("positive_reply_rate", {})

    funnel = outcome_funnel(events, min_sends)
    if funnel["insufficient"]:
        console.print(
            f"[dim]sent -> replied -> positive_reply -> meeting: "
            f"insufficient data (<{min_sends} sent, have {funnel['n_sent']})[/dim]"
        )
    else:
        band = benchmark_band(funnel["positive_reply_rate"], bands) if bands else None
        band_note = f" [north star: positive reply rate — benchmark band: {band}]" if band else ""
        out_table = Table(title=f"Outcome funnel{band_note}")
        out_table.add_column("stage")
        out_table.add_column("count", justify="right")
        out_table.add_column("rate of sent", justify="right")
        out_table.add_row("sent", str(funnel["n_sent"]), "—")
        out_table.add_row("replied", str(funnel["n_replied"]), f"{funnel['reply_rate']:.1%}")
        out_table.add_row("positive_reply", str(funnel["n_positive_reply"]), f"{funnel['positive_reply_rate']:.1%}")
        out_table.add_row("meeting", str(funnel["n_meeting"]), f"{funnel['meeting_rate']:.1%}")
        console.print(out_table)

    for dim, label in ATTRIBUTION_DIMENSIONS:
        table = attribution_table(messages, events, dim, min_sends)
        if not table:
            console.print(f"[dim]{label} attribution: insufficient data (no messages yet)[/dim]")
            continue
        rt = Table(title=f"{label} attribution")
        for col in (dim, "sent", "reply rate", "positive reply rate", "meeting rate"):
            rt.add_column(col)
        for key, stats in sorted(table.items()):
            if stats["insufficient"]:
                rt.add_row(key, f"insufficient data (<{min_sends} sent, have {stats['n_sent']})", "", "", "")
            else:
                rt.add_row(
                    key, str(stats["n_sent"]),
                    f"{stats['reply_rate']:.1%}", f"{stats['positive_reply_rate']:.1%}",
                    f"{stats['meeting_rate']:.1%}",
                )
        console.print(rt)
