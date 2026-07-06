"""Pydantic models for pipeline entities and the scoring handoff."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class Status(str, Enum):
    new = "new"
    enriched = "enriched"
    scored = "scored"
    qualified = "qualified"
    disqualified = "disqualified"
    contacts_found = "contacts_found"


class Sector(str, Enum):
    saas = "saas"
    fintech = "fintech"
    edtech = "edtech"
    healthcare = "healthcare"
    other = "other"


class Profile(str, Enum):
    laggard = "laggard"      # talks AI (or should), no visible execution
    adopter = "adopter"      # visibly investing in AI
    hybrid = "hybrid"
    unclear = "unclear"


class Company(BaseModel):
    cik: int
    ticker: str
    name: str
    exchange: str | None = None
    sic: str | None = None
    sic_description: str | None = None
    sector_bucket: Sector = Sector.other
    market_cap: float | None = None
    employee_count: int | None = None
    website: str | None = None
    hq_state: str | None = None
    ipo_date: date | None = None
    status: Status = Status.new
    profile: Profile | None = None


class Signal(BaseModel):
    company_cik: int
    source: str  # edgar | parallel | derived
    type: str    # E1..E9, P1..P6
    title: str
    detail: str = ""
    evidence_url: str | None = None
    evidence_quote: str | None = None
    observed_at: date | None = None
    weight: float = 0
    raw: dict = Field(default_factory=dict)


class AngleFamily(str, Enum):
    funding = "funding"
    leadership = "leadership"
    ai_move = "ai_move"


class FundingDetails(BaseModel):
    amount_usd: float | None = None
    instrument: Literal["follow_on", "atm", "pipe", "shelf", "debt", "other"] = "other"
    announced: date | None = None
    use_of_proceeds: str | None = None
    filing_type: str | None = None


class LeadershipDetails(BaseModel):
    role: str
    person_name: str | None = None
    start_date: date | None = None
    first_in_role: bool = False
    mandate_quote: str | None = None


class AiMoveDetails(BaseModel):
    initiative: str
    move_type: Literal["product_launch", "partnership", "pilot", "exec_statement"] = "product_launch"
    partner: str | None = None
    exec_quote: str | None = None
    announced: date | None = None


ANGLE_DETAILS_MODELS: dict[str, type[BaseModel]] = {
    "funding": FundingDetails,
    "leadership": LeadershipDetails,
    "ai_move": AiMoveDetails,
}


class Angle(BaseModel):
    """One dated outreach event. Deduped by fingerprint; never bulk-wiped
    (unlike signals). Families and semantics: docs/SIGNALS.md."""

    company_cik: int
    family: AngleFamily
    headline: str
    details: dict = Field(default_factory=dict)
    evidence_url: str | None = None
    evidence_quote: str | None = None
    event_date: date
    source: str  # edgar | parallel
    strength: float = 0
    status: str = "active"  # active | stale
    fingerprint: str

    @model_validator(mode="after")
    def _validate_details(self):
        model = ANGLE_DETAILS_MODELS[self.family.value]
        self.details = model.model_validate(self.details).model_dump(mode="json")
        return self


class ServiceFit(BaseModel):
    service: str
    priority: int = 1  # 1 = lead with this
    rationale: str = ""


class AngleRef(BaseModel):
    fingerprint: str
    family: AngleFamily
    message_hook: str = Field(description="One-sentence opening line a seller could use for this angle")


class PrimaryAngle(BaseModel):
    fingerprint: str
    family: AngleFamily
    why_this_angle: str


class ScoreVerdict(BaseModel):
    """What the scoring LLM must produce for one company."""

    ticker: str
    intent: int = Field(ge=0, le=30, description="Stated AI interest/urgency")
    capability_gap: int = Field(ge=0, le=25, description="Lack of internal AI capability")
    timing: int = Field(ge=0, le=25, description="Buying-window signals (new exec, restructuring, IPO)")
    commercial_fit: int = Field(ge=0, le=20, description="Size/sector/GTM/economics fit for martechs.io")
    profile: Profile
    service_fit: list[ServiceFit]
    reasoning: str = Field(description="Cited reasoning; must reference packet evidence")
    why_now: str = Field(description="Outreach thesis: freshest dated evidence + the window it opens, or an explicit 'no fresh timing' statement")
    evidence_cited: list[str] = Field(default_factory=list)
    confidence: str = "medium"  # low | medium | high
    angle_ranking: list[AngleRef] = Field(
        default_factory=list,
        description="All packet angles ranked by outreach power, strongest first; [] if the packet has no angles",
    )
    primary_angle: PrimaryAngle | None = Field(
        default=None, description="The single angle outreach should lead with; null if no angles"
    )

    @computed_field  # type: ignore[misc]
    @property
    def total(self) -> int:
        return self.intent + self.capability_gap + self.timing + self.commercial_fit


class Contact(BaseModel):
    company_cik: int
    name: str
    title: str
    role_bucket: str = ""
    linkedin_url: str | None = None
    email: str | None = None
    email_source: str | None = None  # URL where the email is published
    confidence: str = "medium"
    evidence: dict = Field(default_factory=dict)
