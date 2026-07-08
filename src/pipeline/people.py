"""People search: decision-makers for qualified accounts via Parallel research.

Role targeting comes from the company's service_fit — via config/personas.yaml
services mapping when the active pack has one (PERSONAS non-empty), else the
legacy flat config people.roles_by_service lookup — plus always-include roles
(CEO — micro-caps buy top-down). Emails are recorded ONLY when publicly
published somewhere citable; no guessing or pattern inference.
"""
from __future__ import annotations

from pipeline.config import PERSONAS, SETTINGS
from pipeline.db import order_by_tier_priority
from pipeline.models import Contact
from pipeline.parallel_client import run_task, run_tasks_batch

PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string", "description": "Exact current title"},
                    "linkedin_url": {"type": "string", "description": "LinkedIn profile URL, or empty if not found"},
                    "email": {"type": "string", "description": "ONLY if publicly published (IR page, press release, website). Empty otherwise. Never guess."},
                    "email_source_url": {"type": "string", "description": "URL where the email is published"},
                    "confidence": {"type": "string", "description": "high | medium | low — confidence this person currently holds this role"},
                    "source_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "title", "confidence"],
            },
        },
        "notes": {"type": "string", "description": "Roles that could not be found, ambiguities"},
    },
    "required": ["people"],
    "additionalProperties": False,
}


def _service_key(value: str) -> str:
    """Scorers sometimes write the display name — normalize to the catalog key."""
    from pipeline.config import SERVICES

    raw = (value or "").strip()
    if raw in SERVICES:
        return raw
    low = raw.lower()
    for key, svc in SERVICES.items():
        if low == key.lower() or low == str(svc.get("name", "")).lower():
            return key
    for key, svc in SERVICES.items():
        if str(svc.get("name", "")).lower() in low or key.replace("_", " ") in low:
            return key
    return raw


def persona_defs(personas: dict | None = None) -> dict:
    """personas (default: this module's PERSONAS) minus the reserved
    "services" key — persona key -> persona dict. Takes an explicit dict so
    callers with their own PERSONAS reference (messages.py — tests monkeypatch
    it independently of this module's) get a consistent view."""
    source = PERSONAS if personas is None else personas
    return {k: v for k, v in source.items() if k != "services"}


def target_roles(service_fit: list[dict]) -> list[str]:
    """Personas path (config/personas.yaml services mapping) when the active
    pack has personas, else the legacy flat roles_by_service lookup. The
    default pack's personas.yaml encodes the exact same role lists in the
    exact same order, so output is identical either way — see
    tests/test_personas.py::test_target_roles_identical_to_legacy_path."""
    cfg = SETTINGS.get("people", {})
    roles: list[str] = list(cfg.get("always_include_roles", ["CEO"]))
    ranked = sorted(service_fit or [], key=lambda s: s.get("priority", 9))
    if PERSONAS:
        defs = persona_defs()
        services_map: dict = PERSONAS.get("services", {})
        for fit in ranked[:2]:  # top two services drive targeting
            for key in services_map.get(_service_key(fit.get("service", "")), []):
                persona = defs.get(key) or {}
                role = persona.get("role_bucket")
                if role and role not in roles:
                    roles.append(role)
    else:
        by_service: dict = cfg.get("roles_by_service", {})
        for fit in ranked[:2]:  # top two services drive targeting
            for role in by_service.get(_service_key(fit.get("service", "")), []):
                if role not in roles:
                    roles.append(role)
    return roles[:6]


def match_persona(role_bucket: str, title: str = "") -> dict | None:
    """Resolve a persona for a contact — used to attach pains/language to a
    message packet (messages.py). Tries the contact's stored role_bucket
    first (case-insensitive exact match against persona.role_bucket — this
    accepts LEGACY bucket values already in the DB, e.g. "CEO", captured by
    the keyword match in _contacts_from_result before personas existed).
    Falls back to matching the contact's title against each persona's title
    variants — needed because that legacy match is a plain substring test of
    the abbreviated role_bucket token in the title (e.g. "cmo" is not a
    substring of "chief marketing officer"), so many real contacts land with
    role_bucket == "" despite having a clearly identifiable title. Returns
    None if PERSONAS is empty (no persona pack active) or nothing matches."""
    if not PERSONAS:
        return None
    defs = persona_defs()

    rb = (role_bucket or "").strip().lower()
    if rb:
        for persona in defs.values():
            if str(persona.get("role_bucket", "")).strip().lower() == rb:
                return persona

    t = (title or "").strip().lower()
    if t:
        for persona in defs.values():
            for variant in persona.get("titles", []) or []:
                v = str(variant).strip().lower()
                if v and (v in t or t in v):
                    return persona

    return None


def _people_input_text(company: dict, roles: list[str]) -> str:
    website = f" (website: {company['website']})" if company.get("website") else ""
    return (
        f"Find the current executives of {company['name']} (US-listed, ticker "
        f"{company['ticker']}){website}. Target roles, in priority order: "
        f"{', '.join(roles)}. For each person found: full name, exact current title, "
        "LinkedIn profile URL, and a company email address ONLY if it is publicly "
        "published somewhere you can cite (investor relations page, press release, "
        "company website) — never guess or construct emails. Note anyone who recently "
        "left or was recently appointed. Small companies may not have all these roles; "
        "report only people you can verify."
    )


def _contacts_from_result(company: dict, roles: list[str], content: dict) -> tuple[list[Contact], str]:
    contacts: list[Contact] = []
    for p in content.get("people", []):
        name = (p.get("name") or "").strip()
        title = (p.get("title") or "").strip()
        if not name or not title:
            continue
        role_bucket = next(
            (r for r in roles if r.lower() in title.lower()),
            roles[0] if any(k in title.lower() for k in ("chief executive", "ceo")) else "",
        )
        contacts.append(Contact(
            company_cik=company["cik"],
            name=name, title=title, role_bucket=role_bucket,
            linkedin_url=(p.get("linkedin_url") or None),
            email=(p.get("email") or None),
            email_source=(p.get("email_source_url") or None),
            confidence=p.get("confidence", "medium"),
            evidence={"source_urls": p.get("source_urls", [])},
        ))
    return contacts, content.get("notes", "")


def find_people(company: dict, service_fit: list[dict]) -> tuple[list[Contact], str]:
    roles = target_roles(service_fit)
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    result = run_task(
        _people_input_text(company, roles), PEOPLE_SCHEMA,
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    return _contacts_from_result(company, roles, result["content"])


def find_people_batch(items: list[tuple[dict, list[dict]]]) -> list[tuple[list[Contact], str] | Exception]:
    """items = [(company, service_fit), ...] — one Parallel task each, polled together."""
    if not items:
        return []
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    roles_per = [target_roles(fits) for _, fits in items]
    results = run_tasks_batch(
        [(_people_input_text(c, roles), PEOPLE_SCHEMA)
         for (c, _), roles in zip(items, roles_per)],
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    out: list = []
    for (company, _), roles, result in zip(items, roles_per, results):
        if isinstance(result, Exception):
            out.append(result)
        else:
            try:
                out.append(_contacts_from_result(company, roles, result["content"]))
            except Exception as exc:
                out.append(exc)
    return out


def select_targets(companies: list[dict], priority_by_cik: dict[int, float | None], cap: int) -> list[dict]:
    """Qualified companies ready for people search, ordered by (tier asc,
    priority desc) — see db.order_by_tier_priority — then capped so the
    strongest accounts get worked first when the per-run cap bites."""
    return order_by_tier_priority(companies, priority_by_cik)[: max(cap, 0)]
