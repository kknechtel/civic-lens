// Build "Add to Calendar" deep-links for Google, Outlook, Apple/.ics.
//
// Each provider has its own URL/format. We try to parse the event's
// optional time string ("8:00 PM", "8:00 PM - 11:59 PM", "6-9pm") into a
// real start + end datetime in venue-local time. When no time is set,
// we treat it as an all-day event.
//
// All providers below take WALL-CLOCK times without a timezone — the
// venue is in America/New_York and the user is almost certainly in
// the same TZ. We deliberately don't try to UTC-convert because the
// venue and the user agree on what "8pm" means; UTC conversion would
// just confuse cross-DST display.

export interface EventForCalendar {
  title: string;
  /** YYYY-MM-DD */
  date: string;
  /** Free-form start time like "8:00 PM" or "8pm". May be null. */
  time?: string | null;
  /** Free-form end time like "11:59 PM". May be null. */
  end_time?: string | null;
  venue?: string | null;
  city?: string | null;
  description?: string | null;
  ticket_url?: string | null;
}

// "8:00 PM" / "8pm" / "8:30 AM" / "20:00" → {hh, mm}
// Returns null when we can't parse.
function parseClockTime(s: string | null | undefined): { hh: number; mm: number } | null {
  if (!s) return null;
  const m = s.trim().match(/^(\d{1,2})(?::(\d{2}))?\s*([APap][Mm])?$/);
  if (!m) return null;
  let hh = parseInt(m[1], 10);
  const mm = m[2] ? parseInt(m[2], 10) : 0;
  const ampm = m[3]?.toUpperCase();
  if (ampm === "PM" && hh < 12) hh += 12;
  if (ampm === "AM" && hh === 12) hh = 0;
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;
  return { hh, mm };
}

// Trim "8:00 PM - 11:59 PM" or "6-9pm" to its start half — when the
// scraper packed end-time into the start string instead of end_time.
function startHalf(s: string | null | undefined): string | null {
  if (!s) return null;
  return s.split(/\s*[-–—]\s*/, 2)[0]?.trim() || null;
}

/** Returns local-time Date objects for start + end of the event. */
function eventStartEnd(ev: EventForCalendar): { start: Date; end: Date; allDay: boolean } {
  const [y, m, d] = ev.date.split("-").map(n => parseInt(n, 10));
  const t = parseClockTime(startHalf(ev.time));
  if (!t) {
    // All-day event — full local day at 00:00..23:59
    return {
      start: new Date(y, (m || 1) - 1, d || 1, 0, 0, 0),
      end:   new Date(y, (m || 1) - 1, d || 1, 23, 59, 0),
      allDay: true,
    };
  }
  const start = new Date(y, (m || 1) - 1, d || 1, t.hh, t.mm, 0);
  // End: explicit end_time, OR start + 2h (typical bar gig length)
  const endT = parseClockTime(startHalf(ev.end_time));
  let end: Date;
  if (endT) {
    end = new Date(y, (m || 1) - 1, d || 1, endT.hh, endT.mm, 0);
    if (end <= start) end = new Date(end.getTime() + 24 * 60 * 60 * 1000); // crossed midnight
  } else {
    end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  }
  return { start, end, allDay: false };
}

// "2026-05-24T20:00:00" — local wall-clock without TZ suffix
function toLocalIsoNoTz(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00`;
}

// Compact YYYYMMDDTHHmmSS used by Google Calendar (no separators)
function toCompactNoTz(d: Date): string {
  return toLocalIsoNoTz(d).replace(/[-:]/g, "");
}

function locationOf(ev: EventForCalendar): string {
  return [ev.venue, ev.city].filter(Boolean).join(", ");
}

function descriptionOf(ev: EventForCalendar): string {
  const parts: string[] = [];
  if (ev.description) parts.push(ev.description);
  if (ev.ticket_url) parts.push(`Info: ${ev.ticket_url}`);
  if (typeof window !== "undefined") parts.push(`Via ${window.location.host}`);
  return parts.join("\n\n");
}

/** Google Calendar event-create URL. Opens prefilled "Save event" UI. */
export function googleCalendarUrl(ev: EventForCalendar): string {
  const { start, end, allDay } = eventStartEnd(ev);
  // Google's date param format:
  //   timed: 20260524T200000/20260524T230000
  //   all-day: 20260524/20260525   (end is exclusive)
  const dates = allDay
    ? `${toCompactNoTz(start).slice(0, 8)}/${toCompactNoTz(new Date(start.getTime() + 24 * 60 * 60 * 1000)).slice(0, 8)}`
    : `${toCompactNoTz(start)}/${toCompactNoTz(end)}`;
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: ev.title,
    dates,
    location: locationOf(ev),
    details: descriptionOf(ev),
  });
  return `https://calendar.google.com/calendar/r/eventedit?${params.toString()}`;
}

/** Outlook (web) deep-link to compose a new event. */
export function outlookCalendarUrl(ev: EventForCalendar): string {
  const { start, end, allDay } = eventStartEnd(ev);
  const params = new URLSearchParams({
    path: "/calendar/action/compose",
    rru: "addevent",
    subject: ev.title,
    startdt: toLocalIsoNoTz(start),
    enddt: toLocalIsoNoTz(end),
    location: locationOf(ev),
    body: descriptionOf(ev),
    allday: allDay ? "true" : "false",
  });
  return `https://outlook.live.com/calendar/0/deeplink/compose?${params.toString()}`;
}

/** Yahoo Calendar URL — bonus, since the param shape is similar enough. */
export function yahooCalendarUrl(ev: EventForCalendar): string {
  const { start, end, allDay } = eventStartEnd(ev);
  const params = new URLSearchParams({
    v: "60",
    title: ev.title,
    st: toCompactNoTz(start),
    et: toCompactNoTz(end),
    in_loc: locationOf(ev),
    desc: descriptionOf(ev),
  });
  if (allDay) params.set("dur", "allday");
  return `https://calendar.yahoo.com/?${params.toString()}`;
}

/** RFC 5545 .ics blob. Browser downloads it; Apple Calendar / Fantastical /
 *  Outlook desktop pick it up. */
export function buildIcs(ev: EventForCalendar): string {
  const { start, end, allDay } = eventStartEnd(ev);
  const uid = `${ev.date}-${ev.title.toLowerCase().replace(/\s+/g, "-")}@civic-lens`;
  const esc = (s: string) =>
    s.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n");
  const dtFmt = (d: Date) =>
    allDay
      ? `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`
      : toCompactNoTz(d);
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Around Town AHNJ//EN",
    "BEGIN:VEVENT",
    `UID:${uid}`,
    `DTSTAMP:${toCompactNoTz(new Date())}`,
    allDay ? `DTSTART;VALUE=DATE:${dtFmt(start)}` : `DTSTART:${dtFmt(start)}`,
    allDay
      ? `DTEND;VALUE=DATE:${dtFmt(new Date(start.getTime() + 24 * 60 * 60 * 1000))}`
      : `DTEND:${dtFmt(end)}`,
    `SUMMARY:${esc(ev.title)}`,
    `LOCATION:${esc(locationOf(ev))}`,
    `DESCRIPTION:${esc(descriptionOf(ev))}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ];
  return lines.join("\r\n");
}

/** Trigger an .ics file download in the browser. */
export function downloadIcs(ev: EventForCalendar): void {
  const blob = new Blob([buildIcs(ev)], { type: "text/calendar" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${ev.date}-${ev.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase().slice(0, 40)}.ics`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
