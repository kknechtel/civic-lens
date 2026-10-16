"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDocuments, getDocumentViewUrl, getCalendarEvents, type Document, type CalendarEvent } from "@/lib/api";
import FilePreviewModal from "@/components/FilePreviewModal";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DocumentTextIcon,
  EyeIcon,
  CalendarDaysIcon,
  ArrowDownTrayIcon,
} from "@heroicons/react/24/outline";

const brandColor = "#385854";

export default function CalendarPage() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [viewerDoc, setViewerDoc] = useState<{ url: string; filename: string } | null>(null);

  const { data: documents } = useQuery({
    queryKey: ["calendar-docs-all"],
    queryFn: () => getDocuments(),
  });

  // Borough calendar — govt meetings only. Concerts, fireworks, and other
  // community events live on events.ahnj.info, not the civic-research calendar.
  const { data: boroughEvents } = useQuery({
    queryKey: ["calendar-borough-events", "govt"],
    queryFn: () => getCalendarEvents(undefined, undefined, { event_type: "govt" }),
  });

  // Merge borough events into a map keyed by date. Belt-and-suspenders
  // client-side filter: even if the API surfaces a non-govt row by mistake
  // (legacy 'general' rows the backfill hasn't reclassified), drop it here.
  const boroughEventsByDate = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const ev of boroughEvents || []) {
      if (ev.event_type && ev.event_type !== "govt") continue;
      if (!map.has(ev.date)) map.set(ev.date, []);
      map.get(ev.date)!.push(ev);
    }
    return map;
  }, [boroughEvents]);

  // Parse dates from document notes + filenames
  const events = useMemo(() => {
    if (!documents) return new Map<string, Document[]>();
    const map = new Map<string, Document[]>();
    const seen = new Set<string>(); // dedupe by doc id

    for (const doc of documents) {
      if (seen.has(doc.id)) continue;
      const dateStr = extractDateFromDoc(doc);
      if (dateStr) {
        seen.add(doc.id);
        if (!map.has(dateStr)) map.set(dateStr, []);
        map.get(dateStr)!.push(doc);
      }
    }
    return map;
  }, [documents]);

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const monthName = currentDate.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const days: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) days.push(null);
  for (let d = 1; d <= daysInMonth; d++) days.push(d);

  const prevMonth = () => setCurrentDate(new Date(year, month - 1, 1));
  const nextMonth = () => setCurrentDate(new Date(year, month + 1, 1));

  const handleViewDoc = async (doc: Document) => {
    const { url } = await getDocumentViewUrl(doc.id);
    setViewerDoc({ url, filename: doc.filename });
  };

  const selectedDocs = selectedDate ? events.get(selectedDate) || [] : [];

  const downloadICS = (date: string, docs: Document[]) => {
    const d = new Date(date + "T12:00:00");
    const dtStr = d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
    const titles = docs.map(doc => extractMeetingTitle(doc.filename, doc.doc_type)).join(", ");
    const desc = docs.map(doc => `- ${doc.filename}${doc.notes ? ": " + doc.notes.slice(0, 200) : ""}`).join("\\n");
    const ics = [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Civic Lens//EN",
      "BEGIN:VEVENT",
      `DTSTART;VALUE=DATE:${date.replace(/-/g, "")}`,
      `DTEND;VALUE=DATE:${date.replace(/-/g, "")}`,
      `SUMMARY:${titles}`,
      `DESCRIPTION:${desc}`,
      "END:VEVENT", "END:VCALENDAR",
    ].join("\r\n");
    const blob = new Blob([ics], { type: "text/calendar" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `ah-meeting-${date}.ics`; a.click();
  };

  const googleCalendarUrl = (date: string, docs: Document[]) => {
    const titles = docs.map(doc => extractMeetingTitle(doc.filename, doc.doc_type)).join(", ");
    const desc = docs.map(doc => doc.filename).join("\n");
    const dateStr = date.replace(/-/g, "");
    return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(titles)}&dates=${dateStr}/${dateStr}&details=${encodeURIComponent(desc)}&location=${encodeURIComponent("Atlantic Highlands Borough Hall, 100 First Ave, Atlantic Highlands, NJ")}`;
  };

  return (
    <div className="h-full">
      {/* Calendar — viewer is now a modal popup, so the calendar always uses
          the full width. */}
      <div className="p-3 md:p-8 overflow-auto h-full">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4 md:mb-6">
          <div className="flex items-center gap-3">
            <CalendarDaysIcon className="w-6 h-6 flex-shrink-0" style={{ color: brandColor }} />
            <h1 className="text-xl md:text-2xl font-bold text-gray-900">Meeting Calendar</h1>
          </div>
          <p className="text-xs md:text-sm text-gray-500">{events.size} dates · {documents?.length || 0} docs</p>
        </div>

        {/* Month navigation */}
        <div className="bg-white rounded-xl shadow mb-4 md:mb-6">
          <div className="flex items-center justify-between px-3 md:px-6 py-3 md:py-4 border-b border-gray-200">
            <button onClick={prevMonth} className="p-2 hover:bg-gray-100 rounded-lg">
              <ChevronLeftIcon className="w-5 h-5 text-gray-600" />
            </button>
            <h2 className="text-lg font-semibold text-gray-900">{monthName}</h2>
            <button onClick={nextMonth} className="p-2 hover:bg-gray-100 rounded-lg">
              <ChevronRightIcon className="w-5 h-5 text-gray-600" />
            </button>
          </div>

          <div className="p-2">
            <div className="grid grid-cols-7 gap-px mb-1 border-b border-gray-200 pb-1">
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
                <div key={d} className="text-center text-xs font-medium text-gray-500 py-1">{d}</div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-px">
              {days.map((day, i) => {
                if (day === null) return <div key={`empty-${i}`} className="min-h-[80px]" />;
                const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
                const dateDocs = events.get(dateStr);
                const dateBorough = boroughEventsByDate.get(dateStr);
                const hasDocEvents = !!dateDocs;
                const hasBoroughEvents = !!dateBorough;
                const hasEvents = hasDocEvents || hasBoroughEvents;
                const isSelected = dateStr === selectedDate;
                const isToday = new Date().toISOString().slice(0, 10) === dateStr;

                // Borough events take priority (they have proper titles + times)
                const boroughTitles = hasBoroughEvents
                  ? dateBorough!.map(e => e.time ? `${e.title} ${e.time}` : e.title)
                  : [];
                // Document-derived titles as fallback
                const docTitles = hasDocEvents && !hasBoroughEvents
                  ? Array.from(new Set(dateDocs!.map(d => extractMeetingTitle(d.filename, d.doc_type))))
                  : [];
                const titles = boroughTitles.length > 0 ? boroughTitles : docTitles;

                return (
                  <button
                    key={day}
                    onClick={() => setSelectedDate(isSelected ? null : dateStr)}
                    className={`min-h-[80px] p-1 text-left rounded-lg transition-colors border ${
                      isSelected ? "text-white border-transparent"
                        : hasEvents ? "border-gray-100 hover:border-gray-300"
                        : isToday ? "bg-gray-50 border-gray-200"
                        : "border-transparent hover:bg-gray-50"
                    }`}
                    style={isSelected ? { backgroundColor: brandColor } : {}}
                  >
                    <div className={`text-xs font-medium mb-0.5 ${isSelected ? "text-white" : isToday ? "text-gray-900" : "text-gray-600"}`}>
                      {day}
                    </div>
                    {hasEvents && titles.slice(0, 3).map((title, ti) => (
                      <div key={ti}
                        className={`text-[9px] leading-tight px-1 py-0.5 rounded mb-0.5 truncate ${
                          isSelected ? "bg-white/20 text-white" : ""
                        }`}
                        style={!isSelected ? { backgroundColor: `${brandColor}12`, color: brandColor } : {}}
                      >
                        {title}
                      </div>
                    ))}
                    {hasEvents && titles.length > 3 && (
                      <div className={`text-[8px] ${isSelected ? "text-white/70" : "text-gray-400"}`}>
                        +{titles.length - 3} more
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Selected date documents */}
        {selectedDate && (
          <div className="bg-white rounded-xl shadow p-4 md:p-6">
            <div className="flex items-center justify-between mb-3 gap-2">
              <h3 className="font-semibold text-gray-900">
                {new Date(selectedDate + "T12:00:00").toLocaleDateString("en-US", {
                  weekday: "long", month: "long", day: "numeric", year: "numeric",
                })}
              </h3>
              {selectedDocs.length > 0 && (
                <div className="flex gap-1">
                  <button onClick={() => downloadICS(selectedDate, selectedDocs)}
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-gray-200 rounded-lg hover:bg-gray-50" title="Download .ics">
                    <ArrowDownTrayIcon className="w-3 h-3" /> ICS
                  </button>
                  <a href={googleCalendarUrl(selectedDate, selectedDocs)} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-gray-200 rounded-lg hover:bg-gray-50" title="Add to Google Calendar">
                    <CalendarDaysIcon className="w-3 h-3" /> Google
                  </a>
                </div>
              )}
            </div>
            {selectedDocs.length > 0 ? (
              <div className="space-y-2">
                {selectedDocs.map((doc) => (
                  <div key={doc.id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors">
                    <DocumentTextIcon className="w-5 h-5 flex-shrink-0" style={{ color: brandColor }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900">{extractMeetingTitle(doc.filename, doc.doc_type)}</p>
                      <p className="text-xs text-gray-500 truncate">{doc.filename}</p>
                      {doc.notes && <p className="text-xs text-gray-500 mt-1 line-clamp-2">{doc.notes}</p>}
                    </div>
                    <button onClick={() => handleViewDoc(doc)}
                      className="p-2 rounded-lg hover:bg-gray-200" style={{ color: brandColor }} title="View">
                      <EyeIcon className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">No meeting documents for this date.</p>
            )}
          </div>
        )}

        {/* Recent meetings */}
        {!selectedDate && (
          <div className="bg-white rounded-xl shadow p-6">
            <h3 className="font-semibold text-gray-900 mb-3">Recent Meetings with Documents</h3>
            <div className="space-y-2">
              {Array.from(events.entries())
                .sort((a, b) => b[0].localeCompare(a[0]))
                .slice(0, 15)
                .map(([date, docs]) => (
                  <button key={date} onClick={() => setSelectedDate(date)}
                    className="w-full flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 text-left transition-colors">
                    <div className="w-12 h-12 rounded-lg flex flex-col items-center justify-center flex-shrink-0"
                      style={{ backgroundColor: `${brandColor}10` }}>
                      <span className="text-[10px] font-medium" style={{ color: brandColor }}>
                        {new Date(date + "T12:00:00").toLocaleDateString("en-US", { month: "short" })}
                      </span>
                      <span className="text-lg font-bold" style={{ color: brandColor }}>
                        {new Date(date + "T12:00:00").getDate()}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900">{docs.map(d => extractMeetingTitle(d.filename, d.doc_type)).filter((v, i, a) => a.indexOf(v) === i).join(", ")}</p>
                      <p className="text-xs text-gray-500">{docs.length} document{docs.length > 1 ? "s" : ""} &middot; {new Date(date + "T12:00:00").toLocaleDateString("en-US", { year: "numeric" })}</p>
                    </div>
                  </button>
                ))}
              {events.size === 0 && (
                <p className="text-sm text-gray-400 py-4">No meetings found. Meeting dates are extracted from document metadata.</p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Document viewer — popup modal with proper PDF rendering, zoom,
          page nav, and swipe gestures (FilePreviewModal handles all viewports). */}
      {viewerDoc && (
        <FilePreviewModal
          isOpen
          url={viewerDoc.url}
          filename={viewerDoc.filename}
          onClose={() => setViewerDoc(null)}
        />
      )}
    </div>
  );
}

function extractMeetingTitle(filename: string, docType: string | null): string {
  const name = filename.replace(/\.\w+$/, "");
  const patterns: [RegExp, string][] = [
    [/HHRS\s*Regular\s*BOE/i, "HHRS Regular BOE Meeting"],
    [/HHRS\s*Special\s*BOE/i, "HHRS Special BOE Meeting"],
    [/HHPK.*Regular\s*BOE/i, "HH PK-12 Regular BOE Meeting"],
    [/HHPK.*Special/i, "HH PK-12 Special Meeting"],
    [/HHPK.*Organization/i, "HH PK-12 Organization Meeting"],
    [/BOE\s*Meeting/i, "Board of Education Meeting"],
    [/BOE\s*Agenda/i, "Board of Education Agenda"],
    [/Council\s*Meeting/i, "Council Meeting"],
    [/Council\s*Agenda/i, "Council Agenda"],
    [/Planning\s*Board/i, "Planning Board Meeting"],
    [/Zoning\s*Board/i, "Zoning Board Meeting"],
    [/Work\s*Session/i, "Work Session"],
    [/Special\s*Meeting/i, "Special Meeting"],
    [/Regular\s*Meeting/i, "Regular Meeting"],
    [/Reorganization/i, "Reorganization Meeting"],
    [/Green\s*Team/i, "Green Team Meeting"],
  ];
  for (const [pattern, title] of patterns) {
    if (pattern.test(name)) return title;
  }
  // Check filename for resolution/ordinance numbers
  if (/^\d{4}-\d{3}\s/.test(name)) return "Council Action";
  if (/^ORD\s/i.test(name)) return "Ordinance";
  if (/Payment\s*of\s*Bills/i.test(name)) return "Payment of Bills";
  if (/Budget/i.test(name)) return "Budget Action";
  if (docType === "minutes") return "Meeting Minutes";
  if (docType === "agenda") return "Meeting Agenda";
  if (docType === "resolution") return "Resolution";
  if (docType === "ordinance") return "Ordinance";
  if (docType === "budget") return "Budget";
  if (docType === "planning") return "Planning";
  if (docType === "general") return "Council Action";
  return docType ? docType.charAt(0).toUpperCase() + docType.slice(1) : "Document";
}

function extractDateFromDoc(doc: Document): string | null {
  const monthNames: Record<string, number> = {
    january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
    july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
    jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };

  const validate = (y: number, m: number, d: number): string | null => {
    if (y >= 2015 && y <= 2030 && m >= 1 && m <= 12 && d >= 1 && d <= 31) {
      return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    }
    return null;
  };

  // 1. Try extracting date from AI notes first (most accurate)
  // Pattern: "on Month DD, YYYY" or "Month DD, YYYY"
  if (doc.notes) {
    const noteMatch = doc.notes.match(/(?:on\s+)?(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s*(\d{4})/i);
    if (noteMatch) {
      const m = monthNames[noteMatch[1].toLowerCase()];
      if (m) {
        const r = validate(+noteMatch[3], m, +noteMatch[2]);
        if (r) return r;
      }
    }
  }

  // 2. Try filename patterns
  const fn = doc.filename;

  // YYYY-MM-DD or YYYY.MM.DD
  const isoMatch = fn.match(/(\d{4})[-.](\d{1,2})[-.](\d{1,2})/);
  if (isoMatch) { const r = validate(+isoMatch[1], +isoMatch[2], +isoMatch[3]); if (r) return r; }

  // MM_DD_YYYY or MM-DD-YYYY
  const mdyMatch = fn.match(/(\d{1,2})[_-](\d{1,2})[_-](\d{4})/);
  if (mdyMatch) { const r = validate(+mdyMatch[3], +mdyMatch[1], +mdyMatch[2]); if (r) return r; }

  // M.DD.YYYY
  const dotMatch = fn.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (dotMatch) { const r = validate(+dotMatch[3], +dotMatch[1], +dotMatch[2]); if (r) return r; }

  // MonthNameDDYYYY
  const monthDayYear = fn.match(/(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[_\s]*(\d{1,2})[_\s,]*(\d{4})/i);
  if (monthDayYear) {
    const m = monthNames[monthDayYear[1].toLowerCase()];
    if (m) { const r = validate(+monthDayYear[3], m, +monthDayYear[2]); if (r) return r; }
  }

  // Number-MonthDDYYYY (e.g., "12-May222024")
  const numMonthMatch = fn.match(/(\d{1,2})[-_]?(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{1,2})(\d{4})/i);
  if (numMonthMatch) {
    const m = monthNames[numMonthMatch[2].toLowerCase()];
    if (m) { const r = validate(+numMonthMatch[4], m, +numMonthMatch[3]); if (r) return r; }
  }

  return null;
}
