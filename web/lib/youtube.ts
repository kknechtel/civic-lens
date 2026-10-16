// Turn whatever YouTube URL we hold for a band into an embeddable one.
//
// Our links come from knownBandLinks.ts and the enrichment script, in
// every shape YouTube has ever used. Only some can be embedded:
//
//   watch?v=ID / youtu.be/ID / shorts/ID   → the video
//   embed/ID                               → already embeddable
//   playlist?list=ID                       → the playlist
//   channel/UCxxx                          → that channel's uploads,
//                                            whose playlist id is the
//                                            channel id with UC→UU
//   /@handle, /c/Name, /user/Name          → NOT embeddable; resolving a
//                                            handle to a channel id needs
//                                            the Data API and a key
//
// Returns null for the last group so callers fall back to a plain link
// rather than rendering a broken iframe.
//
// nocookie host: YouTube's privacy-enhanced domain, which doesn't set
// tracking cookies until playback. Reasonable default for a borough app
// whose visitors didn't ask to be tracked for looking at a band page.

const HOST = "https://www.youtube-nocookie.com/embed";

/** Extract a bare video id from the common URL shapes. */
function videoId(u: URL): string | null {
  if (u.hostname === "youtu.be") {
    const id = u.pathname.slice(1).split("/")[0];
    return id || null;
  }
  const v = u.searchParams.get("v");
  if (v) return v;
  const m = u.pathname.match(/^\/(?:embed|shorts|v)\/([^/?]+)/);
  return m ? m[1] : null;
}

export function youtubeEmbedUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  if (!/(^|\.)youtube\.com$|(^|\.)youtu\.be$|(^|\.)youtube-nocookie\.com$/.test(u.hostname)) {
    return null;
  }

  const id = videoId(u);
  if (id) return `${HOST}/${encodeURIComponent(id)}`;

  const list = u.searchParams.get("list");
  if (list) return `${HOST}/videoseries?list=${encodeURIComponent(list)}`;

  // A channel's uploads live in a playlist whose id is the channel id
  // with the UC prefix swapped for UU. Only works for /channel/UC… URLs —
  // @handles and /user/ names can't be converted without an API lookup.
  const chan = u.pathname.match(/^\/channel\/(UC[^/?]+)/);
  if (chan) return `${HOST}/videoseries?list=UU${chan[1].slice(2)}`;

  return null;
}
