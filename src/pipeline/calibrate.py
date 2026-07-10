"""Outcome → weight calibration report (v3.2, 2026-07-10).

Closes the loop the analytics stage opened: correlates outcome events
(sent/replied/positive_reply/meeting) with the SIGNALS present at each
message's company — plus the angle_family/service dimensions analytics
already tracks — and reports which signals over/under-perform the baseline,
with a suggested weight direction.

REPORT-ONLY by design: weights and thresholds in config/settings.yaml are
human decisions. This module never writes settings; it prints evidence.
Suggestions appear only when BOTH the signal row and the baseline clear the
min-sends bar — thin samples produce misleading rates.
"""
from __future__ import annotations

from collections import defaultdict

from pipeline import db
from pipeline.analytics import attribution_table, outcome_funnel
from pipeline.config import SETTINGS

# a signal must beat/lag the baseline positive-reply rate by this much before
# the report suggests touching its weight — jitter-sized differences hold
RAISE_RATIO = 1.3
LOWER_RATIO = 0.7


def _min_sends() -> int:
    a = SETTINGS.get("analytics", {})
    return int(a.get("min_sends_for_calibration", 25))


def signal_outcome_table(
    messages_by_cik: dict[int, list[dict]],
    events: list[dict],
    signals_by_cik: dict[int, list[dict]],
    min_sends: int,
) -> dict[str, dict]:
    """Per signal type: outcome funnel over the messages sent to companies
    carrying that signal. A message counts toward every signal its company
    has (signals co-occur; this is attribution, not isolation)."""
    msg_ids_with_signal: dict[str, set[int]] = defaultdict(set)
    for cik, msgs in messages_by_cik.items():
        types = {s.get("type") for s in signals_by_cik.get(int(cik), []) if s.get("type")}
        for t in types:
            msg_ids_with_signal[t].update(m["id"] for m in msgs)
    table: dict[str, dict] = {}
    for t in sorted(msg_ids_with_signal):
        ids = msg_ids_with_signal[t]
        table[t] = outcome_funnel([e for e in events if e["message_id"] in ids], min_sends)
    return table


def weight_suggestions(table: dict[str, dict], baseline: dict) -> list[dict]:
    """Directional advice per signal vs the baseline positive-reply rate.
    Empty when the baseline itself is insufficient — no advice from noise."""
    if baseline.get("insufficient"):
        return []
    base_rate = baseline.get("positive_reply_rate") or baseline.get("reply_rate") or 0.0
    out: list[dict] = []
    for t, funnel in sorted(table.items()):
        if funnel.get("insufficient"):
            out.append({"signal": t, "verdict": "insufficient data",
                        "n_sent": funnel.get("n_sent", 0), "ratio": None})
            continue
        rate = funnel.get("positive_reply_rate") or funnel.get("reply_rate") or 0.0
        ratio = (rate / base_rate) if base_rate else None
        if ratio is None:
            verdict = "no baseline rate"
        elif ratio >= RAISE_RATIO:
            verdict = "consider raising weight"
        elif ratio <= LOWER_RATIO:
            verdict = "consider lowering weight"
        else:
            verdict = "hold"
        out.append({"signal": t, "verdict": verdict, "n_sent": funnel["n_sent"],
                    "ratio": round(ratio, 2) if ratio is not None else None})
    return out


def render(console) -> None:
    """`pipeline calibrate` entry point."""
    min_sends = _min_sends()
    try:
        events = db.all_message_events()
    except Exception:
        console.print("[yellow]message_events table unavailable — apply sql/schema.sql first.[/yellow]")
        return
    if not events:
        console.print(
            "No outcome events yet — log sends/replies with `pipeline outcome` "
            "and re-run once sends accumulate."
        )
        return

    messages_by_cik = db.all_messages()
    signals_by_cik = db.all_signals()
    baseline = outcome_funnel(events, min_sends)

    if baseline.get("insufficient"):
        console.print(
            f"Baseline insufficient: {baseline['n_sent']} sent < {min_sends} required "
            f"(analytics.min_sends_for_calibration) — rates from thin samples mislead; "
            "no suggestions until more sends land."
        )
        return

    console.print(
        f"[bold]Baseline[/bold] ({baseline['n_sent']} sent): "
        f"reply {baseline['reply_rate']:.1%} | positive {baseline['positive_reply_rate']:.1%} "
        f"| meeting {baseline['meeting_rate']:.1%}"
    )

    table = signal_outcome_table(messages_by_cik, events, signals_by_cik, min_sends)
    console.print("\n[bold]Per-signal outcomes[/bold] (message counts toward every signal its company carries):")
    for s in weight_suggestions(table, baseline):
        f = table[s["signal"]]
        rates = ("—" if f.get("insufficient")
                 else f"reply {f['reply_rate']:.1%} / positive {f['positive_reply_rate']:.1%}")
        ratio = f" ({s['ratio']}x baseline)" if s["ratio"] is not None else ""
        console.print(f"  {s['signal']:>3}  sent {s['n_sent']:>4}  {rates}{ratio}  -> {s['verdict']}")

    flat_messages = [m for msgs in messages_by_cik.values() for m in msgs]
    for dim in ("angle_family", "service", "archetype"):
        console.print(f"\n[bold]By {dim}[/bold]:")
        for key, f in sorted(attribution_table(flat_messages, events, dim, min_sends).items()):
            if f.get("insufficient"):
                console.print(f"  {key}: insufficient ({f['n_sent']} sent)")
            else:
                console.print(
                    f"  {key}: sent {f['n_sent']}, reply {f['reply_rate']:.1%}, "
                    f"positive {f['positive_reply_rate']:.1%}, meeting {f['meeting_rate']:.1%}"
                )

    console.print(
        "\n[dim]Report only — weights live in config/settings.yaml (scoring.weights) "
        "and stay a human decision. Re-run after each outcome batch.[/dim]"
    )
