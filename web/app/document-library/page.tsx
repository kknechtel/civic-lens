"use client";

import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getDocuments,
  getDocument,
  createProject,
  deleteDocument,
  getDocumentViewUrl,
  searchDocuments,
  getSearchFacets,
  recordSearchClick,
  type Document,
} from "@/lib/api";
import dynamic from "next/dynamic";
import UploadModal from "@/components/UploadModal";
import DocumentChatModal from "@/components/EnhancedDocumentChatModal";
import type { SearchResult } from "@/lib/api";
import { useMunicipality, type Municipality, type EntityKind } from "@/app/contexts/MunicipalityContext";

// react-pdf depends on browser-only globals (DOMMatrix, etc.) — must not SSR.
const PDFViewer = dynamic(() => import("@/components/PDFViewer"), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-gray-400">Loading PDF viewer…</div>,
});
import {
  FolderPlusIcon,
  ArrowUpTrayIcon,
  TrashIcon,
  MagnifyingGlassIcon,
  ChatBubbleLeftRightIcon,
  DocumentTextIcon,
  SparklesIcon,
  CalendarIcon,
  XMarkIcon,
  ArrowTopRightOnSquareIcon,
  ArrowDownTrayIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  BuildingOfficeIcon,
  AcademicCapIcon,
  FolderIcon,
} from "@heroicons/react/24/outline";

const brandColor = "#385854";

type EntityMap = Record<string, { label: string; short: string; icon: any }>;

const KIND_ICON: Partial<Record<EntityKind, any>> = {
  municipal: BuildingOfficeIcon,
  school: AcademicCapIcon,
};

/** Top-level library groups, in display order — the municipality profile's
 *  entities (keyed by documents.municipality) plus a catch-all. */
function buildEntities(m: Municipality): EntityMap {
  const map: EntityMap = {};
  for (const e of m.entities) {
    map[e.key] = { label: e.label, short: e.short, icon: KIND_ICON[e.kind] || FolderIcon };
  }
  map.general = { label: "General / Uncategorized", short: "Other", icon: FolderIcon };
  return map;
}

/** Municipality group for a doc; falls back to the legacy category for unmigrated rows. */
function entityOf(doc: Document, ENTITIES: EntityMap, m: Municipality): string {
  if (doc.municipality && ENTITIES[doc.municipality]) return doc.municipality;
  if (doc.category === "school") return m.entities.find((e) => e.kind === "school")?.key || "general";
  if (doc.category === "town") return m.entities[0]?.key || "general";
  return "general";
}

interface GroupNode {
  label: string;
  icon?: any;
  docs: Document[];
  children: Map<string, GroupNode>;
}

/**
 * Display fiscal_year defensively. The DB still has legacy junk like "2026-07"
 * (a regex mis-match against a resolution number — see backfill endpoint).
 * Until that data is cleaned, hide values that aren't a valid YYYY or
 * YYYY-(YY|YYYY) school year ending in year+1.
 */
function displayFiscalYear(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const s = raw.trim();
  if (/^(19|20)\d{2}$/.test(s)) return s;
  const m4 = s.match(/^((?:19|20)\d{2})-((?:19|20)\d{2})$/);
  if (m4 && parseInt(m4[2], 10) === parseInt(m4[1], 10) + 1) return s;
  const m2 = s.match(/^((?:19|20)\d{2})-(\d{2})$/);
  if (m2 && parseInt(m2[2], 10) === (parseInt(m2[1], 10) + 1) % 100) return s;
  return null;
}

/**
 * Format an ISO YYYY-MM-DD into a compact human-readable date like
 * "Feb 4, 2026". Date construction uses noon UTC to avoid timezone
 * rollover making the day shift on the user's locale.
 */
function formatDocDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return iso;
  const [_, y, mo, d] = m;
  const dt = new Date(`${y}-${mo}-${d}T12:00:00Z`);
  if (isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export default function DocumentLibraryPage() {
  const municipality = useMunicipality();
  const ENTITIES = useMemo(() => buildEntities(municipality), [municipality]);
  const entityOfDoc = (d: Document) => entityOf(d, ENTITIES, municipality);

  const queryClient = useQueryClient();
  const [showUpload, setShowUpload] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectType, setNewProjectType] = useState("");
  const [search, setSearch] = useState("");
  const [entityFilter, setEntityFilter] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState("");
  const [yearFilter, setYearFilter] = useState("");
  const [deptFilter, setDeptFilter] = useState("");
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  const [viewerUrl, setViewerUrl] = useState<string | null>(null);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const [chatDoc, setChatDoc] = useState<Document | null>(null);
  const [showChat, setShowChat] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const { data: documents } = useQuery({
    queryKey: ["documents", docTypeFilter],
    queryFn: () =>
      getDocuments({
        doc_type: docTypeFilter || undefined,
      }),
  });

  const { data: facets } = useQuery({
    queryKey: ["facets"],
    queryFn: () => getSearchFacets(),
  });

  const { data: searchResponse } = useQuery({
    queryKey: ["search", search, docTypeFilter],
    queryFn: () =>
      searchDocuments(search, {
        doc_type: docTypeFilter || undefined,
      }),
    enabled: search.length > 1,
  });
  const searchResults = searchResponse?.results;

  const createProjectMutation = useMutation({
    mutationFn: () => createProject(newProjectName, undefined, newProjectType || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setShowNewProject(false);
      setNewProjectName("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (selectedDoc) setSelectedDoc(null);
    },
  });

  const handleSelectDoc = async (doc: Document) => {
    setSelectedDoc(doc);
    setViewerUrl(null);
    setViewerError(null);
    setViewerError(null);
    // Fire-and-forget: tell the backend the user clicked this result.
    // Used for relevance analytics — see search_query_log.
    if (searchResponse?.query_id && search.length > 1) {
      recordSearchClick(searchResponse.query_id, doc.id);
    }
    // Fetch full doc (includes notes/AI summary which are stripped from list)
    try {
      const full = await getDocument(doc.id);
      setSelectedDoc(full);
    } catch {
      // Fall back to list-version doc if fetch fails
    }
    try {
      const { url } = await getDocumentViewUrl(doc.id);
      setViewerUrl(url);
    } catch (err) {
      setViewerUrl(null);
      setViewerError(err instanceof Error ? err.message : "Couldn't load this file.");
    }
  };

  // Deep link: /document-library?doc=<id> (dashboard "Recently Added" list).
  // Read window.location directly so the page doesn't need a Suspense
  // boundary for useSearchParams.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("doc");
    if (!id) return;
    let cancelled = false;
    (async () => {
      try {
        const full = await getDocument(id);
        if (cancelled) return;
        setSelectedDoc(full);
        const { url } = await getDocumentViewUrl(id);
        if (!cancelled) setViewerUrl(url);
      } catch (err) {
        if (!cancelled && err instanceof Error) setViewerError(err.message);
        // Doc gone or not visible to this user — leave the library unselected.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Result lookup keyed by document id — carries snippet, match_type,
  // additional snippets, page numbers, and chunk-match count so DocRow can
  // render rich match context.
  const searchById = useMemo(() => {
    const map = new Map<string, SearchResult>();
    (searchResults || []).forEach((r) => map.set(r.id, r));
    return map;
  }, [searchResults]);

  // Filter documents
  const displayDocs = useMemo(() => {
    let docs = documents || [];
    if (search.length > 1 && searchResults) {
      const ids = new Set(searchResults.map((r) => r.id));
      docs = docs.filter((d) => ids.has(d.id));
    }
    if (entityFilter) {
      docs = docs.filter((d) => entityOfDoc(d) === entityFilter);
    }
    if (yearFilter) {
      docs = docs.filter((d) => d.fiscal_year === yearFilter);
    }
    if (deptFilter) {
      // Case-insensitive comparison — the dropdown shows server-deduped names,
      // but underlying records may still vary in casing/whitespace.
      const target = deptFilter.toLowerCase().trim();
      docs = docs.filter((d) => (d.department || "").toLowerCase().trim() === target);
    }
    return docs;
  }, [documents, search, searchResults, entityFilter, yearFilter, deptFilter, ENTITIES]);

  // Use server-deduped facet lists (4-digit years, case-insensitive depts).
  const availableYears = useMemo(
    () => Object.keys(facets?.fiscal_years || {}).sort().reverse(),
    [facets],
  );
  const availableDepts = useMemo(
    () => Object.keys(facets?.departments || {}),
    [facets],
  );

  const hasActiveFilters = !!(entityFilter || docTypeFilter || yearFilter || deptFilter);

  // Build hierarchy: Entity (municipality) > Doc Type > Documents
  const hierarchy = useMemo(() => {
    const root = new Map<string, GroupNode>();

    for (const doc of displayDocs) {
      const entity = entityOfDoc(doc);
      const docType = doc.doc_type || "other";

      if (!root.has(entity)) {
        root.set(entity, {
          label: ENTITIES[entity]?.label || entity,
          icon: ENTITIES[entity]?.icon || FolderIcon,
          docs: [],
          children: new Map(),
        });
      }
      const entityNode = root.get(entity)!;

      if (!entityNode.children.has(docType)) {
        entityNode.children.set(docType, {
          label: formatDocType(docType),
          docs: [],
          children: new Map(),
        });
      }
      entityNode.children.get(docType)!.docs.push(doc);
    }

    // Sort docs within each group by (cleaned) fiscal year desc, then filename
    root.forEach((entityNode) => {
      entityNode.children.forEach((typeNode) => {
        typeNode.docs.sort((a, b) => {
          const ya = displayFiscalYear(a.fiscal_year) || "";
          const yb = displayFiscalYear(b.fiscal_year) || "";
          if (ya !== yb) return yb.localeCompare(ya);
          return a.filename.localeCompare(b.filename);
        });
      });
    });

    // Return in entity order
    const sorted = new Map<string, GroupNode>();
    for (const key of Object.keys(ENTITIES)) {
      if (root.has(key)) sorted.set(key, root.get(key)!);
    }
    // Add any remaining
    root.forEach((v, k) => { if (!sorted.has(k)) sorted.set(k, v); });
    return sorted;
  }, [displayDocs, ENTITIES]);

  const toggleCollapse = (key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const collapseAll = () => {
    const allKeys = new Set<string>();
    hierarchy.forEach((entityNode, entityKey) => {
      allKeys.add(entityKey);
      entityNode.children.forEach((_, typeKey) => {
        allKeys.add(`${entityKey}:${typeKey}`);
      });
    });
    setCollapsed(allKeys);
  };

  const expandAll = () => {
    setCollapsed(new Set());
  };

  const docTypeColor = (type: string | null) => {
    const colors: Record<string, string> = {
      budget: "bg-emerald-50 text-emerald-700",
      audit: "bg-blue-50 text-blue-700",
      financial_statement: "bg-purple-50 text-purple-700",
      minutes: "bg-amber-50 text-amber-700",
      agenda: "bg-orange-50 text-orange-700",
      ordinance: "bg-red-50 text-red-700",
      resolution: "bg-indigo-50 text-indigo-700",
      legal: "bg-pink-50 text-pink-700",
    };
    return colors[type || ""] || "bg-gray-100 text-gray-600";
  };

  const isSearching = search.length > 1;

  return (
    <div className="flex h-full">
      {/* Left: Document list (hidden on mobile when doc selected) */}
      <div className={`${selectedDoc ? "hidden md:flex w-[420px]" : "flex-1"} flex flex-col border-r border-gray-200 bg-white transition-all min-w-0`}>
        {/* Header */}
        <div className="px-4 pt-3 pb-2 border-b border-gray-200 bg-white flex-shrink-0">
          <div className="flex items-center justify-between mb-2.5">
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-semibold text-gray-900">Documents</h1>
              <span className="text-xs text-gray-500">
                {hasActiveFilters || isSearching
                  ? `${displayDocs.length.toLocaleString()} of ${(documents?.length || 0).toLocaleString()}`
                  : `${(documents?.length || 0).toLocaleString()}`}
              </span>
            </div>
            <button onClick={() => setShowUpload(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md hover:opacity-90 text-white text-xs font-medium shadow-sm" style={{ backgroundColor: brandColor }}>
              <ArrowUpTrayIcon className="w-4 h-4" />
              <span className="hidden sm:inline">Upload</span>
            </button>
          </div>

          {/* Search — supports "quoted phrases", AND, OR, -exclusion (Postgres websearch_to_tsquery) */}
          <div className="relative mb-2">
            <MagnifyingGlassIcon className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder='Search content — use "quotes" for exact phrase'
              className="w-full pl-9 pr-8 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:border-transparent bg-white"
              style={{ "--tw-ring-color": brandColor } as React.CSSProperties} />
            {search && (
              <button onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                <XMarkIcon className="w-4 h-4" />
              </button>
            )}
          </div>
          {isSearching && (
            <div className="text-[10px] text-gray-400 mb-2 px-1">
              Tip: <code className="font-mono">&quot;exact phrase&quot;</code>, <code className="font-mono">word1 word2</code> = AND, <code className="font-mono">a OR b</code>, <code className="font-mono">-exclude</code>
            </div>
          )}

          {/* Filters — compact one-row layout, more readable than 2x2 */}
          <div className="grid grid-cols-2 gap-1.5">
            <select value={entityFilter} onChange={(e) => setEntityFilter(e.target.value)}
              className="px-2 py-1 border border-gray-300 rounded text-[11px] bg-white min-w-0">
              <option value="">All entities</option>
              {Object.entries(ENTITIES).filter(([k]) => k !== "general").map(([k, e]) => (
                <option key={k} value={k}>{e.short}</option>
              ))}
            </select>
            <select value={docTypeFilter} onChange={(e) => setDocTypeFilter(e.target.value)}
              className="px-2 py-1 border border-gray-300 rounded text-[11px] bg-white min-w-0">
              <option value="">All types</option>
              {facets && Object.keys(facets.doc_types).sort().map((k) => (
                <option key={k} value={k === "unclassified" ? "" : k}>{k.replace(/_/g, " ")}</option>
              ))}
            </select>
            <select value={yearFilter} onChange={(e) => setYearFilter(e.target.value)}
              className="px-2 py-1 border border-gray-300 rounded text-[11px] bg-white min-w-0">
              <option value="">All years</option>
              {availableYears.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
            <select value={deptFilter} onChange={(e) => setDeptFilter(e.target.value)}
              className="px-2 py-1 border border-gray-300 rounded text-[11px] bg-white min-w-0">
              <option value="">All departments</option>
              {availableDepts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          {/* Active filter chips — quick visual + one-click clear */}
          {hasActiveFilters && (
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              {entityFilter && (
                <FilterChip label={ENTITIES[entityFilter]?.short || entityFilter} onClear={() => setEntityFilter("")} />
              )}
              {docTypeFilter && (
                <FilterChip label={docTypeFilter.replace(/_/g, " ")} onClear={() => setDocTypeFilter("")} />
              )}
              {yearFilter && <FilterChip label={`FY ${yearFilter}`} onClear={() => setYearFilter("")} />}
              {deptFilter && <FilterChip label={deptFilter} onClear={() => setDeptFilter("")} />}
              <button onClick={() => { setEntityFilter(""); setDocTypeFilter(""); setYearFilter(""); setDeptFilter(""); }}
                className="text-[10px] text-gray-500 hover:text-gray-800 underline ml-1">
                Clear all
              </button>
            </div>
          )}

          {/* Tree controls (subtle, only when not searching) */}
          {!isSearching && (
            <div className="flex items-center justify-end gap-2 mt-2 text-[10px] text-gray-400">
              <button onClick={collapseAll} className="hover:text-gray-700">Collapse all</button>
              <span className="text-gray-300">·</span>
              <button onClick={expandAll} className="hover:text-gray-700">Expand all</button>
            </div>
          )}
        </div>

        {/* Document hierarchy */}
        <div className="flex-1 overflow-y-auto">
          {isSearching ? (
            <>
              {/* Did-you-mean banner — only when result count is sparse */}
              {searchResponse?.did_you_mean && (
                <button
                  onClick={() => setSearch(searchResponse.did_you_mean!)}
                  className="w-full text-left px-4 py-2 bg-amber-50 border-b border-amber-200 hover:bg-amber-100 transition-colors"
                >
                  <span className="text-xs text-amber-900">
                    Did you mean{" "}
                    <span className="font-semibold underline">{searchResponse.did_you_mean}</span>?
                  </span>
                </button>
              )}
              {/* Auto-extracted filter chips — shows what the parser pulled
                  out of the natural-language query (e.g. "FY 2024", "School",
                  "audit"). Separate from manual dropdown chips above. */}
              {searchResponse?.parsed_filters && searchResponse.parsed_filters.hits.length > 0 && (
                <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wide">From your query:</span>
                  {searchResponse.parsed_filters.hits.map((hit, i) => (
                    <span key={i} className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-white border border-gray-200 text-gray-700">
                      {hit}
                    </span>
                  ))}
                </div>
              )}
              {/* Flat list with snippets so the user sees match context */}
              {displayDocs.map((doc) => {
                const hit = searchById.get(doc.id);
                return (
                  <DocRow key={doc.id} doc={doc} isSelected={selectedDoc?.id === doc.id}
                    onClick={() => handleSelectDoc(doc)} docTypeColor={docTypeColor}
                    snippet={hit?.snippet}
                    matchType={hit?.match_type}
                    additionalSnippets={hit?.additional_snippets || undefined}
                    pages={hit?.pages || undefined}
                    matchCount={hit?.match_count || undefined}
                    summary={hit?.summary || doc.notes} />
                );
              })}
            </>
          ) : (
            // Hierarchical view
            Array.from(hierarchy.entries()).map(([entityKey, entityNode]) => {
              const entityCollapsed = collapsed.has(entityKey);
              const entityDocCount = Array.from(entityNode.children.values()).reduce((s, n) => s + n.docs.length, 0);
              const EntityIcon = entityNode.icon || FolderIcon;

              return (
                <div key={entityKey}>
                  {/* Entity header */}
                  <button onClick={() => toggleCollapse(entityKey)}
                    className="w-full flex items-center gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-200 hover:bg-gray-100 transition-colors">
                    {entityCollapsed ? <ChevronRightIcon className="w-3.5 h-3.5 text-gray-400" /> : <ChevronDownIcon className="w-3.5 h-3.5 text-gray-400" />}
                    <EntityIcon className="w-4 h-4" style={{ color: brandColor }} />
                    <span className="text-sm font-semibold text-gray-900 flex-1 text-left">{entityNode.label}</span>
                    <span className="text-[11px] text-gray-400">{entityDocCount}</span>
                  </button>

                  {!entityCollapsed && Array.from(entityNode.children.entries()).map(([typeKey, typeNode]) => {
                    const typeCollapsedKey = `${entityKey}:${typeKey}`;
                    const typeCollapsed = collapsed.has(typeCollapsedKey);

                    return (
                      <div key={typeKey}>
                        {/* Doc type header */}
                        <button onClick={() => toggleCollapse(typeCollapsedKey)}
                          className="w-full flex items-center gap-2 pl-8 pr-4 py-2 border-b border-gray-100 hover:bg-gray-50 transition-colors">
                          {typeCollapsed ? <ChevronRightIcon className="w-3 h-3 text-gray-400" /> : <ChevronDownIcon className="w-3 h-3 text-gray-400" />}
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${docTypeColor(typeKey)}`}>{typeNode.label}</span>
                          <span className="text-[11px] text-gray-400 ml-auto">{typeNode.docs.length}</span>
                        </button>

                        {!typeCollapsed && typeNode.docs.map((doc) => (
                          <DocRow key={doc.id} doc={doc} isSelected={selectedDoc?.id === doc.id}
                            onClick={() => handleSelectDoc(doc)} docTypeColor={docTypeColor} indent />
                        ))}
                      </div>
                    );
                  })}
                </div>
              );
            })
          )}
          {displayDocs.length === 0 && (
            <div className="px-4 py-12 text-center text-gray-400 text-sm">
              {search ? "No documents match your search." : "No documents found."}
            </div>
          )}
        </div>
      </div>

      {/* Right: Selected document detail + viewer */}
      {selectedDoc ? (
        <div className="flex-1 flex flex-col bg-gray-50">
          {/* Doc info bar — action buttons get bigger tap targets on mobile.
              Close (×) is redundant with the back arrow on mobile so it's
              md+ only. */}
          <div className="flex items-center justify-between px-2 md:px-4 py-2 bg-white border-b border-gray-200 gap-1">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              {/* Mobile back button */}
              <button onClick={() => { setSelectedDoc(null); setViewerUrl(null); }}
                className="md:hidden flex items-center justify-center min-w-[40px] min-h-[40px] text-gray-500 hover:text-gray-700 rounded-md hover:bg-gray-100"
                aria-label="Back to list">
                <ChevronLeftIcon className="w-5 h-5" />
              </button>
              <DocumentTextIcon className="hidden md:inline w-4 h-4 flex-shrink-0" style={{ color: brandColor }} />
              <span className="text-sm font-medium text-gray-900 truncate">{selectedDoc.title || selectedDoc.filename}</span>
            </div>
            <div className="flex items-center gap-0.5 flex-shrink-0">
              <button onClick={() => { setChatDoc(selectedDoc); setShowChat(true); }}
                className="flex items-center justify-center min-w-[40px] min-h-[40px] md:min-w-0 md:min-h-0 md:p-1.5 text-gray-400 hover:text-purple-600 rounded-md hover:bg-gray-100" title="Chat" aria-label="Chat about this doc">
                <ChatBubbleLeftRightIcon className="w-5 h-5 md:w-4 md:h-4" />
              </button>
              {viewerUrl && (
                <a href={viewerUrl} target="_blank" rel="noopener noreferrer"
                  className="flex items-center justify-center min-w-[40px] min-h-[40px] md:min-w-0 md:min-h-0 md:p-1.5 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100" title="Open in new tab" aria-label="Open in new tab">
                  <ArrowTopRightOnSquareIcon className="w-5 h-5 md:w-4 md:h-4" />
                </a>
              )}
              {viewerUrl && (
                <a href={viewerUrl} download={selectedDoc.filename}
                  className="hidden md:flex items-center justify-center p-1.5 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100" title="Download" aria-label="Download">
                  <ArrowDownTrayIcon className="w-4 h-4" />
                </a>
              )}
              <button onClick={() => { if (confirm("Delete?")) deleteMutation.mutate(selectedDoc.id); }}
                className="flex items-center justify-center min-w-[40px] min-h-[40px] md:min-w-0 md:min-h-0 md:p-1.5 text-gray-400 hover:text-red-500 rounded-md hover:bg-gray-100" title="Delete" aria-label="Delete">
                <TrashIcon className="w-5 h-5 md:w-4 md:h-4" />
              </button>
              <button onClick={() => { setSelectedDoc(null); setViewerUrl(null); }}
                className="hidden md:flex items-center justify-center p-1.5 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100" aria-label="Close">
                <XMarkIcon className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="flex-1 flex flex-col overflow-hidden">
            {/* AI Summary */}
            {selectedDoc.notes && (
              <div className="bg-white border-b border-gray-200 px-3 md:px-5 py-3 md:py-4">
                <div className="flex items-center gap-2 mb-2">
                  <SparklesIcon className="w-4 h-4" style={{ color: brandColor }} />
                  <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: brandColor }}>AI Summary</h3>
                </div>
                <p className="text-sm text-gray-700 leading-relaxed">{selectedDoc.notes}</p>
                <div className="flex flex-wrap gap-2 mt-3">
                  {selectedDoc.doc_type && (
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${docTypeColor(selectedDoc.doc_type)}`}>
                      {selectedDoc.doc_type}
                    </span>
                  )}
                  {selectedDoc.category && (
                    <span className="px-2 py-0.5 rounded-full text-xs capitalize" style={{ backgroundColor: `${brandColor}10`, color: brandColor }}>
                      {selectedDoc.category}
                    </span>
                  )}
                  {displayFiscalYear(selectedDoc.fiscal_year) && (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-600">
                      <CalendarIcon className="w-3 h-3 inline mr-0.5" />FY {displayFiscalYear(selectedDoc.fiscal_year)}
                    </span>
                  )}
                  <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-500">
                    {formatBytes(selectedDoc.file_size)}
                  </span>
                  {selectedDoc.department && (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-500">
                      {selectedDoc.department}
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Document viewer — PDFs use react-pdf with skip-to-match + highlight */}
            <div className="flex-1 bg-gray-900 overflow-hidden">
              {viewerUrl ? (
                selectedDoc.filename.toLowerCase().endsWith(".pdf") ? (
                  <PDFViewer
                    isOpen
                    embedded
                    fileUrl={viewerUrl}
                    filename={selectedDoc.title || selectedDoc.filename}
                    onClose={() => { setSelectedDoc(null); setViewerUrl(null); }}
                    highlightTerm={isSearching ? search : undefined}
                  />
                ) : /\.(png|jpg|jpeg|gif|webp)$/i.test(selectedDoc.filename) ? (
                  <div className="w-full h-full flex items-center justify-center p-8">
                    <img src={viewerUrl} alt={selectedDoc.filename} className="max-w-full max-h-full object-contain" />
                  </div>
                ) : /\.(mp3|m4a|wav|ogg|oga|aac|flac|webm)$/i.test(selectedDoc.filename) ? (
                  <div className="w-full h-full flex items-center justify-center p-8">
                    <audio controls preload="metadata" src={viewerUrl} className="w-full max-w-xl" />
                  </div>
                ) : /\.(mp4|mov|m4v)$/i.test(selectedDoc.filename) ? (
                  <video controls preload="metadata" src={viewerUrl} className="w-full h-full bg-black" />
                ) : /\.(docx?|xlsx?|pptx?)$/i.test(selectedDoc.filename) && /^https:/.test(viewerUrl) ? (
                  // Office Online renders Word/Excel/PowerPoint from a public
                  // URL; the presigned S3 URL qualifies for its 1h lifetime.
                  <iframe
                    title={selectedDoc.filename}
                    src={`https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(viewerUrl)}`}
                    className="w-full h-full bg-white border-0"
                  />
                ) : (
                  <div className="flex items-center justify-center h-full p-8">
                    <div className="bg-white rounded-lg p-6 text-center max-w-sm">
                      <DocumentTextIcon className="w-10 h-10 mx-auto mb-3" style={{ color: brandColor }} />
                      <p className="text-sm text-gray-700 mb-1">
                        {/\.wma$/i.test(selectedDoc.filename)
                          ? "Windows Media Audio can't play in the browser."
                          : "This file type can't be previewed here."}
                      </p>
                      <p className="text-xs text-gray-500 mb-4">Download it to open in a desktop app.</p>
                      <a href={viewerUrl} download={selectedDoc.filename}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-white text-sm font-medium hover:opacity-90"
                        style={{ backgroundColor: brandColor }}>
                        <ArrowDownTrayIcon className="w-4 h-4" />
                        Download {formatBytes(selectedDoc.file_size)}
                      </a>
                    </div>
                  </div>
                )
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm px-6 text-center">
                  {viewerError ? `Couldn't load this file: ${viewerError}` : "Loading..."}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        // Library overview is desktop-only — on mobile the right side would
        // share screen with the doc list as a 50/50 split, which is unusable.
        // Mobile users see only the doc list; if they want stats they can
        // rotate to landscape or use a tablet.
        documents && documents.length > 0 && (
          <div className="hidden md:flex flex-1 min-w-0">
            <LibraryOverview
              documents={documents}
              facets={facets}
              onPickYear={(y) => setYearFilter(y)}
              onPickEntity={(c) => setEntityFilter(c)}
              onPickType={(t) => setDocTypeFilter(t)}
            />
          </div>
        )
      )}

      {/* Modals */}
      {showUpload && (
        <UploadModal projectId="default" onClose={() => setShowUpload(false)} />
      )}
      {showChat && chatDoc && (
        <DocumentChatModal document={chatDoc} isOpen={showChat} onClose={() => setShowChat(false)} />
      )}
      {showNewProject && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-4">Create Project</h2>
            <div className="space-y-4">
              <input type="text" value={newProjectName} onChange={(e) => setNewProjectName(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="Project name" />
              <select value={newProjectType} onChange={(e) => setNewProjectType(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
                <option value="">General</option>
                <option value="town">Town</option>
                <option value="school">School District</option>
              </select>
              <div className="flex justify-end gap-3">
                <button onClick={() => setShowNewProject(false)} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
                <button onClick={() => createProjectMutation.mutate()} disabled={!newProjectName}
                  className="px-4 py-2 text-sm text-white rounded-lg hover:opacity-90 disabled:opacity-50"
                  style={{ backgroundColor: brandColor }}>Create</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function renderMarked(snippet: string, key: string | number) {
  // Backend returns snippets with <<MARK>>...<</MARK>> so we can apply our own
  // styling without trusting raw HTML from the DB. Replace with safe spans.
  const parts = snippet.split(/(<<MARK>>|<<\/MARK>>)/);
  let inMark = false;
  return (
    <span key={key}>
      {parts.map((p, i) => {
        if (p === "<<MARK>>") { inMark = true; return null; }
        if (p === "<</MARK>>") { inMark = false; return null; }
        if (inMark) return <mark key={i} className="bg-amber-200 text-amber-900 px-0.5 rounded">{p}</mark>;
        return <span key={i}>{p}</span>;
      })}
    </span>
  );
}

function DocRow({
  doc, isSelected, onClick, docTypeColor, indent,
  snippet, matchType, additionalSnippets, pages, matchCount, summary,
}: {
  doc: Document; isSelected: boolean; onClick: () => void;
  docTypeColor: (t: string | null) => string; indent?: boolean;
  snippet?: string | null;
  matchType?: "phrase" | "fts" | "hybrid" | "filename";
  additionalSnippets?: string[];
  pages?: number[];
  matchCount?: number;
  /** AI-generated doc summary (notes). Rendered as italic gray context above
   *  the highlighted matching-text snippet so users can tell what the doc IS,
   *  separate from where the search hit it. */
  summary?: string | null;
}) {
  const renderedSnippet = useMemo(() => (snippet ? renderMarked(snippet, "p") : null), [snippet]);
  const renderedAdditional = useMemo(
    () => (additionalSnippets || []).slice(0, 2).map((s, i) => renderMarked(s, i)),
    [additionalSnippets],
  );

  return (
    <button onClick={onClick}
      className={`w-full text-left ${indent ? "pl-12" : "pl-4"} pr-4 py-2.5 border-b border-gray-100 hover:bg-gray-50 transition-colors ${
        isSelected ? "border-l-4 bg-gray-50" : ""
      }`}
      style={isSelected ? { borderLeftColor: brandColor, backgroundColor: `${brandColor}08` } : {}}>
      <div className="flex items-start gap-2 min-w-0">
        <DocumentTextIcon className="w-3.5 h-3.5 text-gray-400 flex-shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="text-sm text-gray-900 truncate flex-1 font-medium">
              {doc.title || doc.filename}
            </p>
            {matchType === "phrase" && (
              <span className="text-[9px] uppercase tracking-wide bg-amber-100 text-amber-700 px-1 rounded">phrase</span>
            )}
            {matchType === "hybrid" && (
              <span className="text-[9px] uppercase tracking-wide bg-emerald-100 text-emerald-700 px-1 rounded" title="Semantic + keyword + rerank">hybrid</span>
            )}
            {matchType === "filename" && (
              <span className="text-[9px] uppercase tracking-wide bg-gray-100 text-gray-500 px-1 rounded">name</span>
            )}
            {matchCount && matchCount > 1 && (
              <span className="text-[9px] uppercase tracking-wide bg-blue-50 text-blue-700 px-1 rounded">
                {matchCount} matches
              </span>
            )}
          </div>
          {/* Show filename as a small secondary line ONLY when we have a real
              title (otherwise the title field above already shows the filename
              and we'd be duplicating). */}
          {doc.title && (
            <p className="text-[10px] text-gray-400 truncate mt-0.5 font-mono">{doc.filename}</p>
          )}
          <div className="flex items-center gap-1.5 mt-0.5">
            {doc.doc_date && (
              <span className="text-[10px] text-gray-500 font-medium">{formatDocDate(doc.doc_date)}</span>
            )}
            {!doc.doc_date && displayFiscalYear(doc.fiscal_year) && (
              <span className="text-[10px] text-gray-400">FY {displayFiscalYear(doc.fiscal_year)}</span>
            )}
            {doc.department && <span className="text-[10px] text-gray-400">{doc.department}</span>}
            {pages && pages.length > 0 && (
              <span className="text-[10px] text-gray-400">
                p. {pages.slice(0, 3).join(", ")}{pages.length > 3 ? "…" : ""}
              </span>
            )}
          </div>
          {/* AI summary — what the doc IS. Distinct from the match snippet below. */}
          {summary && (
            <p className="text-[11px] text-gray-500 mt-1 leading-snug line-clamp-2 italic">{summary}</p>
          )}
          {/* Matching text — where the search hit landed, with the term highlighted */}
          {renderedSnippet && (
            <p className="text-[12px] text-gray-800 mt-1.5 leading-snug line-clamp-2 pl-2 border-l-2 border-amber-300 bg-amber-50/30 py-0.5 pr-1 rounded-sm">
              {renderedSnippet}
            </p>
          )}
          {renderedAdditional.length > 0 && (
            <div className="mt-0.5 space-y-0.5">
              {renderedAdditional.map((node, i) => (
                <p key={i} className="text-[11px] text-gray-600 leading-snug line-clamp-1 pl-2 border-l border-amber-200">
                  {node}
                </p>
              ))}
            </div>
          )}
        </div>
        <ChevronRightIcon className="w-3 h-3 text-gray-300 flex-shrink-0 mt-1.5" />
      </div>
    </button>
  );
}

function formatDocType(type: string): string {
  const labels: Record<string, string> = {
    budget: "Budgets",
    audit: "Audit Reports",
    financial_statement: "Financial Statements",
    minutes: "Meeting Minutes",
    agenda: "Agendas",
    ordinance: "Ordinances",
    resolution: "Resolutions",
    legal: "Legal Documents",
    other: "Other Documents",
  };
  return labels[type] || type.charAt(0).toUpperCase() + type.slice(1).replace(/_/g, " ");
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border border-gray-200 bg-gray-50 text-gray-700">
      <span className="capitalize">{label}</span>
      <button onClick={onClear} className="text-gray-400 hover:text-gray-700" aria-label={`Clear ${label}`}>
        <XMarkIcon className="w-3 h-3" />
      </button>
    </span>
  );
}

/**
 * Empty-state replacement for the right pane. Shows a quick library overview
 * (entity / year / type breakdowns) using the same cleaned-year logic as the
 * filters, so users see the actual coverage of the corpus instead of a
 * decorative "Select a document" placeholder. Each row is clickable —
 * picking a year/entity/type just sets the corresponding filter on the left.
 */
function LibraryOverview({
  documents,
  facets,
  onPickYear,
  onPickEntity,
  onPickType,
}: {
  documents: Document[];
  facets: { fiscal_years: Record<string, number>; doc_types: Record<string, number>; categories: Record<string, number> } | undefined;
  onPickYear: (y: string) => void;
  onPickEntity: (c: string) => void;
  onPickType: (t: string) => void;
}) {
  const municipality = useMunicipality();
  const ENTITIES = useMemo(() => buildEntities(municipality), [municipality]);
  const entityOfDoc = (d: Document) => entityOf(d, ENTITIES, municipality);

  const total = documents.length;

  // Year coverage from cleaned values — count docs with no clean year so the
  // user can see how much data needs backfilling.
  const yearStats = useMemo(() => {
    const counts: Record<string, number> = {};
    let missing = 0;
    for (const d of documents) {
      const y = displayFiscalYear(d.fiscal_year);
      if (!y) missing++;
      else counts[y] = (counts[y] || 0) + 1;
    }
    return {
      missing,
      sorted: Object.entries(counts).sort((a, b) => b[0].localeCompare(a[0])),
    };
  }, [documents]);

  const entityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const d of documents) {
      const k = entityOfDoc(d);
      counts[k] = (counts[k] || 0) + 1;
    }
    return counts;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents, ENTITIES]);

  const topTypes = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const d of documents) {
      const k = d.doc_type || "other";
      counts[k] = (counts[k] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [documents]);

  const max = (entries: [string, number][]) => entries.reduce((m, [, v]) => Math.max(m, v), 0);
  const yearMax = max(yearStats.sorted);

  return (
    <div className="flex-1 overflow-y-auto bg-gray-50">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h2 className="text-2xl font-semibold text-gray-900">Library overview</h2>
          <p className="text-sm text-gray-500 mt-1">
            {total.toLocaleString()} documents · click any row to filter the list on the left
          </p>
        </div>

        {/* Entity breakdown */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-3">By entity</h3>
          <div className="grid grid-cols-3 gap-3">
            {Object.entries(ENTITIES)
              .filter(([key]) => entityCounts[key])
              .map(([key, e]) => [key, e.short, e.icon] as const)
              .map(([key, label, Icon]) => (
              <button key={key} onClick={() => onPickEntity(key === "general" ? "" : key)}
                className="flex items-start gap-3 p-3 rounded-md border border-gray-100 hover:border-gray-300 hover:bg-gray-50 text-left transition-colors">
                <Icon className="w-5 h-5 mt-0.5 flex-shrink-0" style={{ color: brandColor }} />
                <div className="min-w-0">
                  <div className="text-2xl font-semibold text-gray-900">{(entityCounts[key] || 0).toLocaleString()}</div>
                  <div className="text-xs text-gray-500">{label}</div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Year coverage */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">By fiscal year</h3>
            {yearStats.missing > 0 && (
              <span className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded">
                {yearStats.missing.toLocaleString()} without a year
              </span>
            )}
          </div>
          {yearStats.sorted.length === 0 ? (
            <div className="text-sm text-gray-400 italic">No years detected yet.</div>
          ) : (
            <div className="space-y-1.5">
              {yearStats.sorted.slice(0, 12).map(([year, count]) => {
                const pct = yearMax ? (count / yearMax) * 100 : 0;
                return (
                  <button key={year} onClick={() => onPickYear(year)}
                    className="w-full flex items-center gap-3 px-2 py-1 rounded hover:bg-gray-50 group">
                    <span className="text-xs text-gray-700 w-20 text-left font-mono">FY {year}</span>
                    <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: brandColor }} />
                    </div>
                    <span className="text-xs text-gray-500 w-12 text-right tabular-nums">{count}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Top doc types */}
        {topTypes.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-3">Top document types</h3>
            <div className="grid grid-cols-2 gap-2">
              {topTypes.map(([type, count]) => (
                <button key={type} onClick={() => onPickType(type)}
                  className="flex items-center justify-between px-3 py-2 rounded border border-gray-100 hover:border-gray-300 hover:bg-gray-50 transition-colors">
                  <span className="text-sm text-gray-700 capitalize">{type.replace(/_/g, " ")}</span>
                  <span className="text-xs text-gray-500 tabular-nums">{count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
