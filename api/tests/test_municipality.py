"""Every shipped profile must load and validate; schema guards must hold."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from municipality import MUNICIPALITIES_DIR, Municipality, load_municipality
from services.sources.registry import ADAPTERS

PROFILES = sorted(p.parent.name for p in Path(MUNICIPALITIES_DIR).glob("*/municipality.yaml"))


def test_profiles_exist():
    assert {"atlantic-highlands-nj", "long-beach-ny"} <= set(PROFILES)


@pytest.mark.parametrize("slug", PROFILES)
def test_profile_loads(slug):
    m = load_municipality(slug)
    assert m.slug == slug
    assert m.entities, "a profile needs at least one entity"
    assert m.state_profile is not None, f"no _states/{m.state.lower()}.yaml for {slug}"
    for s in m.sources:
        assert s.adapter in ADAPTERS, f"{slug}:{s.id} uses unknown adapter {s.adapter!r}"
    for v in m.events.venues:
        if v.adapter in ("html", "playwright", "playwright_paginated"):
            assert v.parser, f"{slug}: venue {v.name!r} needs a parser"


def _base(**over):
    data = {
        "slug": "x", "name": "Town of X", "short_name": "X", "state": "NY",
        "branding": {"app_name": "X"},
        "entities": [{"key": "x", "label": "Town of X", "short": "X"}],
        "sources": [],
    }
    data.update(over)
    return data


def test_source_must_reference_known_entity():
    with pytest.raises(ValidationError, match="unknown entity"):
        Municipality.model_validate(_base(sources=[{"id": "s", "adapter": "html_index", "entity": "nope"}]))


def test_robots_opt_out_requires_permission_note():
    src = {"id": "s", "adapter": "html_index", "entity": "x", "respect_robots": False}
    with pytest.raises(ValidationError, match="permission_note"):
        Municipality.model_validate(_base(sources=[src]))
    src["permission_note"] = "City Clerk J. Doe, email 2026-10-20"
    assert Municipality.model_validate(_base(sources=[src])).sources[0].respect_robots is False


def test_duplicate_ids_rejected():
    s = {"id": "s", "adapter": "html_index", "entity": "x"}
    with pytest.raises(ValidationError, match="duplicate source"):
        Municipality.model_validate(_base(sources=[s, s]))


@pytest.mark.parametrize("start,year,month,expected", [
    (1, 2026, 3, "2026"),
    (7, 2026, 3, "2025-26"),
    (7, 2026, 7, "2026-27"),
    (7, 2099, 12, "2099-00"),
])
def test_fiscal_year_labels(start, year, month, expected):
    m = Municipality.model_validate(_base(fiscal_year={"start_month": start}))
    assert m.fiscal_year_for(year, month) == expected


def test_school_entities_use_school_calendar():
    m = Municipality.model_validate(_base(
        fiscal_year={"start_month": 1, "school_start_month": 7},
        entities=[{"key": "x", "label": "X", "short": "X"},
                  {"key": "sd", "label": "X SD", "short": "SD", "kind": "school"}],
    ))
    assert m.fiscal_year_for(2026, 9) == "2026"
    assert m.fiscal_year_for(2026, 9, "sd") == "2026-27"
    assert m.entity_category("sd") == "school"
    assert m.entity_category("x") == "town"


def test_public_profile_hides_source_params():
    m = load_municipality("long-beach-ny")
    pub = m.public_profile()
    assert "sources" not in pub
    assert pub["records_law"]["short"] == "FOIL"
    assert [e["key"] for e in pub["entities"]][0] == "long_beach"


def test_state_glossary_merges_under_local():
    m = load_municipality("long-beach-ny")
    assert "foil" in m.glossary  # from _states/ny.yaml
    assert "lbcsd" in m.glossary  # from the profile


def test_long_beach_city_sources_stay_robots_compliant():
    """longbeachny.gov disallows crawlers; until the city grants permission no
    source may opt out of robots.txt for that host."""
    m = load_municipality("long-beach-ny")
    for s in m.sources:
        urls = " ".join(map(str, s.params.get("start_urls", [])))
        if "longbeachny.gov" in urls:
            assert s.respect_robots, s.id


def test_yaml_files_parse_cleanly():
    for p in Path(MUNICIPALITIES_DIR).rglob("*.yaml"):
        assert isinstance(yaml.safe_load(p.read_text()), dict), p
