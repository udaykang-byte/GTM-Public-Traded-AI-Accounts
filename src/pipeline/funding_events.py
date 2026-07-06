"""EDGAR funding-event collector -> outreach angles (family 'funding').

Free tier: filing-index metadata decides what to download; amount and
use-of-proceeds extraction is best-effort text parsing (nullable — the filing
itself is the evidence). Angle semantics: pipeline/angles.py, docs/SIGNALS.md.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from pipeline import angles as angles_mod
from pipeline.edgar_signals import _filing_items, _filing_url, _init_edgar
from pipeline.models import Angle

OFFERING_FORMS = ("424B1", "424B2", "424B3", "424B4", "424B5")
SHELF_FORMS = ("S-3", "S-3ASR")
DEBT_PHRASES = ["credit agreement", "loan and security agreement", "term loan", "revolving credit"]
PIPE_PHRASES = ["securities purchase agreement", "private placement", "note purchase agreement"]
ATM_PHRASE = "at-the-market"

AMOUNT_KEYWORDS = ["gross proceeds", "aggregate offering price", "principal amount", "aggregate purchase price"]
AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion)?", re.IGNORECASE)
PROCEEDS_RE = re.compile(r"(?i)(?:intend to )?use\s+the\s+net\s+proceeds[^.]{0,400}\.")

INSTRUMENT_LABEL = {
    "shelf": "Shelf registration", "atm": "ATM program", "follow_on": "Offering priced",
    "pipe": "PIPE/private placement", "debt": "Debt facility",
}


def _extract_amount(text: str) -> float | None:
    """Largest dollar figure within 300 chars of a proceeds keyword."""
    if not text:
        return None
    lower = text.lower()
    best: float | None = None
    for kw in AMOUNT_KEYWORDS:
        for m in re.finditer(re.escape(kw), lower):
            window = text[max(0, m.start() - 300): m.start() + 300]
            for am in AMOUNT_RE.finditer(window):
                val = float(am.group(1).replace(",", ""))
                unit = (am.group(2) or "").lower()
                if unit == "million":
                    val *= 1e6
                elif unit == "billion":
                    val *= 1e9
                if val < 100_000:  # "$0.001 par value" noise
                    continue
                if best is None or val > best:
                    best = val
    return best


def _use_of_proceeds(text: str) -> str | None:
    m = PROCEEDS_RE.search(text or "")
    return " ".join(m.group(0).split())[:350] if m else None


def _accession(filing) -> str:
    return str(getattr(filing, "accession_no", "") or getattr(filing, "accession_number", ""))


def _angle(company: dict, filing, form: str, instrument: str, text: str) -> Angle:
    cik = int(company["cik"])
    fdate = filing.filing_date
    amount = _extract_amount(text)
    quote = _use_of_proceeds(text)
    url = _filing_url(cik, filing)
    amt = f" ~${amount / 1e6:.0f}M" if amount else ""
    return Angle(
        company_cik=cik, family="funding",
        headline=f"{INSTRUMENT_LABEL[instrument]}{amt} — {form} filed {fdate}",
        details={
            "amount_usd": amount, "instrument": instrument, "announced": fdate,
            "use_of_proceeds": quote, "filing_type": form,
        },
        evidence_url=url, evidence_quote=quote, event_date=fdate, source="edgar",
        strength=angles_mod.compute_strength("funding", fdate, bool(quote), bool(url)),
        fingerprint=angles_mod.make_fingerprint("funding", _accession(filing)),
    )


def funding_angles(edgar_company, company: dict) -> list[Angle]:
    cutoff = date.today() - timedelta(days=angles_mod.freshness_days("funding"))
    out: list[Angle] = []
    seen: set[str] = set()

    for form in OFFERING_FORMS + SHELF_FORMS:
        try:
            filings = list(edgar_company.get_filings(form=form))
        except Exception:
            continue
        for filing in filings:
            fdate = getattr(filing, "filing_date", None)
            if fdate is None or fdate < cutoff:
                break  # newest-first
            try:
                text = filing.text() or ""
            except Exception:
                text = ""
            if form in SHELF_FORMS:
                instrument = "shelf"
            else:
                instrument = "atm" if ATM_PHRASE in text.lower() else "follow_on"
            a = _angle(company, filing, form, instrument, text)
            if a.fingerprint not in seen:
                seen.add(a.fingerprint)
                out.append(a)

    try:
        eightks = list(edgar_company.get_filings(form="8-K"))
    except Exception:
        eightks = []
    for filing in eightks:
        fdate = getattr(filing, "filing_date", None)
        if fdate is None or fdate < cutoff:
            break
        items = _filing_items(filing)
        if not ({"3.02", "1.01"} & items):
            continue
        try:
            text = filing.text() or ""
        except Exception:
            continue
        lower = text.lower()
        if "3.02" in items:
            instrument = "pipe"
        elif any(p in lower for p in DEBT_PHRASES):
            instrument = "debt"
        elif any(p in lower for p in PIPE_PHRASES):
            instrument = "pipe"
        else:
            continue  # Item 1.01 with no financing language — not a funding event
        a = _angle(company, filing, "8-K " + "/".join(sorted({"3.02", "1.01"} & items)), instrument, text)
        if a.fingerprint not in seen:
            seen.add(a.fingerprint)
            out.append(a)
    return out


def collect(company: dict) -> tuple[list[Angle], list[str]]:
    """Run the funding collector for one company. Mirrors edgar_signals.collect."""
    _init_edgar()
    from edgar import Company as EdgarCompany

    try:
        ec = EdgarCompany(int(company["cik"]))
    except Exception as exc:
        return [], [f"edgar company lookup failed: {exc}"]
    try:
        return funding_angles(ec, company), []
    except Exception as exc:
        return [], [f"funding: {type(exc).__name__}: {exc}"]
