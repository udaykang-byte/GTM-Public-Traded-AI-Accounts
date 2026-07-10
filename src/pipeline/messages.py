"""Outreach message generation: packet handoff for LLM copywriting + QA gate.

v2 sub-project 2 flow (no LLM API cost):
  messages --prepare  ->  data/message_queue/<TICKER>__<contact-slug>.json packets
  /outreach skill     ->  Claude Code Haiku subagents write data/message_results/<same-name>.json
  messages --commit   ->  validate + qa_check, upsert `messages` rows, archive

One packet per CONTACT (role-aware copy). The copywriter framework
(config/outbound_copywriter.md), services catalog, and output schema live in
data/message_queue/_shared.json; a copywriter needs the packet plus that file.
Each packet also carries a `persona` block (pains/language/committee_role/
seniority) from config/personas.yaml when the contact's role_bucket or title
resolves to one (see pipeline.people.match_persona) — null otherwise; older
packets without the key still commit fine. Generation only — no sending, no
CRM push (sub-project 3).
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from pydantic import ValidationError

from pipeline import angles as angles_mod
from pipeline import db
from pipeline import people as people_mod
from pipeline.config import (
    MSG_ARCHIVE_DIR,
    MSG_QUEUE_DIR,
    MSG_RESULTS_DIR,
    PERSONAS,
    SERVICES,
    SETTINGS,
    profile_file,
)
from pipeline.db import order_by_tier_priority
from pipeline.models import MessageSequence

# Fallback banned list, used only when the active pack's settings.yaml has no
# messages.banned_words key. The canonical, editable list now lives in
# config/settings.yaml (messages.banned_words) — SEE ALSO
# config/outbound_copywriter.md's "Voice" section, which documents the same
# words for humans; change all three together. Single words match inflections
# (leverage -> leveraging); phrases match with flexible whitespace/hyphens.
_DEFAULT_BANNED_WORDS = [
    "leverage", "utilize", "streamline", "comprehensive", "robust", "innovative",
    "cutting-edge", "game-changing", "revolutionary", "disruptive", "synergy",
    "best-in-class", "world-class", "next-generation", "solution", "excited to",
    "passionate about", "thrilled", "reimagine", "transform", "empower",
    "elevate", "optimize", "drive results", "thought leader",
]

MEETING_ASK_RE = re.compile(
    r"book a call|hop on a call|quick call|minute call|calendly|calendar"
    r"|schedule a|15 minutes|30 minutes|a demo", re.IGNORECASE)
LINK_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\[[^\]]{1,30}\]")
SUBJECT_CHARS_RE = re.compile(r"[^a-z0-9 '&+?$.-]")
# batch-1 lesson (2026-07-07): quoting signals back at the prospect reads as
# surveillance — filing forms and day-precise dates are hard failures; the
# analyst-voice constructions that came with them are warnings
FILING_FORM_RE = re.compile(r"\b(8-K|10-K|10-Q|424B\d?|DEF 14A|S-1|S-3|S-4|13D|13G)\b", re.IGNORECASE)
# batch-2 lesson (2026-07-10): instrument names are filing language too — a
# "shelf registration"/"PIPE placement" opener reads as compliance surveillance
# exactly like a form name (copywriter doc: "the human version of it —
# 'Congrats on the raise'")
INSTRUMENT_RE = re.compile(
    r"\b(shelf registration|shelf offering|private placement|PIPE placement"
    r"|registered direct|at-the-market offering)\b|\bPIPE\b", re.IGNORECASE)
# month-name event citations ("from November") aren't day-precise, so warning
# tier — but they pull copy back toward filing-surveillance voice
MONTH_CITE_RE = re.compile(
    r"\b(in|from|last|since|back in) (january|february|march|april|may|june"
    r"|july|august|september|october|november|december)\b", re.IGNORECASE)
DATE_CITE_RE = re.compile(
    r"\b20\d{2}-\d{2}-\d{2}\b"
    r"|\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\.? \d{1,2}(st|nd|rd|th)?\b", re.IGNORECASE)
ANALYST_VOICE_RE = re.compile(
    r"\bthat kind of\b|\b(usually|typically|often) (comes with|means|signals)\b"
    r"|\bexecution window\b|\bfirst.100.days\b|\bcapability gap\b|\bgtm velocity\b",
    re.IGNORECASE)
FILING_SPEAK_RE = re.compile(r"\b(earnings call|press release|proxy statement|filed|filing)\b", re.IGNORECASE)

HARD_RULES = [
    "NEVER invent metrics, client names, or case studies — martechs.io has no "
    "citable proof points yet; archetypes case_study and benchmark are FORBIDDEN.",
    "TRANSLATE, DON'T CITE: packet signals are diagnosis, not copy. Follow the "
    "Signal -> Pain -> Fix table in copywriter_framework. The trigger event gets "
    "ONE humanized clause ('congrats on the raise'); NEVER filing form names "
    "(8-K, 10-K, 424B5...), NEVER calendar dates ('May 6th', '2026-03-16'), "
    "never 'announced on'/'filed' language, never quotes lifted from filings — "
    "these are automatic QA failures.",
    "VALUE PROP REQUIRED: every email states plainly what martechs.io does for "
    "their situation and what changes for them (use the per-service value-prop "
    "lines in copywriter_framework). An email that is only observations plus a "
    "question does not ship.",
    "PATTERN PROOF in steps 1-2: how companies in their exact spot get stuck "
    "and what fixes it — unnamed patterns, no invented clients or numbers. This "
    "is our social proof.",
    "Facts you rely on must come from the packet — but expressed as the pain "
    "they imply, not as citations. Lead step 1 with the pain implied by the "
    "packet's primary_angle_fingerprint angle; steps 2 and 3 must each bring "
    "something NEW (a different angle's pain or the service pitch) — never "
    "'just bumping'.",
    "PERSONA: when the packet has a persona block, use its pains and "
    "language.their_words as raw material for how this contact would "
    "describe their own situation — do not invent pains beyond what the "
    "packet (angles, verdict) and persona together support. If persona is "
    "null, write from the packet's angles and verdict alone.",
    "Fully rendered plain text: real first name, real company name; no "
    "{{merge_variables}}, no [bracketed placeholders], no signature block — "
    "sign off with just 'Uday'.",
    "Premium positioning: soft, no-oriented CTAs; no meeting ask before step 4; "
    "no links in any step; never 'reply to this email' (channel-neutral).",
    "Subject: 3-5 words, all lowercase, about THEM — step 1 only; steps 2-4 "
    "have subject null (same thread).",
    "Step 1 body 60-120 words; steps 2-4 shorter is fine; hard max 150 anywhere; "
    "every sentence its own paragraph; exactly one CTA per step and it is a "
    "question; no banned words.",
    "WRITE LIKE A HUMAN: contractions, plain words, at most 2 em-dashes per "
    "email; never 'that kind of X usually means/comes with' analyst voice; no "
    "consulting jargon (execution window, first-100-days, capability gap, GTM "
    "velocity). Say it the way you'd say it across a table.",
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "contact"


def _cfg() -> dict:
    return SETTINGS.get("messages", {})


# the framework doc stays verbose for humans; what ships to the subagents is
# distilled: <!-- embed:skip --> ... <!-- /embed:skip --> spans (interview
# framing, QA checklist — duplicated by services_catalog/hard_rules/qa_check)
# and all maintainer comments are stripped. The shared file is re-read every
# copywriter turn, so every KB here is paid ~6x per invocation.
_EMBED_SKIP_RE = re.compile(r"<!--\s*embed:skip\s*-->.*?<!--\s*/embed:skip\s*-->", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _distill_framework(text: str) -> str:
    text = _EMBED_SKIP_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _framework_text() -> str:
    path = profile_file(_cfg().get("framework_file", "outbound_copywriter.md"))
    return _distill_framework(path.read_text())


def _pick_angle(score: dict, fresh: list[dict]) -> dict | None:
    """Fallback chain: verdict primary if still fresh -> first fresh ranked ->
    strongest fresh -> None (skip company, never a stale hook)."""
    by_fp = {a["fingerprint"]: a for a in fresh}
    pa = (score.get("primary_angle") or {}).get("fingerprint")
    if pa and pa in by_fp:
        return by_fp[pa]
    for ref in score.get("angle_ranking") or []:
        if ref.get("fingerprint") in by_fp:
            return by_fp[ref["fingerprint"]]
    return fresh[0] if fresh else None


def _angle_summary(slim: dict) -> str:
    """One human-readable line per angle so the copywriter can weigh angles
    at a glance instead of re-deriving them from the structured fields.
    Raw material for diagnosis only — headlines may contain filing language
    the copy itself must never use."""
    age = slim.get("age_days")
    strength = slim.get("strength")
    bits = [b for b in (
        f"{age}d old" if age is not None else None,
        f"strength {strength:.2f}" if strength is not None else None,
    ) if b]
    meta = f" ({', '.join(bits)})" if bits else ""
    return f"{slim.get('family')}: {slim.get('headline')}{meta}"


def _recommended_service(fits: list[dict], role_bucket: str) -> str:
    """First service (priority order) whose target roles include this contact's
    role — the role-aware part of per-contact packets. Consults config/
    personas.yaml's services mapping first (when the active pack has one),
    else the legacy flat people.roles_by_service lookup. Falls back to the
    lead service either way when role_bucket doesn't match anything."""
    ordered = sorted(fits, key=lambda f: f.get("priority", 9))
    rb = (role_bucket or "").strip().lower()
    if rb:
        if PERSONAS:
            defs = people_mod.persona_defs(PERSONAS)
            services_map: dict = PERSONAS.get("services", {})
            persona_keys = {
                k for k, v in defs.items()
                if str(v.get("role_bucket", "")).strip().lower() == rb
            }
            for f in ordered:
                if persona_keys & set(services_map.get(f.get("service", ""), [])):
                    return f["service"]
        else:
            roles_by_service = SETTINGS.get("people", {}).get("roles_by_service", {})
            for f in ordered:
                roles = [r.lower() for r in roles_by_service.get(f.get("service", ""), [])]
                if rb in roles:
                    return f["service"]
    return ordered[0]["service"] if ordered else ""


def prepare(
    limit: int | None = None, ticker: str | None = None,
    force: bool = False, dry_run: bool = False,
) -> tuple[list[str], dict]:
    """Build one packet per (company with a fresh angle) x contact.
    Returns (packet paths, skips) — skips maps reason -> [ticker/contact...].
    dry_run computes the same lists but writes nothing (files or DB)."""
    if ticker:
        row = db.get_company_by_ticker(ticker)
        companies = [row] if row else []
    else:
        companies = db.get_companies(status="contacts_found")

    # fetched once up front (needed to sort) and reused in the loop below —
    # avoids a second db.latest_score round trip per company
    scores_by_cik = {int(c["cik"]): db.latest_score(int(c["cik"])) for c in companies}
    if not ticker:
        priority_by_cik = {cik: (s or {}).get("priority") for cik, s in scores_by_cik.items()}
        companies = order_by_tier_priority(companies, priority_by_cik)

    cap = int(_cfg().get("max_per_run", 40))
    cap = min(limit, cap) if limit else cap

    schema = MessageSequence.model_json_schema()
    shared_path = MSG_QUEUE_DIR / "_shared.json"
    if not dry_run:
        shared_path.write_text(json.dumps({
            "copywriter_framework": _framework_text(),
            "services_catalog": SERVICES,
            "output_schema": schema,
            "sequence_plan": {
                "day_offsets": _cfg().get("day_offsets", [0, 3, 8, 16]),
                "cta_by_step": {"1": "confirm_problem", "2": "offer_deliverable",
                                "3": "micro_commitment", "4": "breakup_options"},
            },
            "hard_rules": HARD_RULES,
            "instructions": (
                "This file is identical for every packet in the queue — read it ONCE. "
                "For each packet: write a 4-step outreach sequence for that ONE contact "
                "following copywriter_framework, sequence_plan, and hard_rules, as JSON "
                "matching output_schema EXACTLY, to the packet's output_path."
            ),
        }, indent=2, default=str))

    angles_by_cik = db.all_angles()
    messages_by_cik = db.all_messages()
    if not dry_run:
        stale_ids = [
            a["id"]
            for rows in angles_by_cik.values()
            for a in rows
            if a.get("id") is not None and a.get("status") == "active"
            and not angles_mod.is_fresh(a["family"], a["event_date"])
        ]
        db.mark_angles_stale(stale_ids)

    written: list[str] = []
    skips: dict[str, list[str]] = {"no_angle": [], "no_score": [], "no_contacts": [],
                                   "no_channel": [], "existing": []}
    for company in companies:
        if len(written) >= cap:
            break
        cik = int(company["cik"])
        score = scores_by_cik.get(cik)
        fits = (score or {}).get("service_fit") or []
        if not score or not fits:
            skips["no_score"].append(company["ticker"])
            continue
        fresh = [a for a in angles_by_cik.get(cik, [])
                 if angles_mod.is_fresh(a["family"], a["event_date"])]
        fresh.sort(key=lambda a: -(a.get("strength") or 0))
        angle = _pick_angle(score, fresh)
        if angle is None:
            skips["no_angle"].append(company["ticker"])
            continue
        contacts = db.get_contacts(cik)
        if not contacts:
            skips["no_contacts"].append(company["ticker"])
            continue

        existing = {
            (m.get("contact_id"), m.get("angle_fingerprint"))
            for m in messages_by_cik.get(cik, [])
        }
        for contact in contacts:
            if len(written) >= cap:
                break
            name = f"{company['ticker']}__{_slug(contact['name'])}"
            if not contact.get("email") and not contact.get("linkedin_url"):
                # unreachable on every channel — a packet would burn a
                # copywriter run on a sequence that can never be sent
                skips["no_channel"].append(name)
                continue
            if not force and (contact.get("id"), angle["fingerprint"]) in existing:
                skips["existing"].append(name)
                continue
            colleagues = [
                {"name": c["name"], "title": c["title"],
                 "role_bucket": c.get("role_bucket") or ""}
                for c in contacts if c.get("id") != contact.get("id")
            ]
            output_path = (MSG_RESULTS_DIR / f"{name}.json").as_posix()
            persona = people_mod.match_persona(
                contact.get("role_bucket") or "", contact.get("title") or "")
            rec_service = _recommended_service(fits, contact.get("role_bucket") or "")
            packet = {
                "ticker": company["ticker"],
                "company": {
                    k: company.get(k)
                    for k in ("cik", "ticker", "name", "exchange", "sector_bucket",
                              "market_cap", "sic_description", "website", "hq_state")
                },
                "contact": {
                    "id": contact.get("id"),
                    "name": contact["name"],
                    "title": contact["title"],
                    "role_bucket": contact.get("role_bucket") or "",
                    "has_email": bool(contact.get("email")),
                    "has_linkedin": bool(contact.get("linkedin_url")),
                },
                "colleagues_also_messaged": colleagues,
                "diversity_note": (
                    f"{len(colleagues)} colleague(s) at {company['name']} receive "
                    f"sequences built on the SAME angle. Write this one through the "
                    f"{contact.get('role_bucket') or contact['title']} lens: a different "
                    "pain, a different subject line, no phrasing reused across colleagues."
                ) if colleagues else None,
                # slim on purpose: scoring `reasoning` is analyst voice the
                # copywriter is forbidden to use (filing forms, dates,
                # instrument names) — shipping it only seeds QA retries; and
                # only the recommended service's fit entry travels, so the QA
                # gate enforces the "use recommended_service" instruction
                "verdict": {
                    "profile": score.get("profile"),
                    "why_now": score.get("why_now"),
                    "service_fit": [f for f in fits if f.get("service") == rec_service],
                    "primary_angle": score.get("primary_angle"),
                    "angle_ranking": score.get("angle_ranking") or [],
                },
                "recommended_service": rec_service,
                "persona": ({
                    "committee_role": persona.get("committee_role"),
                    "seniority": persona.get("seniority"),
                    "pains": persona.get("pains", []),
                    "language": persona.get("language", {}),
                } if persona else None),
                "primary_angle_fingerprint": angle["fingerprint"],
                "angles": [{**s, "summary": _angle_summary(s)}
                           for s in (angles_mod.slim(a) for a in fresh)],
                "shared_file": shared_path.as_posix(),
                "output_path": output_path,
                "instructions": (
                    f"First read {shared_path.as_posix()} ONCE per batch — framework, "
                    f"catalog, schema, and hard rules shared by every packet. Write the "
                    f"4-step sequence for {contact['name']} ({contact['title']}) — frame "
                    f"the value in {contact.get('role_bucket') or 'their role'} terms, not "
                    f"generic. Use recommended_service. Lead step 1 with the angle at "
                    f"primary_angle_fingerprint. Write JSON matching output_schema "
                    f"EXACTLY to: {output_path} . Do not add fields. Do not wrap in "
                    "markdown."
                ),
            }
            path = MSG_QUEUE_DIR / f"{name}.json"
            if not dry_run:
                path.write_text(json.dumps(packet, indent=2, default=str))
            written.append(str(path))
    return written, skips


def _banned_pattern(entry: str) -> re.Pattern:
    words = [w for w in re.split(r"[-\s]+", entry.lower()) if w]
    parts = []
    for i, w in enumerate(words):
        stem = w[:-1] if (i == len(words) - 1 and w.endswith("e")) else w
        parts.append(re.escape(stem) + (r"\w*" if i == len(words) - 1 else ""))
    return re.compile(r"\b" + r"[-\s]+".join(parts) + r"\b", re.IGNORECASE)


def _banned_words() -> list[str]:
    """Canonical source: config/settings.yaml messages.banned_words. Falls
    back to _DEFAULT_BANNED_WORDS (identical content) when the key is absent
    OR explicitly null (YAML `banned_words:` with no value) — an explicit
    empty list (`banned_words: []`) is a deliberate pack decision to disable
    the gate, not a fallback trigger."""
    cfg = _cfg()
    value = cfg.get("banned_words")
    if value is None:
        return list(_DEFAULT_BANNED_WORDS)
    return list(value)


def _banned_patterns() -> list[tuple[str, re.Pattern]]:
    return [(w, _banned_pattern(w)) for w in _banned_words()]


def _word_count(text: str) -> int:
    return len(text.split())


# crude "nouns" for the personalization heuristic below — alpha tokens of
# length >=4, minus filler words and common corporate-name suffixes
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "over",
    "than", "then", "will", "would", "could", "about", "have", "has", "had",
    "not", "your", "their", "our", "inc", "corp", "corporation", "company",
    "holdings", "group", "ltd", "llc",
}


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) >= 4 and w not in _STOPWORDS}


def _personalization_score(step1_body: str, packet: dict) -> int:
    """0-5 deterministic heuristic on step 1's body only — warning-tier
    signal, never a hard gate (see qa_check). +1 each for: references the
    primary angle's headline words, echoes why_now content, uses a persona
    their_words phrase, names a company-specific fact beyond the ticker,
    opens (first sentence) with a persona/angle pain word."""
    body_lower = (step1_body or "").lower()
    score = 0

    angle_fp = packet.get("primary_angle_fingerprint")
    angle = next((a for a in packet.get("angles", []) if a.get("fingerprint") == angle_fp), None)
    headline_words = _significant_words(angle.get("headline", "")) if angle else set()
    if headline_words and any(w in body_lower for w in headline_words):
        score += 1

    why_now_words = _significant_words((packet.get("verdict") or {}).get("why_now", ""))
    if why_now_words and any(w in body_lower for w in why_now_words):
        score += 1

    persona = packet.get("persona") or {}
    their_words = (persona.get("language") or {}).get("their_words") or []
    if any(str(phrase).strip().lower() in body_lower for phrase in their_words if phrase):
        score += 1

    company_words = _significant_words((packet.get("company") or {}).get("name", ""))
    if company_words and any(w in body_lower for w in company_words):
        score += 1

    pain_words: set[str] = set()
    for p in persona.get("pains") or []:
        pain_words |= _significant_words(p)
    if not pain_words:
        pain_words = headline_words  # no persona matched -> fall back to the angle
    first_sentence = re.split(r"(?<=[.!?])\s+", (step1_body or "").strip())[0].lower() if step1_body else ""
    if pain_words and any(w in first_sentence for w in pain_words):
        score += 1

    return score


def qa_check(seq: MessageSequence, packet: dict) -> tuple[list[str], list[str]]:
    """Deterministic copy QA. Hard errors kill the draft (re-spawn); warnings
    ride along on the row for human review before export."""
    cfg = _cfg()
    hard: list[str] = []
    warn: list[str] = []

    allowed = set(cfg.get("allowed_archetypes",
                          ["observation", "creative_ideas", "referral_ceiling",
                           "problem_solution", "whole_offer"]))
    if seq.archetype.value not in allowed:
        hard.append(f"archetype '{seq.archetype.value}' not allowed (no proof points yet)")

    packet_fps = {a["fingerprint"] for a in packet.get("angles", [])}
    if seq.angle_fingerprint not in packet_fps:
        hard.append(f"angle_fingerprint '{seq.angle_fingerprint}' not in packet angles")
    if seq.ticker != packet["ticker"]:
        hard.append(f"ticker '{seq.ticker}' != packet ticker '{packet['ticker']}'")
    if seq.contact_name != packet["contact"]["name"]:
        hard.append(f"contact_name '{seq.contact_name}' != packet contact '{packet['contact']['name']}'")
    fit_keys = {f.get("service") for f in packet.get("verdict", {}).get("service_fit", [])}
    if seq.service not in fit_keys:
        hard.append(f"service '{seq.service}' not in packet service_fit {sorted(fit_keys)}")

    subject = (seq.steps[0].subject or "").strip()
    if subject != subject.lower():
        hard.append("subject must be all lowercase")
    n_subj = _word_count(subject)
    if not 3 <= n_subj <= 5:
        hard.append(f"subject has {n_subj} words (need 3-5)")
    if SUBJECT_CHARS_RE.search(subject.lower()):
        hard.append("subject has special characters")
    if subject.lower().startswith(("re:", "fwd:")):
        hard.append("subject fakes a thread (re:/fwd:)")

    wc_cfg = cfg.get("word_count", {})
    hard_max = int(wc_cfg.get("hard_max", 150))
    s1_min, s1_max = int(wc_cfg.get("step1_min", 60)), int(wc_cfg.get("step1_max", 120))
    for s in seq.steps:
        n = _word_count(s.body)
        if n > hard_max:
            hard.append(f"step {s.step} body has {n} words (hard max {hard_max})")
        if s.step == 1 and not s1_min <= n <= s1_max:
            warn.append(f"step 1 body has {n} words (want {s1_min}-{s1_max})")
        if s.step > 1 and not 20 <= n <= s1_max:
            warn.append(f"step {s.step} body has {n} words (want 20-{s1_max})")
        if s.step <= 3 and "?" not in s.body:
            hard.append(f"step {s.step} has no question CTA")
        if s.body.count("?") > 2:
            warn.append(f"step {s.step} has {s.body.count('?')} question marks (want <=2)")
        if LINK_RE.search(s.body):
            if s.step == 1:
                hard.append("step 1 body contains a link")
            else:
                warn.append(f"step {s.step} body contains a link")
        text = f"{s.subject or ''} {s.body}"
        if PLACEHOLDER_RE.search(text):
            hard.append(f"step {s.step} has merge variables or [placeholders] — must be fully rendered")
        m = FILING_FORM_RE.search(text)
        if m:
            hard.append(f"step {s.step} cites a filing form ('{m.group(0)}') — translate the signal into a pain, don't quote it")
        m = INSTRUMENT_RE.search(text)
        if m:
            hard.append(f"step {s.step} uses filing instrument language ('{m.group(0)}') — say the human version ('the raise', 'the new capital')")
        m = MONTH_CITE_RE.search(text)
        if m:
            warn.append(f"step {s.step} cites an event month ('{m.group(0)}') — drop the timestamp, keep the moment")
        m = DATE_CITE_RE.search(text)
        if m:
            hard.append(f"step {s.step} cites a calendar date ('{m.group(0)}') — reads as filing surveillance")
        for word, pat in _banned_patterns():
            if pat.search(text):
                hard.append(f"banned word '{word}' in step {s.step}")
        if s.step <= 3 and MEETING_ASK_RE.search(s.body):
            warn.append(f"step {s.step} sounds like a meeting ask (none before step 4)")
        m = ANALYST_VOICE_RE.search(s.body)
        if m:
            warn.append(f"step {s.step} analyst voice ('{m.group(0)}') — say it like a human")
        m = FILING_SPEAK_RE.search(s.body)
        if m:
            warn.append(f"step {s.step} filing-speak ('{m.group(0)}')")
        if "—" in text:
            hard.append(f"step {s.step} contains an em dash — use a period or comma instead")

    for extra in cfg.get("banned_words_extra", []) or []:
        pat = _banned_pattern(extra)
        for s in seq.steps:
            if pat.search(f"{s.subject or ''} {s.body}"):
                hard.append(f"banned word '{extra}' in step {s.step}")

    if seq.steps[3].cta_type.value != "breakup_options":
        warn.append("step 4 cta_type should be breakup_options")

    if not re.search(r"\bUday\s*$", seq.steps[3].body.strip()):
        hard.append("step 4 must end with the 'Uday' sign-off")

    # unverified numbers: warning-tier heuristic — a hard gate would false-
    # positive on benign counts ("2-3 hours"); this reliably surfaces invented
    # "$2.1M pipeline"-class metrics for the human to spot-check before export
    packet_text = json.dumps(packet, default=str)
    all_bodies = " ".join(s.body for s in seq.steps)
    for token in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", all_bodies):
        core = token.strip("$%").replace(",", "")
        if len(core.replace(".", "")) < 2:
            continue  # single digits are noise ("2 things")
        if core not in packet_text:
            warn.append(f"unverified number '{token}' — not found in packet")

    you = len(re.findall(r"\byou\b|\byour\b|\byou're\b|\byours\b", all_bodies, re.IGNORECASE))
    me = len(re.findall(r"\bwe\b|\bour\b|\bus\b|\bme\b|\bi\b|\bi'm\b|\bi'll\b|\bmy\b", all_bodies, re.IGNORECASE))
    ratio_min = float(cfg.get("you_we_ratio_min", 2.0))
    if me and you / me < ratio_min:
        warn.append(f"you:we ratio {you}:{me} below {ratio_min} — too self-focused")

    # personalization heuristic (v3): warning-tier only, never a hard fail —
    # the QA gate stays a floor on copy MECHANICS, not a judge of prose quality
    p_score = _personalization_score(seq.steps[0].body, packet)
    p_min = int(cfg.get("personalization_min", 3))
    if p_score < p_min:
        warn.append(f"personalization {p_score}/5")

    return hard, warn


def commit(run_id: str | None = None) -> dict:
    """Validate + QA-gate results, upsert messages rows, archive. Invalid
    results stay in place so /outreach can re-spawn and overwrite them."""
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    day_offsets = _cfg().get("day_offsets", [0, 3, 8, 16])

    summary: dict = {"written": [], "invalid": [], "orphan": [], "warnings": {}}
    archive = MSG_ARCHIVE_DIR / run_id
    archive.mkdir(parents=True, exist_ok=True)

    shared_path = MSG_QUEUE_DIR / "_shared.json"
    if shared_path.exists():
        shutil.copy2(shared_path, archive / "_shared.json")

    for result_file in sorted(MSG_RESULTS_DIR.glob("*.json")):
        if result_file.name.startswith("_"):
            continue
        stem = result_file.stem
        packet_file = MSG_QUEUE_DIR / result_file.name
        if not packet_file.exists():
            summary["orphan"].append(stem)
            continue
        try:
            seq = MessageSequence.model_validate_json(result_file.read_text())
        except (ValidationError, json.JSONDecodeError) as exc:
            summary["invalid"].append(f"{stem}: {str(exc)[:300]}")
            continue

        packet = json.loads(packet_file.read_text())
        hard, warn = qa_check(seq, packet)
        if hard:
            summary["invalid"].append(f"{stem}: {'; '.join(hard)}")
            continue

        steps = []
        for s in seq.steps:
            row = s.model_dump(mode="json")
            row["day_offset"] = int(day_offsets[s.step - 1]) if s.step - 1 < len(day_offsets) else s.day_offset
            steps.append(row)

        db.upsert_message({
            "company_cik": packet["company"]["cik"],
            "contact_id": packet["contact"]["id"],
            "contact_name": packet["contact"]["name"],
            "contact_title": packet["contact"]["title"],
            "ticker": packet["ticker"],
            "archetype": seq.archetype.value,
            "angle_fingerprint": seq.angle_fingerprint,
            "angle_family": seq.angle_family.value,
            "service": seq.service,
            "steps": steps,
            "qa_warnings": warn,
            "status": "draft",
            "run_id": run_id,
            "model": "claude-code/haiku-subagent",
        })
        summary["written"].append({
            "ticker": packet["ticker"], "contact": packet["contact"]["name"],
            "archetype": seq.archetype.value, "service": seq.service,
        })
        if warn:
            summary["warnings"][stem] = warn

        shutil.move(str(result_file), archive / result_file.name)
        shutil.move(str(packet_file), archive / f"packet_{stem}.json")

    if shared_path.exists() and not pending_queue():
        shared_path.unlink()  # queue fully drained — next prepare rewrites it

    return summary


def pending_queue() -> list[Path]:
    return sorted(p for p in MSG_QUEUE_DIR.glob("*.json") if not p.name.startswith("_"))


def pending_results() -> list[Path]:
    return sorted(p for p in MSG_RESULTS_DIR.glob("*.json") if not p.name.startswith("_"))
