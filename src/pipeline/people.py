"""People search: decision-makers for qualified accounts via Parallel research.

Role targeting comes from the company's service_fit (config people.roles_by_service)
plus always-include roles (CEO — micro-caps buy top-down). Emails are recorded
ONLY when publicly published somewhere citable; no guessing or pattern inference.
"""
from __future__ import annotations

from pipeline.config import SETTINGS
from pipeline.models import Contact
from pipeline.parallel_client import run_task

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


def target_roles(service_fit: list[dict]) -> list[str]:
    cfg = SETTINGS.get("people", {})
    roles: list[str] = list(cfg.get("always_include_roles", ["CEO"]))
    by_service: dict = cfg.get("roles_by_service", {})
    ranked = sorted(service_fit or [], key=lambda s: s.get("priority", 9))
    for fit in ranked[:2]:  # top two services drive targeting
        for role in by_service.get(fit.get("service", ""), []):
            if role not in roles:
                roles.append(role)
    return roles[:6]


def find_people(company: dict, service_fit: list[dict]) -> tuple[list[Contact], str]:
    roles = target_roles(service_fit)
    website = f" (website: {company['website']})" if company.get("website") else ""
    input_text = (
        f"Find the current executives of {company['name']} (US-listed, ticker "
        f"{company['ticker']}){website}. Target roles, in priority order: "
        f"{', '.join(roles)}. For each person found: full name, exact current title, "
        "LinkedIn profile URL, and a company email address ONLY if it is publicly "
        "published somewhere you can cite (investor relations page, press release, "
        "company website) — never guess or construct emails. Note anyone who recently "
        "left or was recently appointed. Small companies may not have all these roles; "
        "report only people you can verify."
    )
    cfg = SETTINGS.get("enrich", {}).get("parallel", {})
    result = run_task(
        input_text, PEOPLE_SCHEMA,
        processor=cfg.get("processor", "base"),
        timeout_s=int(cfg.get("poll_timeout_seconds", 600)),
    )
    content = result["content"]
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
