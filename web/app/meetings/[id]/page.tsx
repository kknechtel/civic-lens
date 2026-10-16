"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/app/contexts/AuthContext";
import {
  ArrowLeft, Loader2, Mic, Youtube, Play, FileText, Sparkles,
  RefreshCw, AlertCircle, CheckCircle2, Clock, ChevronDown, ChevronRight,
} from "lucide-react";
import {
  getMeeting, transcribeMeeting, summarizeMeeting,
  type MeetingDetail, type TranscriptSegment,
} from "@/lib/meetingsApi";

const brandColor = "#385854";

function fmtTime(secs: number): string {
  if (!isFinite(secs) || secs < 0) return "0:00";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return h > 0
    ? `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
    : `${m}:${s.toString().padStart(2, "0")}`;
}

// In-progress statuses that should trigger polling rather than user action.
const POLL_STATUSES = new Set(["transcribing", "summarizing"]);

export default function MeetingDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const { user, loading: authLoading } = useAuth();
  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"transcribe" | "summarize" | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);
  const ytPlayerRef = useRef<YT.Player | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const m = await getMeeting(params.id);
      setMeeting(m);
      return m;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load meeting");
      return null;
    }
  }, [params.id]);

  // Initial load + polling while a background job is running.
  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.push("/"); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      const m = await refresh();
      if (cancelled) return;
      if (m && POLL_STATUSES.has(m.status)) {
        timer = setTimeout(tick, 4000);
      }
    };
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [user, authLoading, router, refresh]);

  // YouTube IFrame API loader — needed so we can seekTo() on transcript clicks.
  useEffect(() => {
    if (!meeting || meeting.platform !== "youtube" || !meeting.youtube_id) return;
    let mounted = true;
    const ensureAPI = () =>
      new Promise<void>((resolve) => {
        if (window.YT && window.YT.Player) return resolve();
        const tag = document.createElement("script");
        tag.src = "https://www.youtube.com/iframe_api";
        document.body.appendChild(tag);
        (window as unknown as { onYouTubeIframeAPIReady: () => void }).onYouTubeIframeAPIReady = () => resolve();
      });

    ensureAPI().then(() => {
      if (!mounted) return;
      ytPlayerRef.current = new window.YT.Player(`yt-player-${meeting.id}`, {
        videoId: meeting.youtube_id!,
        playerVars: { rel: 0, modestbranding: 1 },
      });
    });

    return () => { mounted = false; ytPlayerRef.current?.destroy?.(); ytPlayerRef.current = null; };
  }, [meeting?.id, meeting?.platform, meeting?.youtube_id, meeting]);

  // Poll the audio/YouTube player's current time so we can highlight the
  // active transcript segment as playback advances.
  useEffect(() => {
    if (!meeting) return;
    let raf = 0;
    const sample = () => {
      if (meeting.platform === "audio") {
        if (audioRef.current) setCurrentTime(audioRef.current.currentTime);
      } else if (ytPlayerRef.current && typeof ytPlayerRef.current.getCurrentTime === "function") {
        try { setCurrentTime(ytPlayerRef.current.getCurrentTime() || 0); } catch { /* not ready */ }
      }
      raf = window.setTimeout(sample as unknown as TimerHandler, 500) as unknown as number;
    };
    sample();
    return () => { if (raf) window.clearTimeout(raf); };
  }, [meeting]);

  const seekTo = useCallback((seconds: number) => {
    if (!meeting) return;
    if (meeting.platform === "audio" && audioRef.current) {
      audioRef.current.currentTime = seconds;
      void audioRef.current.play();
    } else if (meeting.platform === "youtube" && ytPlayerRef.current) {
      ytPlayerRef.current.seekTo(seconds, true);
      ytPlayerRef.current.playVideo?.();
    }
  }, [meeting]);

  const handleTranscribe = async () => {
    if (!meeting) return;
    setBusy("transcribe");
    try {
      await transcribeMeeting(meeting.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transcribe failed");
    } finally {
      setBusy(null);
    }
  };

  const handleSummarize = async () => {
    if (!meeting) return;
    setBusy("summarize");
    try {
      await summarizeMeeting(meeting.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Summarize failed");
    } finally {
      setBusy(null);
    }
  };

  if (authLoading || !user || !meeting) {
    // If the fetch failed, show the error here — without this, an API
    // failure leaves the page stuck on "Loading meeting…" forever because
    // `meeting` never becomes truthy.
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-3">
        <Link href="/meetings" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="w-4 h-4" /> All meetings
        </Link>
        {error ? (
          <div className="p-4 rounded bg-red-50 border border-red-200 text-red-800 text-sm">
            <div className="flex items-center gap-2 font-medium mb-1">
              <AlertCircle className="w-4 h-4" /> Couldn&apos;t load meeting
            </div>
            <div className="font-mono text-xs break-all">{error}</div>
            <button
              onClick={() => { setError(null); refresh(); }}
              className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-white border border-red-300 text-red-700 text-xs hover:bg-red-50"
            >
              <RefreshCw className="w-3 h-3" /> Retry
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading meeting…
          </div>
        )}
      </div>
    );
  }

  const segments: TranscriptSegment[] = meeting.transcript?.segments || [];
  const activeIdx = segments.findIndex(
    (s, i) => currentTime >= s.start && (i === segments.length - 1 || currentTime < segments[i + 1].start)
  );

  return (
    <div className="max-w-7xl mx-auto p-6">
      <Link href="/meetings" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-3">
        <ArrowLeft className="w-4 h-4" /> All meetings
      </Link>

      <div className="mb-4">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          {meeting.platform === "youtube" ? <Youtube className="w-5 h-5 text-red-600" /> : <Mic className="w-5 h-5 text-gray-600" />}
          {meeting.title}
        </h1>
        <div className="text-sm text-gray-500 mt-1">
          {meeting.meeting_body}
          {meeting.meeting_date && ` · ${meeting.meeting_date}`}
          {meeting.transcript?.duration_seconds ? ` · ${fmtTime(meeting.transcript.duration_seconds)}` : ""}
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded bg-red-50 text-red-800 text-sm flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Left: player + transcript ───────────────────────── */}
        <div className="lg:col-span-2 space-y-4">
          <div className="bg-black rounded-lg overflow-hidden">
            {meeting.platform === "youtube" && meeting.youtube_id ? (
              <div className="aspect-video w-full">
                <div id={`yt-player-${meeting.id}`} className="w-full h-full" />
              </div>
            ) : meeting.audio_url ? (
              <audio
                ref={audioRef}
                src={meeting.audio_url}
                controls
                preload="metadata"
                className="w-full bg-black"
              />
            ) : (
              <div className="aspect-video flex items-center justify-center text-gray-400 text-sm">
                No playback URL available
              </div>
            )}
          </div>

          {/* Transcribe controls */}
          <div className="flex items-center gap-2 flex-wrap">
            {meeting.transcript ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-700">
                <CheckCircle2 className="w-4 h-4" /> Transcribed ({meeting.transcript.engine}, {segments.length} segments)
              </span>
            ) : meeting.status === "transcribing" ? (
              <span className="inline-flex items-center gap-1 text-sm text-amber-700">
                <Loader2 className="w-4 h-4 animate-spin" /> Transcribing… this can take a while
              </span>
            ) : (
              <button
                onClick={handleTranscribe}
                disabled={busy === "transcribe"}
                className="px-3 py-1.5 rounded text-sm text-white shadow inline-flex items-center gap-1.5 disabled:opacity-50"
                style={{ backgroundColor: brandColor }}
              >
                {busy === "transcribe" ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                Transcribe meeting
              </button>
            )}
            {meeting.transcript && !meeting.summary && (
              meeting.status === "summarizing" ? (
                <span className="inline-flex items-center gap-1 text-sm text-amber-700">
                  <Loader2 className="w-4 h-4 animate-spin" /> Summarizing…
                </span>
              ) : (
                <button
                  onClick={handleSummarize}
                  disabled={busy === "summarize"}
                  className="px-3 py-1.5 rounded text-sm text-white shadow inline-flex items-center gap-1.5 disabled:opacity-50 bg-purple-600 hover:bg-purple-700"
                >
                  {busy === "summarize" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  Generate AI summary
                </button>
              )
            )}
            {meeting.transcript && (
              <button
                onClick={handleTranscribe}
                className="px-2 py-1.5 rounded text-xs text-gray-600 hover:bg-gray-100 inline-flex items-center gap-1"
                title="Re-transcribe (forces fresh run)"
              >
                <RefreshCw className="w-3 h-3" /> Re-transcribe
              </button>
            )}
          </div>

          {/* Transcript */}
          <div className="bg-white border border-gray-200 rounded-lg">
            <div className="p-3 border-b border-gray-100 text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Transcript
            </div>
            <div
              ref={transcriptRef}
              className="max-h-[600px] overflow-y-auto p-2 text-sm"
            >
              {segments.length === 0 ? (
                <div className="p-6 text-center text-gray-400 text-sm">
                  {meeting.transcript ? "Transcript is empty." : "Transcribe this meeting to see the transcript."}
                </div>
              ) : (
                segments.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => seekTo(s.start)}
                    className={`flex items-start gap-2 w-full text-left px-2 py-1.5 rounded hover:bg-gray-100 transition ${
                      i === activeIdx ? "bg-amber-50" : ""
                    }`}
                  >
                    <span className="text-xs text-gray-400 font-mono shrink-0 w-14 pt-0.5">
                      {fmtTime(s.start)}
                    </span>
                    <span className="text-gray-800 leading-relaxed">{s.text}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        {/* ── Right: summary panel ───────────────────────────── */}
        <aside className="space-y-4">
          {meeting.summary ? (
            <SummaryPanel summary={meeting.summary} onSeek={seekTo} />
          ) : (
            <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm text-gray-500">
              <Sparkles className="w-4 h-4 inline mr-1 text-gray-400" />
              An AI summary will appear here once you generate one. Includes TL;DR,
              decisions, votes, action items, and clickable topic jumps.
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function CollapsibleSection({
  title, icon, count, defaultOpen = false, children,
}: {
  title: string;
  icon?: React.ReactNode;
  /** Shown in a small badge next to the title — usually the item count. */
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-gray-50 transition"
      >
        {open ? (
          <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />
        )}
        {icon}
        <h2 className="text-xs uppercase tracking-wide font-semibold text-gray-700 flex-1">
          {title}
        </h2>
        {typeof count === "number" && (
          <span className="text-xs text-gray-500 bg-gray-100 rounded-full px-2 py-0.5 font-mono">
            {count}
          </span>
        )}
      </button>
      {open && <div className="px-4 pb-4 pt-1 border-t border-gray-100">{children}</div>}
    </section>
  );
}


function SummaryPanel({
  summary, onSeek,
}: {
  summary: NonNullable<MeetingDetail["summary"]>;
  onSeek: (s: number) => void;
}) {
  // TL;DR stays open; everything else collapsed by default so the panel
  // is glanceable on small screens. Counts in the headers show what's
  // inside each section without expanding.
  return (
    <div className="space-y-2">
      <CollapsibleSection title="TL;DR" defaultOpen>
        <p className="text-sm text-gray-800 leading-relaxed">{summary.tldr}</p>
      </CollapsibleSection>

      {summary.topics?.length > 0 && (
        <CollapsibleSection
          title="Topics"
          icon={<Clock className="w-3.5 h-3.5 text-gray-400" />}
          count={summary.topics.length}
        >
          <ul className="space-y-2">
            {summary.topics.map((t, i) => (
              <li key={i}>
                <button onClick={() => onSeek(t.start_seconds)} className="text-left w-full hover:bg-gray-50 rounded p-1.5">
                  <div className="text-sm font-medium text-gray-900 flex items-center gap-1.5">
                    <Play className="w-3 h-3 text-gray-400" /> {t.title}
                    <span className="text-xs text-gray-400 font-mono">{fmtTime(t.start_seconds)}</span>
                  </div>
                  <div className="text-xs text-gray-600 mt-0.5 ml-4">{t.summary}</div>
                </button>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {summary.decisions?.length > 0 && (
        <CollapsibleSection title="Decisions" count={summary.decisions.length}>
          <ul className="space-y-1.5 text-sm">
            {summary.decisions.map((d, i) => (
              <li key={i} className="flex items-start gap-2">
                <button
                  onClick={() => onSeek(d.timestamp_seconds)}
                  className="text-xs text-gray-400 font-mono hover:text-gray-700 shrink-0 mt-0.5"
                >
                  {fmtTime(d.timestamp_seconds)}
                </button>
                <span className="text-gray-800">
                  {d.description}
                  {d.vote && <span className="ml-1 text-xs text-gray-500">({d.vote})</span>}
                </span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {summary.action_items?.length > 0 && (
        <CollapsibleSection title="Action items" count={summary.action_items.length}>
          <ul className="space-y-1.5 text-sm">
            {summary.action_items.map((a, i) => (
              <li key={i} className="text-gray-800">
                {a.description}
                {a.owner && <span className="text-xs text-gray-500 ml-1">— {a.owner}</span>}
                {a.due && <span className="text-xs text-gray-500 ml-1">(due {a.due})</span>}
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {summary.ordinances_resolutions?.length > 0 && (
        <CollapsibleSection
          title="Ordinances / resolutions"
          count={summary.ordinances_resolutions.length}
        >
          <ul className="space-y-1.5 text-sm">
            {summary.ordinances_resolutions.map((o, i) => (
              <li key={i} className="text-gray-800">
                <span className="font-mono text-xs text-gray-500">{o.number}</span> {o.description}
                <span className="text-xs text-gray-500 ml-1">({o.outcome})</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {summary.public_comments?.length > 0 && (
        <CollapsibleSection title="Public comments" count={summary.public_comments.length}>
          <ul className="space-y-1.5 text-sm">
            {summary.public_comments.map((c, i) => (
              <li key={i} className="flex items-start gap-2">
                <button
                  onClick={() => onSeek(c.timestamp_seconds)}
                  className="text-xs text-gray-400 font-mono hover:text-gray-700 shrink-0 mt-0.5"
                >
                  {fmtTime(c.timestamp_seconds)}
                </button>
                <span className="text-gray-800">
                  <span className="font-medium">{c.speaker}:</span> {c.topic}
                </span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}
    </div>
  );
}
