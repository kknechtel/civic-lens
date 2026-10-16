/**
 * Presentations API client.
 */

const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "");

// Direct EC2 endpoint for slow LLM-backed calls (from-chat structuring,
// ai-edit, etc.). Bypasses Amplify SSR's hard 30s response timeout.
const API_DIRECT =
  process.env.NEXT_PUBLIC_API_DIRECT_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("ah_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  Object.assign(headers, authHeaders());
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

/** Same as `request`, but hits the direct EC2 endpoint to escape Amplify's 30s SSR timeout. */
async function requestDirect<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  Object.assign(headers, authHeaders());
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  const base = API_DIRECT || API_BASE;
  const res = await fetch(`${base}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export type SectionKind = "narrative" | "table" | "attachment" | "react_component";

export interface DeckSection {
  id: string;
  kind: SectionKind;
  title?: string;
  body?: string;
  headers?: string[];
  rows?: string[][];
  caption?: string;
  attachment_id?: string;
  /** TSX source for react_component sections — sandboxed via react-live. */
  tsx?: string;
  /** Structured data exposed to the TSX as the `data` identifier. */
  data?: unknown;
  /** Optional cover-banner subtitle (only used when kind='cover-meta'-like). */
  subtitle?: string;
  /** Optional date string for cover banners. */
  date?: string;
  /** SourceChip — shown above the heading. Filename takes priority over URL. */
  source_label?: string;
  source_url?: string;
  source_filename?: string;
  /** When true, a section is treated as the cover banner — title + subtitle
   *  fold into the deck-level cover instead of rendering as a normal section. */
  is_cover?: boolean;
}

export interface DeckAttachment {
  id: string;
  document_id: string;
  filename: string;
  caption?: string | null;
}

export interface FactCheckRecord {
  ran_at: string;
  summary: { supported: number; partial: number; unsupported: number; unresolved: number; no_source: number };
  results: Array<{
    section_id: string | null;
    kind: "filename" | "doc_id";
    id: string;
    label: string;
    verdict: "supported" | "partial" | "unsupported" | "unresolved" | "no_source";
    evidence_quote: string;
    claim: string;
    missing: string[];
    conflicting: string[];
  }>;
}

export interface DisclosureConfig {
  enabled: boolean;
  is_draft: boolean;
  custom_text: string | null;
}

export interface Presentation {
  id: string;
  title: string;
  slug: string | null;
  public_slug: string | null;
  status: "draft" | "published" | "archived";
  sections: DeckSection[];
  attachments: DeckAttachment[];
  theme: Record<string, unknown>;
  last_fact_check: FactCheckRecord | null;
  disclosure?: DisclosureConfig | null;
  has_password: boolean;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  is_owner?: boolean;
  share_role?: "viewer" | "editor" | null;
}

export interface VersionSummary {
  version_no: number;
  title: string;
  published_at: string | null;
  published_by: string | null;
  is_current_public: boolean;
  rolled_back_from_version_no: number | null;
  section_count: number;
  doc_snapshot_count: number;
}

export interface ChangesSincePublish {
  ever_published: boolean;
  title_changed: boolean;
  sections_added: number;
  sections_removed: number;
  sections_changed: number;
  total_changes: number;
}

export interface CitationAuditRow {
  id: string;
  labels: string[];
  label: string | null;
  filename: string | null;
  found: boolean;
  mismatch_score: number;
  looks_mismatched: boolean;
  size_bytes: number | null;
}

export interface CitationAuditResponse {
  presentation_id: string;
  total_citations: number;
  missing: number;
  likely_mismatched: number;
  rows: CitationAuditRow[];
}

export const listPresentations = () => request<Presentation[]>("/api/presentations");
export const getPresentation = (id: string) => request<Presentation>(`/api/presentations/${id}`);

export const createPresentation = (title: string) =>
  request<Presentation>("/api/presentations", { method: "POST", body: JSON.stringify({ title }) });

/** Build a new presentation from a chat transcript (assistant structures it
 *  into 4-8 sections, preserving citations). Routed through API_DIRECT
 *  because the Claude structuring call easily exceeds Amplify's 30s SSR
 *  timeout. */
export const createPresentationFromChat = (
  messages: { role: string; content: string }[],
  title_hint?: string,
) =>
  requestDirect<Presentation>("/api/presentations/from-chat", {
    method: "POST",
    body: JSON.stringify({ messages, title_hint }),
  });

export const updatePresentation = (id: string, patch: Partial<Pick<Presentation, "title" | "sections" | "attachments" | "theme">>) =>
  request<Presentation>(`/api/presentations/${id}`, { method: "PUT", body: JSON.stringify(patch) });

export const deletePresentation = (id: string) =>
  request<{ ok: boolean }>(`/api/presentations/${id}`, { method: "DELETE" });

export const addSection = (id: string, section: Partial<DeckSection> & { kind: SectionKind; title: string; after_section_id?: string }) =>
  request<Presentation>(`/api/presentations/${id}/section`, { method: "POST", body: JSON.stringify(section) });

export const patchSection = (id: string, sectionId: string, patch: Partial<DeckSection>) =>
  request<Presentation>(`/api/presentations/${id}/section/${sectionId}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteSection = (id: string, sectionId: string) =>
  request<Presentation>(`/api/presentations/${id}/section/${sectionId}`, { method: "DELETE" });

export const addAttachment = (id: string, document_id: string, caption?: string) =>
  request<Presentation>(`/api/presentations/${id}/attachments`, { method: "POST", body: JSON.stringify({ document_id, caption }) });

export const removeAttachment = (id: string, attId: string) =>
  request<Presentation>(`/api/presentations/${id}/attachments/${attId}`, { method: "DELETE" });

export const publishPresentation = (id: string) =>
  request<Presentation>(`/api/presentations/${id}/publish`, { method: "POST" });
export const unpublishPresentation = (id: string) =>
  request<Presentation>(`/api/presentations/${id}/unpublish`, { method: "POST" });

export const setPublicPassword = (id: string, password: string) =>
  request<Presentation>(`/api/presentations/${id}/password`, { method: "POST", body: JSON.stringify({ password }) });

export const factCheckPresentation = (id: string) =>
  request<FactCheckRecord>(`/api/presentations/${id}/fact-check`, { method: "POST" });

/** Public, no-auth fetch. The public-viewer page uses these. */
export async function fetchPublicMeta(slug: string): Promise<{ title: string; has_password: boolean }> {
  const res = await fetch(`${API_BASE}/api/presentations/public/${slug}/meta`);
  if (!res.ok) throw new Error("Not found");
  return res.json();
}
export async function fetchPublicDeck(slug: string, password?: string): Promise<{
  title: string; sections: DeckSection[]; attachments: DeckAttachment[];
  theme: Record<string, unknown>; published_at: string | null;
}> {
  const headers: Record<string, string> = {};
  if (password) headers["X-Deck-Password"] = password;
  const res = await fetch(`${API_BASE}/api/presentations/public/${slug}`, { headers });
  if (res.status === 401) throw new Error("password_required");
  if (!res.ok) throw new Error("Not found");
  return res.json();
}

/** Resolve a [source: filename.pdf] citation in a published deck to a signed
 *  S3 URL. Only filenames cited in the deck (or in attachments) are resolvable. */
export async function fetchPublicCitation(slug: string, filename: string, password?: string): Promise<{ url: string; filename: string }> {
  const headers: Record<string, string> = {};
  if (password) headers["X-Deck-Password"] = password;
  const qs = new URLSearchParams({ filename }).toString();
  const res = await fetch(`${API_BASE}/api/presentations/public/${slug}/citation?${qs}`, { headers });
  if (!res.ok) throw new Error(`Citation lookup failed: ${res.status}`);
  return res.json();
}

// ─── Versions / publish history ──────────────────────────────────────────

export const listVersions = (id: string) =>
  request<{ versions: VersionSummary[] }>(`/api/presentations/${id}/versions`);

export const getVersion = (id: string, versionNo: number) =>
  request<VersionSummary & {
    sections: DeckSection[];
    attachments: DeckAttachment[];
    disclosure: DisclosureConfig | null;
    doc_snapshots: Record<string, unknown>;
  }>(`/api/presentations/${id}/versions/${versionNo}`);

export const rollbackToVersion = (id: string, versionNo: number) =>
  request<{ new_version_no: number; rolled_back_from_version_no: number; is_current_public: boolean }>(
    `/api/presentations/${id}/rollback-to/${versionNo}`,
    { method: "POST" },
  );

export const changesSincePublish = (id: string) =>
  request<ChangesSincePublish>(`/api/presentations/${id}/changes-since-publish`);

// ─── Citation audit ──────────────────────────────────────────────────────

export const auditCitations = (id: string) =>
  request<CitationAuditResponse>(`/api/presentations/${id}/audit-citations`);

export type CitationFix =
  | { id: string; action: "strip"; section_id?: string }
  | { id: string; action: "swap"; new_id: string; section_id?: string };

export const applyCitationFixes = (id: string, fixes: CitationFix[]) =>
  request<{ edits: number; sections_changed: number }>(
    `/api/presentations/${id}/fix-citations`,
    { method: "POST", body: JSON.stringify({ fixes }) },
  );

// ─── Disclosure (public viewer first-visit modal) ────────────────────────

export const setDisclosure = (id: string, body: DisclosureConfig) =>
  request<DisclosureConfig>(`/api/presentations/${id}/disclosure`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

// ─── Comments (review threads) ───────────────────────────────────────────

export interface CommentAnchor {
  quote?: string;
  prefix?: string;
  suffix?: string;
}

export interface CommentRecord {
  id: string;
  presentation_id: string;
  section_id: string | null;
  parent_comment_id: string | null;
  author_email: string | null;
  author_name: string | null;
  body: string;
  resolved: boolean;
  resolved_by_email: string | null;
  anchor?: CommentAnchor | null;
  created_at: string;
  updated_at: string | null;
}

export const listComments = (id: string) =>
  request<CommentRecord[]>(`/api/presentations/${id}/comments`);

export const addComment = (id: string, body: {
  section_id: string | null;
  body: string;
  parent_comment_id?: string | null;
  anchor?: CommentAnchor | null;
}) =>
  request<CommentRecord>(`/api/presentations/${id}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const patchComment = (id: string, commentId: string, body: { body?: string; resolved?: boolean }) =>
  request<CommentRecord>(`/api/presentations/${id}/comments/${commentId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteComment = (id: string, commentId: string) =>
  request<{ ok: boolean }>(`/api/presentations/${id}/comments/${commentId}`, { method: "DELETE" });
