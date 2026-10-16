// Where the parts of an event row point.
//
// One row carries three destinations: the act, the venue, and the event
// itself. Keeping the rules here means /calendar, /search, /my-calendar
// and the home page can't drift apart on which text is clickable.
//
// HTML forbids nested anchors, so a row can't be one big <Link> with more
// links inside it. Rows use the stretched-link pattern instead — see
// components/events/EventRowLink.tsx.

// Structurally typed rather than Pick<CalendarEvent, …>: SavedEvent (from
// /my-calendar) carries the same fields with looser types — `event_type`
// is a plain string there — and these only ever compare or encode them.

/** The event's own detail page — the row's catch-all destination. */
export function eventHref(id: string): string {
  return `/calendar/${id}`;
}

/** The act's cross-venue page. Null for non-music events, whose titles
 *  ("Planning Board Meeting") are not acts and would land on a band page
 *  with nothing on it. */
export function bandHref(ev: {
  title?: string | null;
  event_type?: string | null;
}): string | null {
  if (ev.event_type !== "live_music") return null;
  const name = (ev.title || "").trim();
  if (!name) return null;
  return `/bands/${encodeURIComponent(name)}`;
}

/** The venue's page. Only scraped music events carry a real `venue`;
 *  borough rows put a free-text address in `location` instead, which
 *  isn't a venue we list, so those get no link. */
export function venueHref(ev: { venue?: string | null }): string | null {
  const name = (ev.venue || "").trim();
  if (!name) return null;
  return `/venues/${encodeURIComponent(name)}`;
}
