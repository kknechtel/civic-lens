"""Runner — iterates the venue registry, isolates per-venue failures,
persists results to calendar_events with event_type='live_music'.

Dedupe: existing scrape_events.save_to_db uses (date, title) as the
upsert key. That collides if two venues both have a "Live Music"
generic-titled event on the same night — so the dedupe key here
incorporates the venue name. We hand-roll the insert below to keep
the writer simple and explicit.

On a key hit we refresh time/end_time/ticket_url/description instead of
skipping, so set-time changes and parser fixes reach rows that already
exist. Identity (date/venue/title) is never rewritten — a changed title
is a different event, not an edit.
"""
from __future__ import annotations

import logging

from sqlalchemy import text as sql_text

from .registry import venues

logger = logging.getLogger(__name__)


def _save(events: list[dict]) -> int:
    """Upsert by (date, venue, title). Returns count of newly inserted rows."""
    if not events:
        return 0

    from database import SessionLocal

    inserted = 0
    updated = 0
    db = SessionLocal()
    try:
        for ev in events:
            try:
                row = db.execute(sql_text(
                    "SELECT id, time, end_time, ticket_url, description "
                    "FROM calendar_events "
                    "WHERE date = :date AND venue = :venue AND title = :title"
                ), {"date": ev["date"], "venue": ev["venue"], "title": ev["title"]}).fetchone()

                if row:
                    # Refresh the mutable details rather than skipping. Venues
                    # move set times, and a parser fix (like the time-range
                    # repair that turned Sandbox's "6-9pm" from a bare "6"
                    # into 6:00 PM–9:00 PM) only reaches existing rows if we
                    # write it here. Never blank a value we already have with
                    # a null — a venue dropping the time from its page
                    # shouldn't erase a good one.
                    changes = {}
                    for field, key in (("time", "time"), ("end_time", "end_time"),
                                       ("ticket_url", "ticket_url"), ("description", "description")):
                        new = ev.get(key)
                        if new and new != getattr(row, field):
                            changes[field] = new
                    if changes:
                        sets = ", ".join(f"{f} = :{f}" for f in changes)
                        db.execute(sql_text(
                            f"UPDATE calendar_events SET {sets} WHERE id = :id"
                        ), {**changes, "id": row.id})
                        updated += 1
                    continue

                db.execute(sql_text("""
                    INSERT INTO calendar_events
                        (date, title, time, end_time, location, description,
                         source, source_url, venue, city, event_type, ticket_url)
                    VALUES
                        (:date, :title, :time, :end_time, :location, :description,
                         :source, :url, :venue, :city, :event_type, :ticket_url)
                """), {
                    "date": ev["date"],
                    "title": ev["title"],
                    "time": ev.get("time"),
                    "end_time": ev.get("end_time"),
                    "location": ev.get("location"),
                    "description": ev.get("description"),
                    "source": ev.get("source", "music_scraper"),
                    "url": ev.get("url"),
                    "venue": ev.get("venue"),
                    "city": ev.get("city"),
                    "event_type": ev.get("event_type", "live_music"),
                    "ticket_url": ev.get("ticket_url"),
                })
                inserted += 1
            except Exception as exc:
                # Per-row failure — log and keep going so one bad row doesn't
                # drop the whole batch.
                logger.warning("music event insert skipped: %s (event=%r)", exc, ev.get("title"))
                continue

        db.commit()
    finally:
        db.close()

    if updated:
        logger.info("music scrape refreshed details on %d existing events", updated)
    return inserted


def run_music_scrape() -> dict:
    """Fetch from every registered venue, persist new events. Returns a
    counts dict suitable for logging by scheduled_scrape.py.

    Never raises — adapter failures are isolated and reported in the
    `venues` field so the operator can see which venues are healthy.
    """
    per_venue: list[dict] = []
    total_scraped = 0
    total_inserted = 0

    for name, city, adapter, kwargs in venues():
        try:
            events = adapter(venue_name=name, city=city, **kwargs)
        except Exception as exc:
            logger.exception("[%s] adapter raised: %s", name, exc)
            per_venue.append({"venue": name, "city": city, "ok": False,
                              "scraped": 0, "inserted": 0, "error": str(exc)})
            continue

        try:
            n_inserted = _save(events)
        except Exception as exc:
            logger.exception("[%s] save failed: %s", name, exc)
            per_venue.append({"venue": name, "city": city, "ok": False,
                              "scraped": len(events), "inserted": 0,
                              "error": str(exc)})
            continue

        per_venue.append({"venue": name, "city": city, "ok": True,
                          "scraped": len(events), "inserted": n_inserted})
        total_scraped += len(events)
        total_inserted += n_inserted

    return {
        "venues": per_venue,
        "scraped_total": total_scraped,
        "inserted_total": total_inserted,
        "venues_ok": sum(1 for v in per_venue if v["ok"]),
        "venues_failed": sum(1 for v in per_venue if not v["ok"]),
    }
