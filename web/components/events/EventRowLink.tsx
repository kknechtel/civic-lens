"use client";

// Stretched-link plumbing for event rows.
//
// A row needs three destinations — the act, the venue, and the event —
// but HTML forbids nesting <a> inside <a>, so the row can't simply be
// wrapped in a Link. Instead the row is a positioned container with an
// absolutely-filled link behind everything (the catch-all to the event
// detail page), and the act/venue links sit above it on the z-axis.
//
// Usage:
//   <div className="relative flex ...">
//     <RowLink href={eventHref(ev.id)} label={ev.title} />
//     <InlineLink href={bandHref(ev)}>{ev.title}</InlineLink>
//     <InlineLink href={venueHref(ev)}>{ev.venue}</InlineLink>
//   </div>

import Link from "next/link";
import type { ReactNode } from "react";

/** The row-filling catch-all. Renders behind the content, so any click
 *  that doesn't land on an InlineLink opens the event.
 *
 *  `label` is read by screen readers and shown on hover — the overlay has
 *  no text of its own, so without it the row is an unlabelled link. */
export function RowLink({ href, label }: { href: string; label?: string }) {
  return (
    <Link href={href} className="absolute inset-0 z-0" title={label ? `${label} — details` : "Event details"}>
      <span className="sr-only">{label ? `${label} details` : "Event details"}</span>
    </Link>
  );
}

/** A link that sits above the row overlay. Renders a plain <span> when
 *  `href` is null so callers don't have to branch — non-music titles and
 *  venue-less events just aren't clickable. */
export function InlineLink({
  href,
  className,
  style,
  children,
}: {
  href: string | null;
  className?: string;
  style?: React.CSSProperties;
  children: ReactNode;
}) {
  if (!href) {
    return <span className={className} style={style}>{children}</span>;
  }
  return (
    <Link
      href={href}
      className={`relative z-10 hover:underline ${className || ""}`}
      style={style}
    >
      {children}
    </Link>
  );
}

/** Wrapper for controls that must stay clickable above the overlay
 *  (download buttons, RSVP pills) without being links themselves. */
export function AboveRow({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={`relative z-10 ${className || ""}`}>{children}</div>;
}
