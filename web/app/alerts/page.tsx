"use client";

import { useMunicipality, entityOfKind } from "@/app/contexts/MunicipalityContext";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BellIcon, MagnifyingGlassIcon, MicrophoneIcon, DocumentTextIcon,
  PlusIcon, TrashIcon, PauseIcon, PlayIcon,
} from "@heroicons/react/24/outline";
import {
  listAlerts, createAlert, updateAlert, deleteAlert,
  type SavedAlert, type AlertKind, type DigestFrequency,
} from "@/lib/api";

const brandColor = "#385854";

const KIND_LABEL: Record<AlertKind, string> = {
  keyword: "Keyword search",
  new_meeting: "New meeting",
  new_document: "New document",
};

const KIND_ICON: Record<AlertKind, React.ComponentType<{ className?: string }>> = {
  keyword: MagnifyingGlassIcon,
  new_meeting: MicrophoneIcon,
  new_document: DocumentTextIcon,
};

// Matched as a case-insensitive substring of the recording's meeting body.
const MEETING_BODIES = ["Council", "Planning", "Zoning", "Board of Education"] as const;
function docCategories(schoolShort?: string) {
  return [
    { value: "", label: "All categories" },
    { value: "town", label: "Town" },
    { value: "school", label: schoolShort ? `School (${schoolShort})` : "School" },
  ];
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export default function AlertsPage() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);

  const { data: alerts, isLoading } = useQuery({
    queryKey: ["alerts"],
    queryFn: listAlerts,
  });

  const toggle = useMutation({
    mutationFn: (a: SavedAlert) => updateAlert(a.id, { enabled: !a.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <BellIcon className="w-6 h-6" style={{ color: brandColor }} />
            Alerts
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Email digests for new content matching your saved searches. Sent at 7:30am ET.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="px-3 py-2 text-sm rounded-md text-white flex items-center gap-1.5 hover:opacity-90"
            style={{ backgroundColor: brandColor }}
          >
            <PlusIcon className="w-4 h-4" /> New alert
          </button>
        )}
      </div>

      {showForm && (
        <NewAlertForm
          onCancel={() => setShowForm(false)}
          onCreated={() => {
            setShowForm(false);
            qc.invalidateQueries({ queryKey: ["alerts"] });
          }}
        />
      )}

      {isLoading ? (
        <div className="text-sm text-gray-400 py-8 text-center">Loading…</div>
      ) : !alerts || alerts.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-sm text-gray-500">
          No alerts yet. Create one to get a daily email when matching documents are ingested.
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map((a) => {
            const Icon = KIND_ICON[a.kind];
            return (
              <div
                key={a.id}
                className={`bg-white border rounded-lg p-3 md:p-4 ${a.enabled ? "border-gray-200" : "border-gray-200 opacity-60"}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
                      <Icon className="w-3.5 h-3.5" />
                      <span>{KIND_LABEL[a.kind]}</span>
                      <span className="text-gray-300">·</span>
                      <span>{a.frequency}</span>
                    </div>
                    <div className="text-sm font-medium text-gray-900 truncate">{a.name}</div>
                    {a.query && (
                      <div className="text-xs text-gray-600 mt-1 font-mono truncate">"{a.query}"</div>
                    )}
                    <FilterChips filters={a.filters} />
                    <div className="text-[11px] text-gray-400 mt-2">
                      Last sent: {fmtDate(a.last_sent_at)}
                      {" · "}Created: {fmtDate(a.created_at)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => toggle.mutate(a)}
                      className="p-2 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700"
                      title={a.enabled ? "Pause this alert" : "Resume this alert"}
                    >
                      {a.enabled ? <PauseIcon className="w-4 h-4" /> : <PlayIcon className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => {
                        if (confirm(`Delete alert "${a.name}"?`)) remove.mutate(a.id);
                      }}
                      className="p-2 hover:bg-red-50 rounded text-gray-400 hover:text-red-600"
                      title="Delete alert"
                    >
                      <TrashIcon className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FilterChips({ filters }: { filters: Record<string, string | undefined> }) {
  const entries = Object.entries(filters || {}).filter(([, v]) => v);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-2">
      {entries.map(([k, v]) => (
        <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
          {k}: {v}
        </span>
      ))}
    </div>
  );
}

function NewAlertForm({
  onCancel, onCreated,
}: { onCancel: () => void; onCreated: () => void }) {
  const [kind, setKind] = useState<AlertKind>("keyword");
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [frequency, setFrequency] = useState<DigestFrequency>("daily");
  const [body, setBody] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const municipality = useMunicipality();
  const DOC_CATEGORIES = docCategories(entityOfKind(municipality, "school")?.short);

  const create = useMutation({
    mutationFn: () => {
      const filters: Record<string, string> = {};
      if (kind === "new_meeting" && body) filters.body = body;
      if (kind === "new_document" && category) filters.category = category;
      return createAlert({
        kind, name: name.trim(),
        query: kind === "keyword" ? query.trim() : undefined,
        filters, frequency,
      });
    },
    onSuccess: onCreated,
    onError: (e: any) => setError(e?.message || "Failed to create alert"),
  });

  const canSubmit = name.trim().length > 0 &&
    (kind !== "keyword" || query.trim().length > 0);

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 md:p-5 mb-4">
      <div className="text-sm font-semibold text-gray-900 mb-3">New alert</div>

      <label className="block text-xs font-medium text-gray-700 mb-1">Type</label>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
        {(["keyword", "new_meeting", "new_document"] as AlertKind[]).map((k) => {
          const Icon = KIND_ICON[k];
          const active = kind === k;
          return (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={`text-left p-3 rounded-md border text-sm flex items-start gap-2 ${
                active ? "border-2" : "border-gray-200 hover:border-gray-300"
              }`}
              style={active ? { borderColor: brandColor, backgroundColor: `${brandColor}10` } : {}}
            >
              <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-gray-900">{KIND_LABEL[k]}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {k === "keyword" && "Match a phrase across all new content."}
                  {k === "new_meeting" && "New meeting recording posted."}
                  {k === "new_document" && "New document added."}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <label className="block text-xs font-medium text-gray-700 mb-1">Name</label>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={
          kind === "keyword" ? "Bayside Drive mentions"
            : kind === "new_meeting" ? "New Council meetings"
            : "New school district docs"
        }
        className="w-full mb-3 px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400"
      />

      {kind === "keyword" && (
        <>
          <label className="block text-xs font-medium text-gray-700 mb-1">Search query</label>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='e.g. "Bayside Drive" or affordable housing'
            className="w-full mb-3 px-3 py-2 text-sm font-mono border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400"
          />
        </>
      )}

      {kind === "new_meeting" && (
        <>
          <label className="block text-xs font-medium text-gray-700 mb-1">Body (optional)</label>
          <select
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full mb-3 px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400 bg-white"
          >
            <option value="">All bodies</option>
            {MEETING_BODIES.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </>
      )}

      {kind === "new_document" && (
        <>
          <label className="block text-xs font-medium text-gray-700 mb-1">Category (optional)</label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full mb-3 px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400 bg-white"
          >
            {DOC_CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </>
      )}

      <label className="block text-xs font-medium text-gray-700 mb-1">Frequency</label>
      <div className="flex gap-2 mb-4">
        {(["daily", "weekly"] as DigestFrequency[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFrequency(f)}
            className={`px-3 py-1.5 text-xs rounded-md border ${
              frequency === f ? "text-white" : "border-gray-300 text-gray-700 hover:bg-gray-50"
            }`}
            style={frequency === f ? { backgroundColor: brandColor, borderColor: brandColor } : {}}
          >
            {f === "daily" ? "Daily (7:30am ET)" : "Weekly (Mondays)"}
          </button>
        ))}
      </div>

      {error && <div className="text-xs text-red-700 mb-2">{error}</div>}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-sm rounded-md border border-gray-300 text-gray-700 hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={!canSubmit || create.isPending}
          onClick={() => create.mutate()}
          className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-50"
          style={{ backgroundColor: brandColor }}
        >
          {create.isPending ? "Creating…" : "Create alert"}
        </button>
      </div>
    </div>
  );
}
