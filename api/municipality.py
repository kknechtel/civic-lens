"""
Jurisdictions and deployments — everything that makes one deployment Atlantic
Highlands, another all of Nassau County, and another the whole State of New York.

Profiles live in a tree under jurisdictions/, one YAML file per level:

    jurisdictions/us/ny/jurisdiction.yaml                      state
    jurisdictions/us/ny/nassau/jurisdiction.yaml               county
    jurisdictions/us/ny/nassau/long-beach/jurisdiction.yaml    municipality

A deployment is rooted at ONE node, chosen by the DEPLOYMENT env var (a path
like "us/ny/nassau/long-beach", or an alias such as "long-beach-ny"):

  - ancestors (county, state) contribute rules: records law, accounting notes,
    glossary, integrity thresholds, fiscal calendar defaults, and their own
    government as an entity;
  - the root and every descendant contribute entities, sources and venues —
    so rooting at a county indexes every town configured beneath it;
  - `source_templates` on any level are stamped out for every municipality in
    the deployment that supplies the template's variables (e.g. one NY state
    template for State Comptroller audits serves every NY town).

The merged result is a `Municipality` (kept as the name the rest of the app
uses); for a county or state deployment its name/short_name are the root's.
"""
from __future__ import annotations

import copy
import os
import re
import string
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
JURISDICTIONS_DIR = Path(os.getenv("JURISDICTIONS_DIR", REPO_ROOT / "jurisdictions"))
FILE_NAME = "jurisdiction.yaml"

EntityKind = Literal["municipal", "school", "county", "state", "regional", "special_district", "other"]
Level = Literal["state", "county", "municipality"]
_LEVEL_KIND: dict[str, EntityKind] = {"state": "state", "county": "county", "municipality": "municipal"}


# ── Building blocks ──────────────────────────────────────────────────────────

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
    # Older keys stored on documents.municipality; migrated to `key` on boot.
    legacy_keys: list[str] = Field(default_factory=list)
    # Jurisdiction path this entity is defined in (filled by the loader).
    jurisdiction: str = ""


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
    # None = follow the deployment's crawl policy (crawl.respect_robots).
    respect_robots: bool | None = None
    # Free-text record of any permission/arrangement with the site owner.
    permission_note: str = ""
    # Ancestor sources (a county's agenda center) are included in a child
    # deployment only when inherit is true.
    inherit: bool = False
    # Jurisdiction path this source was defined in (filled by the loader).
    jurisdiction: str = ""


class SourceTemplate(BaseModel):
    """A source stamped out once per municipality in the deployment.

    String params may use {name}, {short_name}, {slug}, {state}, {county} and
    any key from the municipality's `template_vars`. A municipality missing a
    variable simply doesn't get the source.
    """
    id: str
    adapter: str
    label: str = ""
    description: str = ""
    entity_kind: EntityKind = "municipal"  # which of the municipality's entities owns the docs
    applies_to: Level = "municipality"
    enabled: bool = True
    historical: bool = False
    doc_type: str | None = None
    category: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    respect_robots: bool | None = None


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
    oversight: list[str] = Field(default_factory=list)
    ai_notes: list[str] = Field(default_factory=list)
    glossary: dict[str, list[str]] = Field(default_factory=dict)


class AIContext(BaseModel):
    """Local knowledge for the chat model. Keep facts sourced and dated."""
    background: str = ""
    notes: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    glossary: dict[str, list[str]] = Field(default_factory=dict)
    department_aliases: dict[str, str] = Field(default_factory=dict)
    earliest_year: int = 1990


class FiscalYear(BaseModel):
    start_month: int = 1  # 1 = calendar year
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
    app_name: str = "Civic Lens"
    tagline: str = "Document Intelligence, Events & Local Business"
    theme_color: str = "#385854"
    public_origin: str = "http://localhost:3000"
    contact_email: str = ""
    banner_url: str = ""
    banner_alt: str = ""


class Features(BaseModel):
    financial: bool = True
    meetings: bool = True
    events: bool = True
    presentations: bool = True
    contracts: bool = True
    alerts: bool = True
    integrity: bool = True


class CrawlPolicy(BaseModel):
    # Whether to obey robots.txt. Public records are public; operators decide.
    respect_robots: bool = True
    # Always identify honestly and stay polite either way.
    min_delay_seconds: float = 1.5


class JurisdictionFile(BaseModel):
    """Raw contents of one jurisdiction.yaml."""
    model_config = {"extra": "forbid"}

    name: str
    short_name: str = ""
    level: Level
    government_type: str = ""
    code: str = ""  # state postal code (state level)
    ocd_id: str = ""
    aliases: list[str] = Field(default_factory=list)  # deployment aliases ("long-beach-ny")
    include: list[str] = Field(default_factory=list)  # extra jurisdictions (neighbours) to index
    timezone: str | None = None
    fiscal_year: FiscalYear | None = None
    branding: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)
    crawl: dict[str, Any] = Field(default_factory=dict)
    integrity: dict[str, Any] = Field(default_factory=dict)
    ai: AIContext = Field(default_factory=AIContext)
    search: SearchConfig = Field(default_factory=SearchConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    entities: list[Entity] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    source_templates: list[SourceTemplate] = Field(default_factory=list)
    template_vars: dict[str, Any] = Field(default_factory=dict)
    # State level only
    records_law: RecordsLaw | None = None
    oversight: list[str] = Field(default_factory=list)
    ai_notes: list[str] = Field(default_factory=list)
    glossary: dict[str, list[str]] = Field(default_factory=dict)


# ── The merged deployment view ───────────────────────────────────────────────

class Municipality(BaseModel):
    """The deployment as the app sees it (a city, a county, or a state)."""
    slug: str  # jurisdiction path, e.g. "us/ny/nassau/long-beach"
    name: str
    short_name: str
    level: Level = "municipality"
    government_type: str = "municipality"
    state: str
    county: str = ""
    timezone: str = "America/New_York"
    fiscal_year: FiscalYear = Field(default_factory=FiscalYear)
    branding: Branding = Field(default_factory=Branding)
    features: Features = Field(default_factory=Features)
    crawl: CrawlPolicy = Field(default_factory=CrawlPolicy)
    integrity: dict[str, Any] = Field(default_factory=dict)
    ai: AIContext = Field(default_factory=AIContext)
    events: EventsConfig = Field(default_factory=EventsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    entities: list[Entity]
    sources: list[Source] = Field(default_factory=list)
    state_profile: StateProfile | None = None
    jurisdictions: list[str] = Field(default_factory=list)  # paths included (root first)
    skipped_templates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> "Municipality":
        keys = [e.key for e in self.entities]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate entity keys in {self.slug}: {sorted(dupes)}")
        ids = [s.id for s in self.sources]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate source ids in {self.slug}: {sorted(dupes)}")
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

    def respects_robots(self, source: Source) -> bool:
        return self.crawl.respect_robots if source.respect_robots is None else source.respect_robots

    def entity_category(self, key: str | None) -> str:
        """Legacy documents.category value ('school' | 'town') for an entity."""
        e = self.entity(key) if key else None
        return "school" if e and e.kind == "school" else "town"

    def fiscal_year_for(self, year: int, month: int, entity_key: str | None = None) -> str:
        """Label for the fiscal year containing (year, month): "2026" for
        calendar-year governments, "2025-26" for July–June ones."""
        e = self.entity(entity_key) if entity_key else None
        start = self.fiscal_year.school_start_month if e and e.kind == "school" else self.fiscal_year.start_month
        if start == 1:
            return str(year)
        first = year if month >= start else year - 1
        return f"{first}-{(first + 1) % 100:02d}"

    def integrity_setting(self, rule: str, key: str, default: Any = None) -> Any:
        return (self.integrity.get(rule) or {}).get(key, default)

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
            "level": self.level,
            "government_type": self.government_type,
            "state": self.state,
            "county": self.county,
            "timezone": self.timezone,
            "fiscal_year": self.fiscal_year.model_dump(),
            "branding": self.branding.model_dump(),
            "features": self.features.model_dump(),
            "entities": [e.model_dump(include={"key", "label", "short", "kind", "jurisdiction"}) for e in self.entities],
            "jurisdictions": self.jurisdictions,
            "records_law": self.state_profile.records_law.model_dump() if self.state_profile else None,
        }


# ── Loading ──────────────────────────────────────────────────────────────────

def _read_yaml(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return data


def _all_paths(base: Path) -> list[str]:
    return sorted(p.parent.relative_to(base).as_posix() for p in base.rglob(FILE_NAME))


def read_jurisdiction(path: str, base: Path | None = None) -> JurisdictionFile:
    base = Path(base or JURISDICTIONS_DIR)
    f = base / path / FILE_NAME
    if not f.exists():
        raise FileNotFoundError(f"No jurisdiction at {f}")
    try:
        return JurisdictionFile.model_validate(_read_yaml(f))
    except Exception as e:
        raise ValueError(f"{path}/{FILE_NAME}: {e}") from e


def resolve(name: str, base: Path | None = None) -> str:
    """Accept a path ("us/ny/nassau/long-beach") or an alias ("long-beach-ny")."""
    base = Path(base or JURISDICTIONS_DIR)
    name = name.strip().strip("/")
    paths = _all_paths(base)
    if name in paths:
        return name
    for p in paths:
        if name in read_jurisdiction(p, base).aliases:
            return p
    raise FileNotFoundError(f"Unknown deployment {name!r}. Jurisdictions: {paths}")


def default_entity_key(path: str) -> str:
    """us/ny/nassau → "ny:nassau"; us/ny → "ny"."""
    parts = path.split("/")[1:]  # drop country
    return ":".join(p.replace("-", "_") for p in parts)


class _SafeVars(dict):
    def __missing__(self, key):
        raise KeyError(key)


def _fill(value: Any, vars_: dict) -> Any:
    if isinstance(value, str):
        # Only substitute {identifier} fields; leave regex quantifiers like {2,4} alone.
        fields = {f for _, f, _, _ in string.Formatter().parse(value) if f}
        if not fields or not all(re.fullmatch(r"[A-Za-z_]\w*", f) for f in fields):
            return value
        return value.format_map(_SafeVars(vars_))
    if isinstance(value, list):
        return [_fill(v, vars_) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, vars_) for k, v in value.items()}
    return value


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _entities_of(path: str, j: JurisdictionFile) -> list[Entity]:
    if j.entities:
        ents = [e.model_copy() for e in j.entities]
    else:
        ents = [Entity(key=default_entity_key(path), label=j.name, short=j.short_name or j.name,
                       kind=_LEVEL_KIND[j.level])]
    for e in ents:
        e.jurisdiction = path
    return ents


def load_deployment(name: str, base_dir: Path | None = None) -> Municipality:
    base = Path(base_dir or JURISDICTIONS_DIR)
    root = resolve(name, base)
    all_paths = _all_paths(base)
    files = {p: read_jurisdiction(p, base) for p in all_paths}
    rj = files[root]

    parts = root.split("/")
    ancestors = [p for p in ("/".join(parts[:i]) for i in range(1, len(parts))) if p in files]
    descendants = [p for p in all_paths if p.startswith(root + "/")]
    included = [root] + descendants + [p for p in rj.include if p in files and p not in descendants]
    unknown_includes = [p for p in rj.include if p not in files]
    if unknown_includes:
        raise ValueError(f"{root}: include references unknown jurisdictions {unknown_includes}")

    state_path = next((p for p in [*ancestors, root] if files[p].level == "state"), None)
    county_path = next((p for p in [*ancestors, root] if files[p].level == "county"), None)
    state_file = files.get(state_path) if state_path else None

    # Settings: ancestors (top-down) then root override.
    settings: dict[str, Any] = {}
    for p in [*ancestors, root]:
        j = files[p]
        for key in ("branding", "features", "crawl", "integrity"):
            settings[key] = _deep_merge(settings.get(key, {}), getattr(j, key))
        if j.fiscal_year:
            settings["fiscal_year"] = j.fiscal_year
        if j.timezone:
            settings["timezone"] = j.timezone

    # Entities: root, descendants/includes, then ancestors nearest-first.
    entities: list[Entity] = []
    for p in included + list(reversed(ancestors)):
        entities += _entities_of(p, files[p])

    # Sources: everything in included; ancestors only when inherit: true.
    sources: list[Source] = []
    for p in included:
        prefix = "" if p == root else p[len(root) + 1:] + ":" if p.startswith(root + "/") else p + ":"
        for s in files[p].sources:
            sources.append(s.model_copy(update={"id": prefix + s.id, "jurisdiction": p}))
    for p in ancestors:
        for s in files[p].sources:
            if s.inherit:
                sources.append(s.model_copy(update={"id": f"{p}:{s.id}", "jurisdiction": p}))

    # Templates from ancestors, root and descendants, applied to each matching jurisdiction.
    templates = [(p, t) for p in [*ancestors, *included] for t in files[p].source_templates]
    targets = [p for p in included]
    skipped: list[str] = []
    for owner, t in templates:
        for p in targets:
            j = files[p]
            if j.level != t.applies_to or not (p == owner or p.startswith(owner + "/") or owner in ancestors):
                continue
            ent = next((e for e in _entities_of(p, j) if e.kind == t.entity_kind), None)
            if ent is None:
                continue
            vars_ = {"name": j.name, "short_name": j.short_name or j.name, "slug": p.rsplit("/", 1)[-1],
                     "state": (state_file.code if state_file else ""), "county": files[county_path].name if county_path else "",
                     **j.template_vars}
            try:
                params = _fill(t.params, vars_)
                label = _fill(t.label or t.id, vars_)
            except KeyError as missing:
                skipped.append(f"{t.id} for {p} (needs {missing})")
                continue
            sid = t.id if len(targets) == 1 or p == root else f"{p[len(root) + 1:] if p.startswith(root + '/') else p}:{t.id}"
            sources.append(Source(
                id=sid, adapter=t.adapter, entity=ent.key, label=label, description=t.description,
                enabled=t.enabled, historical=t.historical, doc_type=t.doc_type, category=t.category,
                params=params, respect_robots=t.respect_robots, jurisdiction=p,
            ))

    venues = [v for p in included for v in files[p].events.venues]

    ai = AIContext(background="\n\n".join(
        (f"## {files[p].name}\n" if p != root else "") + files[p].ai.background.strip()
        for p in included[:10] if files[p].ai.background.strip()
    ), notes=[n for p in [*ancestors, *included] for n in files[p].ai.notes])
    search = SearchConfig(
        glossary={k: v for p in [*ancestors, *included] for k, v in files[p].search.glossary.items()},
        department_aliases={k: v for p in [*ancestors, *included] for k, v in files[p].search.department_aliases.items()},
        earliest_year=rj.search.earliest_year,
    )

    state_profile = None
    if state_file and state_file.records_law:
        state_profile = StateProfile(code=state_file.code, name=state_file.name, records_law=state_file.records_law,
                                     oversight=state_file.oversight, ai_notes=state_file.ai_notes,
                                     glossary=state_file.glossary)

    branding = {"app_name": f"{rj.short_name or rj.name} Civic Lens", **settings.get("branding", {})}
    if rj.branding.get("app_name"):
        branding["app_name"] = rj.branding["app_name"]

    return Municipality.model_validate({
        "slug": root,
        "name": rj.name,
        "short_name": rj.short_name or rj.name,
        "level": rj.level,
        "government_type": rj.government_type or rj.level,
        "state": state_file.code if state_file else "",
        "county": files[county_path].short_name or files[county_path].name if county_path else "",
        "timezone": settings.get("timezone", "America/New_York"),
        "fiscal_year": settings.get("fiscal_year") or FiscalYear(),
        "branding": branding,
        "features": settings.get("features", {}),
        "crawl": settings.get("crawl", {}),
        "integrity": settings.get("integrity", {}),
        "ai": ai,
        "events": {"venues": [v.model_dump() for v in venues]},
        "search": search,
        "entities": [e.model_dump() for e in entities],
        "sources": [s.model_dump() for s in sources],
        "state_profile": state_profile,
        "jurisdictions": included,
        "skipped_templates": skipped,
    })


# Backwards-compatible name used across the app and tests.
load_municipality = load_deployment


def deployment_name() -> str:
    name = os.getenv("DEPLOYMENT") or os.getenv("MUNICIPALITY")
    if not name:
        raise RuntimeError("Set DEPLOYMENT to a jurisdiction path (e.g. us/ny/nassau/long-beach) or alias.")
    return name


@lru_cache(maxsize=1)
def get_municipality() -> Municipality:
    """The deployment this process serves. Cached; restart to pick up edits."""
    return load_deployment(deployment_name())


get_deployment = get_municipality
