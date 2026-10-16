"""Venue registry — built from the active municipality profile.

Each profile lists venues under `events.venues`:

    events:
      venues:
        - name: The Proving Ground
          city: Highlands
          adapter: html            # html | playwright | playwright_paginated | squarespace | tribe_events | bandsintown | ical
          parser: parse_proving_ground   # html/playwright adapters only: a parser in html_parse / playwright_adapter
          params: {url: https://www.theprovingground.com/events}

If a URL breaks (site redesign etc.) the runner logs and continues with the
others, so a single dead venue can't take out the whole nightly run.
"""
from . import squarespace, html_parse, bandsintown, playwright_adapter, tribe_events, ical

ADAPTERS = {
    "html": html_parse.fetch_events,
    "playwright": playwright_adapter.fetch_events,
    "playwright_paginated": playwright_adapter.fetch_events_paginated,
    "squarespace": squarespace.fetch_events,
    "tribe_events": tribe_events.fetch_events,
    "bandsintown": bandsintown.fetch_events,
    "ical": ical.fetch_events,
}


def _parser(name: str):
    for mod in (html_parse, playwright_adapter):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise ValueError(f"unknown event parser {name!r}")


def venues() -> list[tuple]:
    """[(display_name, city, adapter_callable, adapter_kwargs)] for the active profile."""
    from municipality import get_municipality

    out = []
    for v in get_municipality().events.venues:
        if not v.enabled:
            continue
        kwargs = dict(v.params)
        if v.parser:
            kwargs["parser"] = _parser(v.parser)
        out.append((v.name, v.city, ADAPTERS[v.adapter], kwargs))
    return out
