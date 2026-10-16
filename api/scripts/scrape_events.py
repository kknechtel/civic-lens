"""
Scrape the borough events calendar from ahnj.com and store events in the database.
Run: python scripts/scrape_events.py
"""
import sys, os, re, json, logging, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

CALENDAR_URL = "https://www.ahnj.com/ahnj/Upcoming%20Events/"
MONTH_URL = "https://www.ahnj.com/ahnj/Upcoming%20Events/?month={month:02d}&year={year}"

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html",
}


# Trailing time on an event line: "7:00 pm", "12:00 Noon",
# "10:00pm - 2:00am". Anchored so it can't chew into the title.
_TIME_TRAILING = re.compile(
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|noon)"
    r"(?:\s*[-–—]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|noon))?)\s*$",
    re.I,
)
_TIME_ANYWHERE = re.compile(
    r"(\d{1,2}:\d{2}\s*(?:am|pm|noon)?)",
    re.I,
)


def _split_title_time(text: str) -> tuple[str, str | None]:
    """Peel a trailing time (or time range) off an event line.

    Returns (title, time). Falls back to an unanchored match so a line
    that puts the time mid-string still yields a time rather than
    burying it in the title.
    """
    m = _TIME_TRAILING.search(text)
    if m:
        return text[: m.start()].strip(" -–—·,"), m.group(1).strip()

    m = _TIME_ANYWHERE.search(text)
    if m:
        title = (text[: m.start()] + " " + text[m.end():]).strip(" -–—·,")
        return re.sub(r"\s{2,}", " ", title), m.group(1).strip()

    return text.strip(" -–—·,"), None


def scrape_month(year: int, month: int) -> list[dict]:
    """Scrape events for a specific month from the borough calendar.

    The calendar is a table of day cells. Each leaf cell holds the day
    number in a <b><a>, then one <font color="#000000"> per event inside
    a <font size="-2"> wrapper:

        <td class="cal">
          <b><a ...><font>19</font></a><br/></b>
          <font size="-2">
            <img/><font color="#000000">Mayor &amp; Council Meeting 7:00 pm</font><br/>
            <img/><font color="#000000">Hydrant Flushing 10:00pm - 2:00am</font><br/>
          </font>
        </td>

    We take one event per inner <font>. The previous approach flattened
    the cell to text and re-split it on event keywords (Festival,
    Concert, Parade, "Offices closed", …), which cut titles in half
    whenever a keyword landed mid-name — "Music and Food Truck Festival"
    became two events, and "Borough Offices Closed" left a bare
    "Borough" behind. The markup already delimits events; use it.
    """
    url = MONTH_URL.format(month=month, year=year)
    logger.info(f"Scraping {year}-{month:02d}: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    for cell in soup.find_all("td"):
        if cell.find("td"):
            continue  # layout wrapper, not a day cell

        # Day number lives in the bolded link at the top of the cell.
        header = cell.find("b")
        if not header:
            continue
        day_match = re.search(r'\b(\d{1,2})\b', header.get_text(strip=True))
        if not day_match:
            continue
        day = int(day_match.group(1))
        if day < 1 or day > 31:
            continue

        date_str = f"{year}-{month:02d}-{day:02d}"

        for wrapper in cell.find_all("font", attrs={"size": "-2"}):
            for entry in wrapper.find_all("font"):
                raw = entry.get_text(" ", strip=True)
                raw = re.sub(r'[▶►]\s*', '', raw).strip()
                if not raw or "Printed Calendar" in raw:
                    continue

                event_title, event_time = _split_title_time(raw)
                if not event_title or len(event_title) < 3:
                    continue

                events.append({
                    "date": date_str,
                    "title": event_title,
                    "time": event_time,
                    "source": "ahnj_calendar",
                    "url": url,
                })

    logger.info(f"  Found {len(events)} events for {year}-{month:02d}")
    time.sleep(1)  # Be polite
    return events


def scrape_all_months(start_year=2026, start_month=1, end_year=2026, end_month=12) -> list[dict]:
    """Scrape multiple months."""
    all_events = []
    for year in range(start_year, end_year + 1):
        for month in range(start_month if year == start_year else 1,
                           (end_month if year == end_year else 12) + 1):
            events = scrape_month(year, month)
            all_events.extend(events)
    return all_events


# Borough-calendar classification. Lives server-side so both apps consume
# the SAME bucket assignment instead of duplicating heuristics in TS.
#   - 'govt'      → council, planning, harbor commission, BOE, offices-closed
#   - 'community' → parades, fireworks, concerts, farmers markets, holidays,
#                   anything else not matching a govt keyword
_GOVT_KEYWORDS = (
    "council", "planning board", "commission",
    "board of education", "boe", "reorganization",
    "offices closed", "offices are closed",
    "borough hall", "town hall", "court",
    "zoning board", "shade tree",
    "environmental commission", "recreation commission",
)


def classify_borough_event(title: str) -> str:
    t = (title or "").lower()
    return "govt" if any(k in t for k in _GOVT_KEYWORDS) else "community"


def save_to_db(events: list[dict]):
    """Save events to the database calendar_events table."""
    from database import SessionLocal
    from sqlalchemy import text as sql_text

    db = SessionLocal()
    try:
        # Create table if not exists. Extra columns (venue/city/event_type/
        # end_time/ticket_url) added in 2026-05; the IF NOT EXISTS guards
        # make this idempotent on fresh installs, and the migration in
        # database.py picks them up on existing prod tables.
        db.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                date DATE NOT NULL,
                title TEXT NOT NULL,
                time TEXT,
                location TEXT,
                description TEXT,
                source TEXT DEFAULT 'ahnj_calendar',
                source_url TEXT,
                venue TEXT,
                city TEXT,
                event_type TEXT DEFAULT 'general',
                end_time TEXT,
                ticket_url TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.commit()

        # Insert events (upsert by date+title)
        inserted = 0
        for event in events:
            exists = db.execute(sql_text(
                "SELECT 1 FROM calendar_events WHERE date = :date AND title = :title"
            ), {"date": event["date"], "title": event["title"]}).fetchone()

            if not exists:
                db.execute(sql_text("""
                    INSERT INTO calendar_events (date, title, time, source, source_url, event_type)
                    VALUES (:date, :title, :time, :source, :url, :event_type)
                """), {
                    "date": event["date"],
                    "title": event["title"],
                    "time": event.get("time"),
                    "source": event.get("source", "ahnj_calendar"),
                    "url": event.get("url"),
                    "event_type": classify_borough_event(event["title"]),
                })
                inserted += 1

        db.commit()
        logger.info(f"Inserted {inserted} new events ({len(events)} total scraped)")
    finally:
        db.close()


def save_to_json(events: list[dict], path: str = None):
    """Save events to a JSON file."""
    if not path:
        path = str(Path(__file__).parent.parent / "storage" / "calendar_events.json")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(events, f, indent=2)
    logger.info(f"Saved {len(events)} events to {path}")


def run_events_scrape(months_ahead: int = 12) -> dict:
    """Scrape a rolling window starting at the current month and going
    `months_ahead` months forward, then persist to the calendar_events table.

    Called by `scripts/scheduled_scrape.py` on the nightly tick. Returns a
    counts dict for the caller to log; never raises so a scrape failure
    can't cascade into the doc scrape it's chained off.
    """
    try:
        now = datetime.utcnow()
        # Inclusive end month (now.month → now.month + months_ahead).
        total_end = now.month + months_ahead
        end_year = now.year + (total_end - 1) // 12
        end_month = ((total_end - 1) % 12) + 1
        events = scrape_all_months(now.year, now.month, end_year, end_month)
        save_to_db(events)
        return {"scraped": len(events), "ok": True}
    except Exception as exc:
        logger.exception("events scrape failed: %s", exc)
        return {"scraped": 0, "ok": False, "error": str(exc)}


if __name__ == "__main__":
    summary = run_events_scrape()
    print(f"\nEvents scrape: {summary}")
