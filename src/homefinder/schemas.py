"""Data models: what the user tells us, and what we extract about a listing."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

Furnishing = Literal["furnished", "semi", "unfurnished"]
SourceType = Literal["ad", "builder", "forum", "review"]
Level = Literal["high", "medium", "low"]
# Evidence aspects. Each maps to one of the six criteria in rulebook.toml [evidence.aspect_criteria].
Aspect = Literal[
    "property_condition", "management", "cleanliness", "safety", "food", "noise", "rules",
    "location", "hidden_charges", "deposit_refund", "lock_in_notice",
]


class UserProfile(BaseModel):
    city: str = Field(description="City the user is moving to (any Indian city)")
    work_location: str
    budget: float = Field(gt=0, description="Monthly ceiling, Rs (hard cutoff)")
    upfront_cash: float = Field(gt=0, description="Cash available for deposit etc., Rs")
    horizon_months: Literal[6, 12, 24] | None = None  # None = not sure
    mover: Literal["solo", "friends", "family"]
    group_size: int = Field(1, ge=1, description="Total people moving, including the user")
    commute_tolerance: Literal[20, 40, 60, 90]
    move_in_date: date
    food: Literal["cook myself", "need meals", "eat out"]
    privacy: Literal["private room", "sharing is fine"]
    furnishing: Furnishing
    noise: Literal["quiet", "social", "no preference"]
    gender_restricted: bool
    # lifestyle key -> True if must-have. Keys: food, privacy, furnishing, noise, gender
    must_have: dict[str, bool] = {}

    @field_validator("city")
    @classmethod
    def _city_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("city must not be blank")
        return v

    @property
    def horizon(self) -> int:
        return self.horizon_months or 12

    @property
    def sharers(self) -> int:
        """People splitting a flat's costs. Family moves as one paying unit."""
        return self.group_size if self.mover == "friends" else 1

    @property
    def bhk(self) -> int:
        return 1 if self.mover == "solo" else max(1, round(self.group_size / 2))


class Provenance(BaseModel):
    """Where a listing came from. Set by code from the saved document, never by the LLM."""
    source_url: str
    source_type: SourceType | None = None
    agent_source: str
    first_seen_at: str  # ISO time we first saved this page
    retrieved_at: str   # ISO time we last fetched it


class EvidenceItem(BaseModel):
    """One opinion about one aspect of one property ("good property, bad caretakers" is two items)."""
    subject: str = Field(description="Name of the property the statement is about")
    aspect: Aspect
    polarity: Literal["positive", "negative"]
    severity: Level = "medium"
    # Set by code (hidden from the LLM schema).
    source_url: SkipJsonSchema[str | None] = None
    source_type: SkipJsonSchema[SourceType | None] = None


class Listing(BaseModel):
    """One PG / flat as extracted from web text. Unknown values stay None."""
    name: str
    url: str | None = None
    kind: Literal["pg", "flat"]
    area: str | None = None
    rent: float = Field(gt=0, description="Monthly. Per bed for PG, whole unit for flat")
    maintenance: float | None = None
    extras: float | None = None
    deposit: float | None = None
    brokerage: float | None = None
    lock_in_months: int | None = None
    notice_months: float | None = None
    furnishing: Furnishing | None = None
    private_room: bool | None = None
    meals_included: bool | None = None
    kitchen_access: bool | None = None
    vibe: Literal["quiet", "social"] | None = None
    gender_restricted: bool | None = None
    commute_minutes: float | None = None  # not extracted (depends on the user); unknown until a maps step
    # Identity fields used to merge repeated ads (Phase 7.2).
    building_name: str | None = None
    phone: str | None = None
    bhk: int | None = None
    sharing: Literal["single", "double", "triple"] | None = None
    lat: float | None = None
    lng: float | None = None
    extraction_confidence: Level | None = Field(None, description="How clearly the text states this listing's facts")
    # Set by code after extraction (hidden from the LLM schema, so they are not part of the prompt version).
    provenance: SkipJsonSchema[Provenance | None] = None       # the source this record's values came from
    observations: SkipJsonSchema[list[Provenance]] = []        # every source that showed this property
    missing_fields: SkipJsonSchema[list[str]] = []
    property_id: SkipJsonSchema[str | None] = None
    evidence: SkipJsonSchema[list[EvidenceItem]] = []          # evidence items matched to this property


class ListingBatch(BaseModel):
    """The saved answer for one page: its listings, what kind of page it is, and any evidence items."""
    source_type: SourceType | None = None
    listings: list[Listing] = []
    evidence: list[EvidenceItem] = []


class DocListings(ListingBatch):
    """One page of a batched extraction call."""
    doc_id: int


class BatchAnswer(BaseModel):
    documents: list[DocListings]


class CityCosts(BaseModel):
    """One row of the city cost-of-living dataset (INR per month, one person)."""
    name: str
    cost_one_person: float
    rent_one_person: float
    data_quality: Literal["ok", "suspect"] = "ok"
    usable: bool = True  # False when the row is impossible (rent above or near total cost)

