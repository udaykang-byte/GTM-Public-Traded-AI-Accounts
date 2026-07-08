"""Pydantic models for pipeline entities and the scoring handoff."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class Status(str, Enum):
    new = "new"
    enriched = "enriched"
    scored = "scored"
    qualified = "qualified"
    disqualified = "disqualified"
    contacts_found = "contacts_found"


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
    # free vocabulary (profile packs bring their own sector sets) — any
    # lowercase string; unknown sectors warn downstream, never crash here
    sector_bucket: str = "other"
    market_cap: float | None = None
    employee_count: int | None = None
    website: str | None = None
    hq_state: str | None = None
    ipo_date: date | None = None
    status: Status = Status.new
    profile: Profile | None = None

    @field_validator("sector_bucket", mode="before")
    @classmethod
    def _lowercase_sector(cls, v):
        return str(v).lower() if v is not None else v


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


class Archetype(str, Enum):
    observation = "observation"
    creative_ideas = "creative_ideas"
    referral_ceiling = "referral_ceiling"
    problem_solution = "problem_solution"
    whole_offer = "whole_offer"
    case_study = "case_study"    # off until citable proof points exist (settings messages.allowed_archetypes)
    benchmark = "benchmark"      # off until citable proof points exist


class CtaType(str, Enum):
    confirm_problem = "confirm_problem"      # step 1: "seeing this too?"
    offer_deliverable = "offer_deliverable"  # step 2: "want the gap map?"
    micro_commitment = "micro_commitment"    # step 3: 2-min asset / single question
    breakup_options = "breakup_options"      # step 4: numbered options


class MessageStep(BaseModel):
    step: int = Field(ge=1, le=4)
    day_offset: int = Field(default=0, ge=0, description="Stamped by commit() from settings; LLM value ignored")
    subject: str | None = Field(default=None, description="Step 1 only; steps 2-4 reply in-thread (null)")
    body: str = Field(description="Fully rendered plain-text body — real names, no merge variables, no signature block")
    cta_type: CtaType


class MessageSequence(BaseModel):
    """What the copywriter LLM must produce for one contact."""

    ticker: str
    contact_name: str = Field(description="Copy EXACTLY from the packet")
    contact_title: str = Field(description="Copy EXACTLY from the packet")
    archetype: Archetype
    angle_fingerprint: str = Field(description="The step-1 angle; copy EXACTLY from the packet")
    angle_family: AngleFamily
    service: str = Field(description="Catalog KEY from the packet's service_fit (e.g. 'ai_consultation')")
    steps: list[MessageStep] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _validate_steps(self):
        if [s.step for s in self.steps] != [1, 2, 3, 4]:
            raise ValueError("steps must be numbered 1-4 in order")
        if not (self.steps[0].subject or "").strip():
            raise ValueError("step 1 must have a subject")
        for s in self.steps[1:]:
            if s.subject:
                raise ValueError(f"step {s.step} must not have a subject (same thread)")
        return self
