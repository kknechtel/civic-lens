"""Model prompts are rendered from the profile — no town leaks into another."""
import pytest

from municipality import get_municipality, load_municipality


class _Doc:
    filename = "minutes.pdf"
    metadata_ = {"source_site": "city_council", "source_url": "https://x/m.pdf", "title": "Minutes"}
    id = "1"


@pytest.fixture
def as_profile(monkeypatch):
    def use(slug):
        monkeypatch.setenv("DEPLOYMENT", slug)
        get_municipality.cache_clear()
        return get_municipality()
    yield use
    get_municipality.cache_clear()


def test_enrich_prompt_for_long_beach(as_profile):
    from services import doc_enrich

    muni = as_profile("long-beach-ny")
    prompt = doc_enrich.render_prompt(_Doc(), "City Council minutes...", muni)
    assert "Long Beach, NY" in prompt
    assert "- lbcsd: Long Beach City School District" in prompt
    assert "month 7" in prompt  # July–June fiscal year rule
    for leak in ("Atlantic Highlands", "HHRSD", "Monmouth", "New Jersey"):
        assert leak not in prompt
    schema = doc_enrich.schema_for(muni)
    assert schema["properties"]["municipality"]["enum"] == muni.entity_keys
    assert "enum" not in doc_enrich.SCHEMA["properties"]["municipality"]  # base schema untouched


def test_enrich_prompt_for_atlantic_highlands(as_profile):
    from services import doc_enrich

    muni = as_profile("atlantic-highlands-nj")
    prompt = doc_enrich.render_prompt(_Doc(), "text", muni)
    assert "- highlands: Borough of Highlands — a DIFFERENT neighboring town" in prompt
    assert "Long Beach" not in prompt


def test_chat_instructions(as_profile):
    as_profile("long-beach-ny")
    from services.chat import prompt

    text = prompt._background() + prompt._working_instructions()
    assert "City of Long Beach" in text and "research assistant" in text
    assert "{brand_color}" not in text
    assert "Fiscal Stress Monitoring System" in text  # NY state note
    assert "{which_town}" not in text and "{jurisdiction_notes}" not in text
    assert "N.J.S.A." not in text and "Atlantic Highlands" not in text


def test_no_town_names_hardcoded_in_core_python():
    """Guard against regressions: AH-specific names belong in its profile.
    Legacy crawlers and NJ-specific financial modules are allow-listed."""
    import re
    from pathlib import Path

    api = Path(__file__).resolve().parent.parent
    allow = {"services/scraper/crawlers.py", "services/scraper/config.py", "services/event_scrapers/html_parse.py",
             "services/event_scrapers/playwright_adapter.py", "services/financial_agent.py",
             "services/financial_anomaly.py", "services/financial_extractor_v2.py",
             "services/financial_extractor.py", "services/financial_reconcile.py", "services/title_extractor.py",
             "services/FINANCIAL_PIPELINE.md"}
    pat = re.compile(r"Atlantic Highlands|ahnj\.info|Henry Hudson|HHRSD|Monmouth", re.I)
    offenders = []
    for p in list(api.glob("routes/*.py")) + list(api.glob("services/**/*.py")) + [api / "main.py", api / "config.py"]:
        rel = p.relative_to(api).as_posix()
        if rel in allow:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if pat.search(line) and not line.lstrip().startswith("#") and "noqa: locality" not in line:
                offenders.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert not offenders, "\n".join(offenders)
