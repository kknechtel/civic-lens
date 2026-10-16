"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/contexts/AuthContext";
import { Loader2, Mic, Youtube, Calendar, FileText, Sparkles } from "lucide-react";
import { listMeetings, type MeetingListItem } from "@/lib/meetingsApi";
import { useMunicipality, entityOfKind } from "@/app/contexts/MunicipalityContext";

const brandColor = "#385854";


function formatDuration(secs: number | null | undefined): string {
  if (!secs || !isFinite(secs) || secs <= 0) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

type PlatformTab = "all" | "youtube" | "audio";

const TABS: { key: PlatformTab; label: string; description: string }[] = [
  { key: "all", label: "All recordings", description: "Video and audio combined" },
  { key: "youtube", label: "Videos", description: "Meetings published on YouTube" },
  { key: "audio", label: "Audio only", description: "Audio recordings posted by the municipality" },
];

export default function MeetingsPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [meetings, setMeetings] = useState<MeetingListItem[] | null>(null);
  const [platformTab, setPlatformTab] = useState<PlatformTab>("all");
  const [bodyFilter, setBodyFilter] = useState("");
  // Body chips come from the data (every municipality names its boards
  // differently). Remember bodies seen on unfiltered loads so the chips
  // don't disappear once one is selected.
  const [knownBodies, setKnownBodies] = useState<string[]>([]);
  const municipality = useMunicipality();
  const BODY_PRESETS = useMemo(
    () => [{ key: "", label: "All bodies" }, ...knownBodies.map((b) => ({ key: b, label: b }))],
    [knownBodies],
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.push("/"); return; }
    let cancelled = false;
    setMeetings(null);
    (async () => {
      try {
        const list = await listMeetings({
          body: bodyFilter || undefined,
          platform: platformTab === "all" ? undefined : platformTab,
        });
        if (!cancelled) {
          setMeetings(list);
          if (!bodyFilter) {
            const bodies = Array.from(new Set(list.map((m) => m.meeting_body).filter(Boolean))).sort();
            setKnownBodies((prev) => Array.from(new Set([...prev, ...bodies])).sort());
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      }
    })();
    return () => { cancelled = true; };
  }, [user, authLoading, router, bodyFilter, platformTab]);

  const grouped = useMemo(() => {
    if (!meetings) return [];
    const map = new Map<string, MeetingListItem[]>();
    for (const m of meetings) {
      const key = m.meeting_body || "Unknown";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(m);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [meetings]);

  if (authLoading || !user || meetings === null) {
    return (
      <div className="p-6 flex items-center gap-2 text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading meetings…
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Meeting recordings</h1>
          <p className="text-sm text-gray-500">
            {municipality.short_name || "Public"} meetings. Transcribe to enable search and AI summaries.
          </p>
        </div>
      </div>

      {/* Platform tabs: separates video meetings (YouTube) from audio-only
          recordings posted on the municipal site. */}
      <div className="border-b border-gray-200 mb-4">
        <nav className="flex gap-1" aria-label="Recording type">
          {TABS.map((t) => {
            const active = platformTab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => { setPlatformTab(t.key); setBodyFilter(""); }}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                  active
                    ? "text-gray-900"
                    : "text-gray-500 border-transparent hover:text-gray-700 hover:border-gray-300"
                }`}
                style={active ? { borderColor: brandColor, color: brandColor } : {}}
                title={t.description}
              >
                {t.label === "Videos" && <Youtube className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />}
                {t.label === "Audio only" && <Mic className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />}
                {t.label}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {BODY_PRESETS.map((b) => (
          <button
            key={b.key}
            onClick={() => setBodyFilter(b.key)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition ${
              bodyFilter === b.key
                ? "text-white border-transparent shadow"
                : "text-gray-700 bg-white border-gray-200 hover:bg-gray-50"
            }`}
            style={bodyFilter === b.key ? { backgroundColor: brandColor } : {}}
          >
            {b.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 p-3 rounded bg-red-50 text-red-800 text-sm">{error}</div>
      )}

      {meetings.length === 0 && (
        <div className="rounded border border-dashed border-gray-300 p-8 text-center text-gray-500 text-sm">
          No recordings ingested yet. Run the scraper to pull in the latest recordings.
        </div>
      )}

      {grouped.map(([bodyName, items]) => (
        <section key={bodyName} className="mb-8">
          <h2 className="text-sm uppercase tracking-wide font-semibold text-gray-500 mb-2 flex items-center gap-2">
            {items[0]?.platform === "youtube" ? <Youtube className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            {bodyName}
            <span className="text-gray-400 font-normal normal-case">({items.length})</span>
          </h2>
          <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
            {items.map((m) => (
              <Link
                key={m.id}
                href={`/meetings/${m.id}`}
                className="flex items-center gap-4 p-3 hover:bg-gray-50 transition"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-900 truncate">
                    {m.title}
                  </div>
                  <div className="text-xs text-gray-500 flex items-center gap-3 mt-0.5">
                    {m.meeting_date && (
                      <span className="inline-flex items-center gap-1">
                        <Calendar className="w-3 h-3" /> {m.meeting_date}
                      </span>
                    )}
                    <span>{formatDuration(m.duration_seconds)}</span>
                    {m.has_transcript && (
                      <span className="inline-flex items-center gap-1 text-emerald-700">
                        <FileText className="w-3 h-3" /> transcript
                      </span>
                    )}
                    {m.has_summary && (
                      <span className="inline-flex items-center gap-1 text-purple-700">
                        <Sparkles className="w-3 h-3" /> summary
                      </span>
                    )}
                    {m.status === "transcribing" && (
                      <span className="text-amber-700">transcribing…</span>
                    )}
                    {m.status === "transcription_failed" && (
                      <span className="text-red-700">transcription failed</span>
                    )}
                  </div>
                </div>
                <div className="text-xs text-gray-400 shrink-0">
                  {m.platform === "youtube" ? "YouTube" : "Audio"}
                </div>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
