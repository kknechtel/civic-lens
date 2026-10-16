"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAdminDocuments, type AdminDocumentRow } from "@/lib/api";
import { CheckCircle2, XCircle, AlertTriangle, Search } from "lucide-react";

const brandColor = "#385854";

type Tri = "" | "yes" | "no";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function StatusPill({ ok, partial, label }: { ok: boolean; partial?: boolean; label: string }) {
  if (ok) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[11px]">
        <CheckCircle2 className="w-3 h-3" /> {label}
      </span>
    );
  }
  if (partial) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[11px]">
        <AlertTriangle className="w-3 h-3" /> {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[11px]">
      <XCircle className="w-3 h-3" /> {label}
    </span>
  );
}

export default function AdminDocumentsPanel() {
  const [search, setSearch] = useState("");
  const [hasOcr, setHasOcr] = useState<Tri>("");
  const [hasVector, setHasVector] = useState<Tri>("");

  const { data: docs, isLoading } = useQuery({
    queryKey: ["admin-documents", search, hasOcr, hasVector],
    queryFn: () =>
      getAdminDocuments({
        search: search || undefined,
        has_ocr: (hasOcr || undefined) as "yes" | "no" | undefined,
        has_vector: (hasVector || undefined) as "yes" | "no" | undefined,
        limit: 500,
      }),
  });

  // Summary counts (across the loaded subset).
  const total = docs?.length ?? 0;
  const ocrd = docs?.filter((d) => d.is_ocrd).length ?? 0;
  const vec = docs?.filter((d) => d.is_vector_indexed).length ?? 0;
  const partialVec =
    docs?.filter((d) => !d.is_vector_indexed && (d.embedded_chunk_count > 0 || d.chunk_count > 0)).length ?? 0;

  return (
    <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="font-semibold text-gray-900">Document corpus</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {total} documents • OCR'd {ocrd} • Vector-indexed {vec}
            {partialVec > 0 ? ` • ${partialVec} partial` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2 top-1/2 -translate-y-1/2" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by filename"
              className="pl-7 pr-2 py-1.5 text-xs border border-gray-300 rounded w-56 focus:outline-none focus:border-gray-500"
            />
          </div>
          <select
            value={hasOcr}
            onChange={(e) => setHasOcr(e.target.value as Tri)}
            className="text-xs border border-gray-300 rounded px-2 py-1.5"
          >
            <option value="">OCR: any</option>
            <option value="yes">OCR'd</option>
            <option value="no">Not OCR'd</option>
          </select>
          <select
            value={hasVector}
            onChange={(e) => setHasVector(e.target.value as Tri)}
            className="text-xs border border-gray-300 rounded px-2 py-1.5"
          >
            <option value="">Vector: any</option>
            <option value="yes">Indexed</option>
            <option value="no">Not indexed</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
        {isLoading ? (
          <p className="px-6 py-8 text-sm text-gray-500">Loading…</p>
        ) : docs && docs.length > 0 ? (
          <table className="w-full text-xs">
            <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-500">File</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Project</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">FY</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Pages</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Size</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">OCR</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Vector</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Chunks</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">Uploaded by</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {docs.map((d: AdminDocumentRow) => {
                const partialChunks =
                  !d.is_vector_indexed && (d.embedded_chunk_count > 0 || d.chunk_count > 0);
                return (
                  <tr key={d.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 max-w-xs">
                      <p className="font-medium text-gray-800 truncate" title={d.filename}>
                        {d.filename}
                      </p>
                      {d.doc_type && <p className="text-gray-400 text-[10px]">{d.doc_type}</p>}
                    </td>
                    <td className="px-4 py-2 text-gray-600">{d.project_name || "—"}</td>
                    <td className="px-4 py-2 text-gray-600">{d.fiscal_year || "—"}</td>
                    <td className="px-4 py-2 text-gray-600">{d.page_count ?? "—"}</td>
                    <td className="px-4 py-2 text-gray-600">{fmtBytes(d.file_size)}</td>
                    <td className="px-4 py-2">
                      <StatusPill ok={d.is_ocrd} label={d.is_ocrd ? `${(d.ocr_chars / 1000).toFixed(1)}k chars` : "none"} />
                    </td>
                    <td className="px-4 py-2">
                      <StatusPill
                        ok={d.is_vector_indexed}
                        partial={partialChunks}
                        label={d.is_vector_indexed ? "indexed" : partialChunks ? "partial" : "none"}
                      />
                    </td>
                    <td className="px-4 py-2 text-gray-600">
                      {d.embedded_chunk_count}/{d.chunk_count}
                    </td>
                    <td className="px-4 py-2 text-gray-500 text-[11px] truncate max-w-[12rem]"
                        title={d.uploaded_by_email || ""}>
                      {d.uploaded_by_email || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="px-6 py-8 text-sm text-gray-400">No documents match these filters.</p>
        )}
      </div>
    </div>
  );
}
