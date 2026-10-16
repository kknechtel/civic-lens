"""
Municipality profile — everything that makes one deployment Atlantic Highlands
and another Long Beach.

A deployment serves exactly one profile, chosen by the MUNICIPALITY env var
(a directory name under municipalities/). The profile is plain YAML so a new
town can be added without touching Python: name, state, fiscal calendar, the
governments whose records we index ("entities"), and the sources to scrape.

State-level facts (records law, deadlines) live in municipalities/_states/
and are merged in by the `state` field.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MUNICIPALITIES_DIR = Path(os.getenv("MUNICIPALITIES_DIR", REPO_ROOT / "municipalities"))

EntityKind = Literal["municipal", "school", "county", "state", "regional", "special_district", "other"]


class Entity(BaseModel):
    """A government body whose documents we index (the library's top-level groups)."""
    key: str
    label: str
    short: str
    kind: EntityKind = "municipal"
    # Shown to the enrichment model so it can tell neighbouring bodies apart.
    description: str = ""
    # Phrases that identify this entity's documents (letterheads, street names).
    aliases: list[str] = Field(default_factory=list)


class Source(BaseModel):
    """One place we pull documents/recordings/events from."""
    id: str
    adapter: str  # key in services.sources.registry.ADAPTERS
    entity: str  # Entity.key the documents belong to
    label: str = ""
    description: str = ""  # shown on the admin Scraper page
    note: str = ""  # operator caveat ("Cloudflare protected — needs Chrome")
    enabled: bool = True
    # Historical sources are skipped in recent-only runs (nightly schedule).
    historical: bool = False
    # Default doc_type / category for everything this source yields; the
    # filename heuristics and enrichment pass can still refine doc_type.
    doc_type: str | None = None
    category: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # robots.txt is honoured unless the site owner has said otherwise.
    # Opting out requires recording who granted permission and when.
    respect_robots: bool = True
    permission_note: str = ""

    @model_validator(mode="after")
    def _permission(self) -> "Source":
        if not self.respect_robots and not self.permission_note.strip():
            raise ValueError(f"source {self.id!r}: respect_robots: false requires a permission_note "
                             "(who granted permission to crawl, and when)")
        return self


class RecordsLaw(BaseModel):
    name: str  # "Open Public Records Act"
    short: str  # "OPRA"
    citation: str = ""
    acknowledge_business_days: int | None = None
    respond_business_days: int | None = None
    notes: str = ""


class StateProfile(BaseModel):
    code: str
    name: str
    records_law: RecordsLaw
    # Fiscal-oversight agencies and data portals the enrichment prompt and
    # chat can cite ("NJ DLGS", "NY Office of the State Comptroller").
    oversight: list[str] = Field(default_factory=list)
    # Analytical caveats every town in the state shares (accounting basis,
    # statutory caps). Appended to the chat instructions.
    ai_notes: list[str] = Field(default_factory=list)
    # Agency acronyms → expansions for search ("njdep": [...]).
    glossary: dict[str, list[str]] = Field(default_factory=dict)


class AIContext(BaseModel):
    """Local knowledge for the chat model. Keep facts sourced and dated."""
    background: str = ""
    notes: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    # Local abbreviations → expansions ("lbcsd": ["long beach city school district"]).
    glossary: dict[str, list[str]] = Field(default_factory=dict)
    # Phrases → canonical department name ("lbpd": "Police").
    department_aliases: dict[str, str] = Field(default_factory=dict)
    # Earliest year the corpus plausibly covers (filters OCR'd junk dates).
    earliest_year: int = 1990


class FiscalYear(BaseModel):
    # Month the municipal fiscal year starts (1 = calendar year).
    start_month: int = 1
    # School districts in most states run July–June regardless of the town.
    school_start_month: int = 7

    @field_validator("start_month", "school_start_month")
    @classmethod
    def _month(cls, v: int) -> int:
        if not 1 <= v <= 12:
            raise ValueError("month must be 1-12")
        return v


class Venue(BaseModel):
    name: str
    city: str = ""
    adapter: Literal["html", "playwright", "playwright_paginated", "squarespace", "tribe_events", "bandsintown", "ical"]
    parser: str | None = None
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class EventsConfig(BaseModel):
    venues: list[Venue] = Field(default_factory=list)


class Branding(BaseModel):
    app_name: str
    tagline: str = "Document Intelligence, Events & Local Business"
    theme_color: str = "#385854"
    public_origin: str = "http://localhost:3000"
    contact_email: str = ""
    # Optional dashboard photo: a path under web/public or an absolute URL.
    banner_url: str = ""
    banner_alt: str = ""


class Features(BaseModel):
    financial: bool = True
    meetings: bool = True
    events: bool = True
    presentations: bool = True
    contracts: bool = True
    alerts: bool = True


class Municipality(BaseModel):
    slug: str
    name: str  # "Borough of Atlantic Highlands"
    short_name: str  # "Atlantic Highlands"
    government_type: str = "municipality"  # borough, city, village, town, township
    state: str  # two-letter code; loads _states/<code>.yaml
    county: str = ""
    timezone: str = "America/New_York"
    fiscal_year: FiscalYear = Field(default_factory=FiscalYear)
    branding: Branding
    features: Features = Field(default_factory=Features)
    ai: AIContext = Field(default_factory=AIContext)
    events: EventsConfig = Field(default_factory=EventsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    entities: list[Entity]
    sources: list[Source] = Field(default_factory=list)
    state_profile: StateProfile | None = None

    @model_validator(mode="after")
    def _check_refs(self) -> "Municipality":
        keys = [e.key for e in self.entities]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate entity keys in {self.slug}")
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate source ids in {self.slug}")
        for s in self.sources:
            if s.entity not in keys:
                raise ValueError(f"source {s.id!r} references unknown entity {s.entity!r}")
        return self

    # ── helpers ────────────────────────────────────────────────────────
    @property
    def entity_keys(self) -> list[str]:
        return [e.key for e in self.entities]

    @property
    def primary_entity(self) -> Entity:
        return self.entities[0]

    def entity(self, key: str) -> Entity | None:
        return next((e for e in self.entities if e.key == key), None)

    def source(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def entity_category(self, key: str | None) -> str:
        """Legacy documents.category value ('school' | 'town') for an entity."""
        e = self.entity(key) if key else None
        return "school" if e and e.kind == "school" else "town"

    def fiscal_year_for(self, year: int, month: int, entity_key: str | None = None) -> str:
        """Label for the fiscal year containing (year, month).

        Calendar-year governments get "2026"; July–June governments get
        "2025-26" style labels so they line up with how their budgets are titled.
        """
        e = self.entity(entity_key) if entity_key else None
        start = self.fiscal_year.school_start_month if e and e.kind == "school" else self.fiscal_year.start_month
        if start == 1:
            return str(year)
        first = year if month >= start else year - 1
        return f"{first}-{(first + 1) % 100:02d}"

    @property
    def glossary(self) -> dict[str, list[str]]:
        merged = dict(self.state_profile.glossary) if self.state_profile else {}
        merged.update(self.search.glossary)
        return merged

    def entities_prompt(self) -> str:
        """One line per entity for model prompts: key, description, aliases."""
        lines = []
        for e in self.entities:
            alias = f" (also: {', '.join(e.aliases)})" if e.aliases else ""
            lines.append(f"- {e.key}: {e.description or e.label}{alias}")
        return "\n".join(lines)

    def public_profile(self) -> dict:
        """What the web client needs; never includes source params."""
        return {
            "slug": self.slug,
            "name": self.name,
            "short_name": self.short_name,
            "government_type": self.government_type,
            "state": self.state,
            "county": self.county,
            "timezone": self.timezone,
            "fiscal_year": self.fiscal_year.model_dump(),
            "branding": self.branding.model_dump(),
            "features": self.features.model_dump(),
            "entities": [e.model_dump(include={"key", "label", "short", "kind"}) for e in self.entities],
            "records_law": self.state_profile.records_law.model_dump() if self.state_profile else None,
        }


def _read_yaml(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return data


def load_municipality(slug: str, base_dir: Path | None = None) -> Municipality:
    base = Path(base_dir or MUNICIPALITIES_DIR)
    path = base / slug / "municipality.yaml"
    if not path.exists():
        available = sorted(p.parent.name for p in base.glob("*/municipality.yaml"))
        raise FileNotFoundError(f"No municipality profile at {path}. Available: {available}")
    data = _read_yaml(path)
    data.setdefault("slug", slug)
    state_path = base / "_states" / f"{str(data.get('state', '')).lower()}.yaml"
    if state_path.exists():
        data["state_profile"] = _read_yaml(state_path)
    return Municipality.model_validate(data)


@lru_cache(maxsize=1)
def get_municipality() -> Municipality:
    """The profile this process serves. Cached; restart to pick up edits."""
    return load_municipality(os.getenv("MUNICIPALITY", "atlantic-highlands-nj"))
