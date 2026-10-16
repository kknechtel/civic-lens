"""Every jurisdiction must load as a deployment at any level; schema guards must hold."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from municipality import (JURISDICTIONS_DIR, Municipality, _all_paths, default_entity_key, load_deployment,
                          load_municipality, resolve)
from services.sources.registry import ADAPTERS

PATHS = _all_paths(JURISDICTIONS_DIR)


def test_tree_has_city_county_state():
    assert {"us/ny", "us/ny/nassau", "us/ny/nassau/long-beach", "us/nj", "us/nj/monmouth/atlantic-highlands"} <= set(PATHS)


@pytest.mark.parametrize("path", PATHS)
def test_every_jurisdiction_deploys(path):
    m = load_deployment(path)
    assert m.slug == path and m.entities
    assert m.state_profile is not None, f"{path} has no state ancestor with records_law"
    for s in m.sources:
        assert s.adapter in ADAPTERS, f"{path}:{s.id} uses unknown adapter {s.adapter!r}"
    for v in m.events.venues:
        if v.adapter in ("html", "playwright", "playwright_paginated"):
            assert v.parser, f"{path}: venue {v.name!r} needs a parser"
    assert not m.skipped_templates or all("needs" in t for t in m.skipped_templates)


def test_aliases_resolve():
    assert resolve("long-beach-ny") == "us/ny/nassau/long-beach"
    assert resolve("atlantic-highlands-nj") == "us/nj/monmouth/atlantic-highlands"
    with pytest.raises(FileNotFoundError):
        resolve("springfield")


def test_city_inherits_county_and_state():
    m = load_deployment("us/ny/nassau/long-beach")
    assert m.level == "municipality" and m.state == "NY" and m.county == "Nassau County"
    assert m.entity_keys[:3] == ["long_beach", "lbcsd", "lb_library"]
    assert m.entity_keys[-2:] == ["ny:nassau", "ny"]  # ancestors nearest-first
    assert "legislature" not in {s.id for s in m.sources}  # county source not inherited
    osc = m.source("osc_audits")  # stamped from the NY state template
    assert osc.entity == "long_beach" and osc.jurisdiction == "us/ny/nassau/long-beach"
    assert osc.params["start_urls"] == ["https://www.osc.ny.gov/local-government/audits?combine=Long%20Beach"]
    assert osc.params["follow_pattern"].endswith(r"/\d{4}/")  # regex quantifiers untouched by templating


def test_county_deployment_includes_towns():
    m = load_deployment("us/ny/nassau")
    assert m.level == "county" and m.name == "County of Nassau"
    assert m.entity_keys[0] == "ny:nassau" and "long_beach" in m.entity_keys
    ids = {s.id for s in m.sources}
    assert "legislature" in ids and "long-beach:city_council" in ids and "long-beach:osc_audits" in ids


def test_state_deployment_includes_everything_below():
    m = load_deployment("us/nj")
    assert {"nj", "nj:monmouth", "atlantic_highlands", "highlands", "hhrsd"} <= set(m.entity_keys)


def test_neighbour_include_and_legacy_keys():
    m = load_deployment("us/nj/monmouth/atlantic-highlands")
    assert "highlands" in m.entity_keys  # via include:
    assert m.source("us/nj/monmouth/highlands:highlands_borough").entity == "highlands"
    assert m.entity("nj").legacy_keys == ["state"] and m.entity("nj:monmouth").legacy_keys == ["regional"]


def test_default_entity_keys():
    assert default_entity_key("us/ny/nassau") == "ny:nassau"
    assert default_entity_key("us/nj/monmouth/sea-bright") == "nj:monmouth:sea_bright"


def test_template_skips_towns_missing_vars(tmp_path):
    (tmp_path / "us/zz/town").mkdir(parents=True)
    (tmp_path / "us/zz/jurisdiction.yaml").write_text(yaml.safe_dump({
        "name": "State of ZZ", "level": "state", "code": "ZZ",
        "records_law": {"name": "Open Records", "short": "ORA"},
        "source_templates": [{"id": "audits", "adapter": "html_index",
                              "params": {"start_urls": ["https://zz.gov/audits?q={audit_code}"]}}],
    }))
    (tmp_path / "us/zz/town/jurisdiction.yaml").write_text(yaml.safe_dump({"name": "Town", "level": "municipality"}))
    m = load_deployment("us/zz/town", base_dir=tmp_path)
    assert m.source("audits") is None and "needs 'audit_code'" in m.skipped_templates[0]


def _base(**over):
    data = {
        "slug": "x", "name": "Town of X", "short_name": "X", "state": "NY",
        "entities": [{"key": "x", "label": "Town of X", "short": "X"}],
        "sources": [],
    }
    data.update(over)
    return data


def test_source_must_reference_known_entity():
    with pytest.raises(ValidationError, match="unknown entity"):
        Municipality.model_validate(_base(sources=[{"id": "s", "adapter": "html_index", "entity": "nope"}]))


def test_crawl_policy_inherits_and_source_overrides():
    m = load_deployment("us/ny/nassau/long-beach")
    assert m.crawl.respect_robots is False  # set on us/ny
    src = m.sources[0].model_copy(update={"respect_robots": True})
    assert m.respects_robots(src) is True and m.respects_robots(m.sources[0]) is False


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
    pub = load_municipality("long-beach-ny").public_profile()
    assert "sources" not in pub and pub["records_law"]["short"] == "FOIL"
    assert pub["entities"][0]["key"] == "long_beach" and pub["level"] == "municipality"


def test_state_glossary_merges_under_local():
    m = load_municipality("long-beach-ny")
    assert "foil" in m.glossary and "lbcsd" in m.glossary


def test_yaml_files_parse_cleanly():
    for p in Path(JURISDICTIONS_DIR).rglob("*.yaml"):
        data = yaml.safe_load(p.read_text())
        if p.parent.name == "evals":  # eval sets are lists of {q, expect}
            assert isinstance(data, list) and all({"q", "expect"} <= set(c) for c in data), p
        else:
            assert isinstance(data, dict), p
