"use client";

import Image from "next/image";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getProjects, getStatements, getRecentDocuments, type Document } from "@/lib/api";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import {
  FolderIcon, DocumentTextIcon, ChartBarIcon,
  BuildingOfficeIcon, AcademicCapIcon, MicrophoneIcon,
} from "@heroicons/react/24/outline";

function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function DocIcon({ doc }: { doc: Document }) {
  const t = doc.doc_type || "";
  if (t.startsWith("recording_")) return <MicrophoneIcon className="w-4 h-4 text-violet-500" />;
  if (doc.category === "school") return <AcademicCapIcon className="w-4 h-4 text-orange-500" />;
  if (doc.category === "town") return <BuildingOfficeIcon className="w-4 h-4 text-blue-500" />;
  return <DocumentTextIcon className="w-4 h-4 text-gray-400" />;
}

export default function Dashboard() {
  const municipality = useMunicipality();
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const { data: statements } = useQuery({ queryKey: ["statements"], queryFn: () => getStatements() });
  const { data: recentDocs, isLoading: docsLoading } = useQuery({
    queryKey: ["recent-documents", 10],
    queryFn: () => getRecentDocuments(10),
    // Scraper runs at midnight ET; refetch every 5 min while the dashboard
    // is foregrounded so newly scraped docs surface without a hard reload.
    refetchInterval: 5 * 60 * 1000,
    refetchIntervalInBackground: false,
  });

  const totalDocs = projects?.reduce((sum, p) => sum + p.document_count, 0) || 0;
  const townStatements = statements?.filter((s) => s.entity_type === "town") || [];
  const schoolStatements = statements?.filter((s) => s.entity_type === "school") || [];

  return (
    <div className="p-4 md:p-8">
      {/* Banner — a municipality can ship its own photo by adding
          branding.banner_url to its profile; otherwise a brand gradient. */}
      <div
        className="relative h-40 md:h-56 rounded-xl overflow-hidden shadow mb-6 md:mb-8"
        style={{ background: `linear-gradient(135deg, ${municipality.branding.theme_color}, #0f172a)` }}
      >
        {municipality.branding.banner_url && (
          <Image
            src={municipality.branding.banner_url}
            alt={municipality.branding.banner_alt || ""}
            fill
            priority
            sizes="(min-width: 768px) 80vw, 100vw"
            className="object-cover object-bottom"
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 p-4 md:p-6 text-white">
          <h1 className="text-2xl md:text-3xl font-bold drop-shadow-lg">Dashboard</h1>
          <p className="text-sm md:text-base text-white/90 drop-shadow">
            {municipality.short_name ? `${municipality.short_name} ` : ""}Document Library &amp; Financial Analysis
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-6 mt-6 md:mt-8">
        <StatCard
          icon={<FolderIcon className="w-8 h-8 text-blue-500" />}
          label="Projects"
          value={projects?.length || 0}
        />
        <StatCard
          icon={<DocumentTextIcon className="w-8 h-8 text-primary-500" />}
          label="Documents"
          value={totalDocs}
        />
        <StatCard
          icon={<ChartBarIcon className="w-8 h-8 text-purple-500" />}
          label="Town Statements"
          value={townStatements.length}
        />
        <StatCard
          icon={<ChartBarIcon className="w-8 h-8 text-orange-500" />}
          label="School Statements"
          value={schoolStatements.length}
        />
      </div>

      {/* Recently added documents — primary surface for the nightly scrape. */}
      <div className="bg-white rounded-xl shadow p-4 md:p-6 mt-6 md:mt-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Recently Added Documents</h2>
          <Link
            href="/document-library"
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            View all →
          </Link>
        </div>
        {docsLoading ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : !recentDocs || recentDocs.length === 0 ? (
          <p className="text-gray-400 text-sm">
            No documents yet. The nightly scrape runs at midnight ET.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {recentDocs.map((d) => (
              <li key={d.id}>
                <Link
                  href={`/document-library?doc=${encodeURIComponent(d.id)}`}
                  className="flex items-start gap-3 py-2.5 hover:bg-gray-50 -mx-2 px-2 rounded transition-colors"
                >
                  <div className="mt-0.5 flex-shrink-0">
                    <DocIcon doc={d} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-gray-900 truncate">
                      {d.title || d.filename}
                    </div>
                    <div className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-1.5 flex-wrap">
                      {d.department && <span>{d.department}</span>}
                      {d.department && d.doc_type && <span className="text-gray-300">·</span>}
                      {d.doc_type && <span>{d.doc_type.replace(/_/g, " ")}</span>}
                      {d.fiscal_year && (
                        <>
                          <span className="text-gray-300">·</span>
                          <span>FY {d.fiscal_year}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <span className="text-[11px] text-gray-400 flex-shrink-0 mt-1 whitespace-nowrap">
                    {fmtAgo(d.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Recent activity */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6 mt-6 md:mt-8">
        <div className="bg-white rounded-xl shadow p-4 md:p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Recent Projects</h2>
          {projects?.length ? (
            <ul className="space-y-3">
              {projects.slice(0, 5).map((p) => (
                <li key={p.id} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700">{p.name}</span>
                  <span className="text-gray-400">{p.document_count} docs</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400 text-sm">No projects yet. Create one to get started.</p>
          )}
        </div>

        <div className="bg-white rounded-xl shadow p-4 md:p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Recent Financial Statements</h2>
          {statements?.length ? (
            <ul className="space-y-3">
              {statements.slice(0, 5).map((s) => (
                <li key={s.id} className="flex items-center justify-between text-sm">
                  <div>
                    <span className="text-gray-700">{s.entity_name}</span>
                    <span className="text-gray-400 ml-2">FY {s.fiscal_year}</span>
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs ${
                      s.status === "extracted"
                        ? "bg-green-100 text-green-700"
                        : s.status === "processing"
                        ? "bg-yellow-100 text-yellow-700"
                        : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {s.status}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400 text-sm">
              No financial statements yet. Upload documents to extract financial data.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="bg-white rounded-xl shadow p-3 md:p-6 flex items-center gap-3 md:gap-4">
      <div className="flex-shrink-0">{icon}</div>
      <div className="min-w-0">
        <p className="text-xl md:text-2xl font-bold text-gray-900">{value}</p>
        <p className="text-xs md:text-sm text-gray-500 truncate">{label}</p>
      </div>
    </div>
  );
}
