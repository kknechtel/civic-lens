/**
 * Civic Lens API Client
 */

// In browser: use relative URLs so Next.js rewrites proxy to backend (avoids mixed content)
// Server-side: use full URL
const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "");

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ah_token");
}

/** Public helper: returns Authorization header dict for direct fetch() calls. */
export function getAuthToken(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// ─── Auth ─────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  display_name?: string | null;
  picture_url?: string | null;
  is_admin: boolean;
  is_active: boolean;
  must_change_password?: boolean;
}

export async function updateProfile(payload: {
  display_name?: string;
  full_name?: string;
}): Promise<User> {
  return request<User>("/api/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function changePassword(newPassword: string) {
  return request<{ detail: string }>("/api/auth/change-password", {
    method: "POST", body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function login(email: string, password: string) {
  const data = await request<{ access_token: string; pending_approval?: boolean }>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ email, password }),
  });
  localStorage.setItem("ah_token", data.access_token);
  return data;
}

/** Exchange a Google ID token (from Google Identity Services in the browser)
 *  for our JWT. The backend verifies the token server-side via the
 *  GOOGLE_OAUTH_CLIENT_ID env var. */
export async function loginWithGoogle(idToken: string) {
  const data = await request<{ access_token: string; pending_approval?: boolean }>("/api/auth/google", {
    method: "POST", body: JSON.stringify({ id_token: idToken }),
  });
  localStorage.setItem("ah_token", data.access_token);
  return data;
}

/** Self-service signup for the events-app. Creates a User with is_active=false
 *  pending admin approval. Returns the JWT + pending_approval=true so the SPA
 *  can route straight to the "pending" screen. */
export async function signup(email: string, password: string, fullName?: string) {
  const data = await request<{ access_token: string; pending_approval?: boolean }>("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  localStorage.setItem("ah_token", data.access_token);
  return data;
}

export async function getMe(): Promise<User> { return request<User>("/api/auth/me"); }
export function logout() { localStorage.removeItem("ah_token"); }

// ─── Projects ─────────────────────────────────────────────────────────

export interface Project {
  id: string; name: string; description: string | null;
  entity_type: string | null; document_count: number; created_at: string;
  is_owner?: boolean;
  share_role?: "viewer" | "editor" | null;
}

export async function getProjects(): Promise<Project[]> { return request<Project[]>("/api/projects/"); }
export async function createProject(name: string, description?: string, entity_type?: string): Promise<Project> {
  return request<Project>("/api/projects/", { method: "POST", body: JSON.stringify({ name, description, entity_type }) });
}
export async function deleteProject(projectId: string) { return request(`/api/projects/${projectId}`, { method: "DELETE" }); }

// ─── Sharing (projects + presentations) ───────────────────────────────

export interface DirectoryUser {
  id: string;
  email: string;
  full_name: string | null;
}

export interface ShareEntry {
  user_id: string;
  email: string;
  full_name: string | null;
  role: "viewer" | "editor";
}

export async function getUserDirectory(): Promise<DirectoryUser[]> {
  return request<DirectoryUser[]>("/api/auth/directory");
}

export async function listProjectShares(projectId: string): Promise<ShareEntry[]> {
  return request<ShareEntry[]>(`/api/projects/${projectId}/shares`);
}
export async function addProjectShare(projectId: string, userId: string, role: "viewer" | "editor"): Promise<ShareEntry> {
  return request<ShareEntry>(`/api/projects/${projectId}/shares`, {
    method: "POST", body: JSON.stringify({ user_id: userId, role }),
  });
}
export async function removeProjectShare(projectId: string, userId: string) {
  return request(`/api/projects/${projectId}/shares/${userId}`, { method: "DELETE" });
}

export async function listPresentationShares(presentationId: string): Promise<ShareEntry[]> {
  return request<ShareEntry[]>(`/api/presentations/${presentationId}/shares`);
}
export async function addPresentationShare(presentationId: string, userId: string, role: "viewer" | "editor"): Promise<ShareEntry> {
  return request<ShareEntry>(`/api/presentations/${presentationId}/shares`, {
    method: "POST", body: JSON.stringify({ user_id: userId, role }),
  });
}
export async function removePresentationShare(presentationId: string, userId: string) {
  return request(`/api/presentations/${presentationId}/shares/${userId}`, { method: "DELETE" });
}

// ─── Documents ────────────────────────────────────────────────────────

export interface Document {
  id: string; project_id: string; filename: string; original_filename: string;
  s3_key: string; file_size: number; content_type: string | null;
  doc_type: string | null; category: string | null; department: string | null;
  fiscal_year: string | null; status: string; notes: string | null; created_at: string;
  /** Human-readable title (e.g. "Planning Board — Meeting Minutes February 4, 2026").
   *  Populated by services.title_extractor; null on docs not yet backfilled. */
  title?: string | null;
  doc_date?: string | null;
  municipality?: string | null;
}

/** Admin: trigger the title/department/date backfill across the corpus. */
export async function backfillTitles(params?: {
  limit?: number;
  only_missing?: boolean;
  overwrite_department?: boolean;
}): Promise<{
  titles_updated: number;
  departments_updated: number;
  doc_dates_updated: number;
  skipped_no_signal: number;
  examples: Array<{ id: string; filename: string; title: string | null; department: string | null; doc_date: string | null }>;
}> {
  const qs = new URLSearchParams();
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.only_missing !== undefined) qs.set("only_missing", String(params.only_missing));
  if (params?.overwrite_department !== undefined) qs.set("overwrite_department", String(params.overwrite_department));
  return request(`/api/documents/backfill-titles?${qs.toString()}`, { method: "POST" });
}

/** Lightweight: fetches one page, ordered by created_at desc.
 *  Use this for dashboard widgets that just need the latest N — getDocuments()
 *  fans out one request per page and is overkill when you only want 5-10 rows. */
export async function getRecentDocuments(limit = 10): Promise<Document[]> {
  return request<Document[]>(`/api/documents/?limit=${limit}&offset=0`);
}

export async function getDocuments(params?: { project_id?: string; category?: string; doc_type?: string }): Promise<Document[]> {
  // Fetch all pages — paginated to fit Amplify Lambda response limits
  const PAGE_SIZE = 200;
  const baseQuery = new URLSearchParams();
  if (params?.project_id) baseQuery.set("project_id", params.project_id);
  if (params?.category) baseQuery.set("category", params.category);
  if (params?.doc_type) baseQuery.set("doc_type", params.doc_type);

  // Get total count first
  const countRes = await request<{ count: number }>(
    `/api/documents/count${baseQuery.toString() ? `?${baseQuery}` : ""}`
  );
  const total = countRes.count;

  // Fetch all pages in parallel
  const pageCount = Math.ceil(total / PAGE_SIZE);
  const pagePromises: Promise<Document[]>[] = [];
  for (let i = 0; i < pageCount; i++) {
    const q = new URLSearchParams(baseQuery);
    q.set("limit", String(PAGE_SIZE));
    q.set("offset", String(i * PAGE_SIZE));
    pagePromises.push(request<Document[]>(`/api/documents/?${q}`));
  }
  const pages = await Promise.all(pagePromises);
  return pages.flat();
}

/**
 * Upload a single file directly to S3 via presigned URL (bypasses Amplify proxy size limits).
 */
export async function uploadDocument(
  file: File,
  projectId: string,
  metadata?: { doc_type?: string; category?: string; fiscal_year?: string }
): Promise<Document> {
  // 1. Get presigned URL from backend
  const presigned = await request<{ upload_url: string; s3_key: string; document_id: string }>(
    "/api/documents/presigned-upload",
    {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        project_id: projectId,
        file_size: file.size,
        doc_type: metadata?.doc_type,
        category: metadata?.category,
        fiscal_year: metadata?.fiscal_year,
      }),
    }
  );

  // 2. Upload directly to S3 (no proxy involved)
  const uploadRes = await fetch(presigned.upload_url, {
    method: "PUT",
    body: file,
    headers: {
      "Content-Type": file.type || "application/octet-stream",
    },
  });
  if (!uploadRes.ok) {
    throw new Error(`S3 upload failed: ${uploadRes.status} ${uploadRes.statusText}`);
  }

  // 3. Confirm upload — backend records the doc in DB
  return request<Document>("/api/documents/confirm-upload", {
    method: "POST",
    body: JSON.stringify({
      document_id: presigned.document_id,
      s3_key: presigned.s3_key,
      filename: file.name,
      file_size: file.size,
      content_type: file.type || "application/octet-stream",
      project_id: projectId,
      doc_type: metadata?.doc_type,
      category: metadata?.category,
      fiscal_year: metadata?.fiscal_year,
    }),
  });
}

/**
 * Upload multiple files via presigned URLs.
 * Throws if any file fails — caller can inspect `error.results` for per-file status.
 */
export async function uploadMultipleDocuments(
  files: File[],
  projectId: string,
  category?: string
): Promise<{ uploaded: number; files: { filename: string; status: string }[] }> {
  const results = await Promise.allSettled(
    files.map((f) => uploadDocument(f, projectId, { category }))
  );
  const fileResults = results.map((r, i) => ({
    filename: files[i].name,
    status: r.status === "fulfilled" ? "uploaded" : `error: ${(r.reason as Error).message}`,
  }));
  const uploaded = results.filter((r) => r.status === "fulfilled").length;
  const failed = files.length - uploaded;

  if (failed > 0) {
    const failedNames = fileResults.filter((f) => f.status.startsWith("error")).map((f) => `${f.filename}: ${f.status}`).join("; ");
    const err = new Error(`${failed} of ${files.length} uploads failed. ${failedNames}`);
    (err as any).results = fileResults;
    (err as any).uploaded = uploaded;
    throw err;
  }
  return { uploaded, files: fileResults };
}

export async function getDocument(documentId: string): Promise<Document> {
  return request<Document>(`/api/documents/${documentId}`);
}

/** Map a `[source: name]` citation to a document id. Exact server-side match
 *  (filename, then title) first; fuzzy search only if that finds nothing. */
export async function resolveCitation(name: string): Promise<{ id: string; filename: string } | null> {
  try {
    return await request<{ id: string; filename: string }>(
      `/api/documents/resolve?name=${encodeURIComponent(name)}`,
    );
  } catch {
    const r = (await searchDocuments(name)).results;
    const lower = name.toLowerCase();
    const best = r.find((d) => d.filename.toLowerCase().startsWith(lower))
      || r.find((d) => d.filename.toLowerCase().includes(lower)) || r[0];
    return best ? { id: best.id, filename: best.filename } : null;
  }
}

export async function getDocumentViewUrl(documentId: string): Promise<{ url: string }> {
  const result = await request<{ url: string }>(`/api/documents/${documentId}/view-url`);
  // If URL is relative (local storage), prepend the API base
  if (result.url && result.url.startsWith("/")) {
    result.url = `${API_BASE}${result.url}`;
  }
  return result;
}

export async function updateDocument(documentId: string, update: Partial<Document>) {
  return request<Document>(`/api/documents/${documentId}`, { method: "PATCH", body: JSON.stringify(update) });
}

export async function deleteDocument(documentId: string) {
  return request(`/api/documents/${documentId}`, { method: "DELETE" });
}

// ─── Financial Analysis ───────────────────────────────────────────────

export interface FinancialStatement {
  id: string; document_id: string; entity_name: string; entity_type: string;
  statement_type: string; fiscal_year: string;
  total_revenue: number | null; total_expenditures: number | null;
  surplus_deficit: number | null; fund_balance: number | null;
  total_debt: number | null; status: string; created_at: string;
}

export interface LineItem {
  id: string; section: string | null; subsection: string | null;
  line_name: string; amount: number | null; prior_year_amount: number | null;
  budget_amount: number | null; variance: number | null;
}

export interface FinancialAnalysisResult {
  id: string; name: string; entity_type: string; analysis_type: string;
  fiscal_years: string[]; results: Record<string, any>;
  summary: string | null; created_at: string;
}

export async function getStatements(params?: { entity_type?: string; fiscal_year?: string }): Promise<FinancialStatement[]> {
  const query = new URLSearchParams();
  if (params?.entity_type) query.set("entity_type", params.entity_type);
  if (params?.fiscal_year) query.set("fiscal_year", params.fiscal_year);
  const qs = query.toString();
  return request<FinancialStatement[]>(`/api/financial/statements${qs ? `?${qs}` : ""}`);
}

export async function getStatement(statementId: string): Promise<FinancialStatement> {
  return request<FinancialStatement>(`/api/financial/statements/${statementId}`);
}

export async function getStatementLineItems(statementId: string): Promise<LineItem[]> {
  return request<LineItem[]>(`/api/financial/statements/${statementId}/line-items`);
}

export async function getStatementRawExtraction(statementId: string): Promise<Record<string, any>> {
  return request<Record<string, any>>(`/api/financial/statements/${statementId}/raw`);
}

// ─── Drill-down + anomalies (Phase 2) ─────────────────────────────────

export interface AnomalyFlag {
  code: string;
  severity: "info" | "warn" | "high";
  message: string;
  line_id?: string;
  value?: number;
}

export interface DrillResults {
  revenue?: any;
  expenditure?: any;
  debt?: any;
  fund_balance?: any;
  synthesis?: any;
}

export interface DrillResponse {
  statement_id: string;
  status: string;
  accounting_basis: string | null;
  fiscal_calendar: string | null;
  reconcile_status: string | null;
  reconcile_details: Record<string, any>;
  anomaly_flags: AnomalyFlag[];
  drill_results: DrillResults;
}

export async function runDrill(statementId: string, sync = false) {
  const qs = sync ? "?sync=true" : "";
  return request<{
    statement_id: string;
    mode: "background" | "sync";
    status?: string;
    synthesis_ok?: boolean;
    success_count?: number;
    error_count?: number;
    duration_s?: number;
    drill_results?: any;
  }>(`/api/financial/statements/${statementId}/drill${qs}`, { method: "POST" });
}

export async function drillAll(params?: { entity_type?: string; fiscal_year?: string; redrill?: boolean; concurrency?: number }) {
  const qs = new URLSearchParams();
  if (params?.entity_type) qs.set("entity_type", params.entity_type);
  if (params?.fiscal_year) qs.set("fiscal_year", params.fiscal_year);
  if (params?.redrill) qs.set("redrill", "true");
  if (params?.concurrency != null) qs.set("concurrency", String(params.concurrency));
  return request<{ queued: number; concurrency: number; statement_ids: string[] }>(
    `/api/financial/drill-all${qs.toString() ? `?${qs}` : ""}`,
    { method: "POST" },
  );
}

export async function getFinancialDiagnostics() {
  return request<{
    llm_keys: { anthropic_api_key_set: boolean; gemini_api_key_set: boolean };
    statements: { by_status: Record<string, number>; by_accounting_basis: Record<string, number>; by_entity_type: Record<string, number>; total: number };
    extraction_issues: { extracted_with_no_line_items: any[]; extracted_with_no_line_items_count: number };
    drill_issues: { drills_with_errors_count: number; drills_with_errors_sample: any[] };
    next_steps_hint: string;
  }>("/api/financial/diagnostics");
}

// ─── FY merged view ───────────────────────────────────────────────────

export interface FYView {
  entity_type: string;
  fiscal_year: string;
  primary_statement_id: string;
  primary_statement_type: string;
  primary_entity_name: string | null;
  accounting_basis: string | null;
  fiscal_calendar: string | null;
  merged: {
    total_revenue: number | null; total_revenue_source: string | null;
    total_expenditures: number | null; total_expenditures_source: string | null;
    surplus_deficit: number | null; surplus_deficit_source: string | null;
    fund_balance: number | null; fund_balance_source: string | null;
    total_debt: number | null; total_debt_source: string | null;
  };
  sources: {
    statement_id: string; statement_type: string; entity_name: string | null;
    variant: string; status: string; reconcile_status: string | null;
    has_revenue: boolean; has_expenditures: boolean;
    has_fund_balance: boolean; has_debt: boolean;
    line_item_count: number;
  }[];
  merged_line_item_count: number;
  merged_line_items: any[];
  missing: { doc_types: string[]; fields: string[] };
}

export async function getFYView(entity_type: string, fiscal_year: string): Promise<FYView> {
  const qs = new URLSearchParams({ entity_type, fiscal_year });
  return request<FYView>(`/api/financial/fy-view?${qs}`);
}

export async function getDrillResults(statementId: string): Promise<DrillResponse> {
  return request<DrillResponse>(`/api/financial/statements/${statementId}/drill`);
}

export async function getStatementAnomalies(statementId: string): Promise<{ statement_id: string; anomaly_flags: AnomalyFlag[] }> {
  return request(`/api/financial/statements/${statementId}/anomalies`);
}

// ─── Contracts + Vendors (Phase 3) ────────────────────────────────────

export interface VendorSummary {
  id: string; name: string; category: string | null;
  contract_count: number; payment_total: number; created_at: string;
}

export interface ContractRow {
  id: string; vendor_id: string; vendor_name: string;
  entity_type: string; title: string; amount: number | null;
  fiscal_year: string | null; contract_type: string | null;
  awarded_date: string | null;
  authorizing_resolution: string | null;
  status: string;
}

export async function listVendors(params?: { q?: string; category?: string }): Promise<VendorSummary[]> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.category) qs.set("category", params.category);
  return request<VendorSummary[]>(`/api/contracts/vendors${qs.toString() ? `?${qs}` : ""}`);
}

export async function listContracts(params?: { entity_type?: string; fiscal_year?: string; vendor?: string; min_amount?: number }): Promise<ContractRow[]> {
  const qs = new URLSearchParams();
  if (params?.entity_type) qs.set("entity_type", params.entity_type);
  if (params?.fiscal_year) qs.set("fiscal_year", params.fiscal_year);
  if (params?.vendor) qs.set("vendor", params.vendor);
  if (params?.min_amount != null) qs.set("min_amount", String(params.min_amount));
  return request<ContractRow[]>(`/api/contracts/contracts${qs.toString() ? `?${qs}` : ""}`);
}

export async function extractFinancialData(documentId: string, entityType: string, statementType: string) {
  return request<{ statement_id: string; status: string }>("/api/financial/extract", {
    method: "POST", body: JSON.stringify({ document_id: documentId, entity_type: entityType, statement_type: statementType }),
  });
}

export async function createAnalysis(name: string, entityType: string, analysisType: string, statementIds: string[]): Promise<FinancialAnalysisResult> {
  return request<FinancialAnalysisResult>("/api/financial/analyze", {
    method: "POST", body: JSON.stringify({ name, entity_type: entityType, analysis_type: analysisType, statement_ids: statementIds }),
  });
}

export async function getAnalyses(entityType?: string): Promise<FinancialAnalysisResult[]> {
  const qs = entityType ? `?entity_type=${entityType}` : "";
  return request<FinancialAnalysisResult[]>(`/api/financial/analyses${qs}`);
}

// ─── Processing ───────────────────────────────────────────────────────

export interface ProcessingStats {
  total: number; processed: number; processing: number; uploaded: number; errors: number;
}

export async function processDocuments(params?: { document_ids?: string[]; project_id?: string }) {
  return request<{ detail: string; count: number }>("/api/processing/run", { method: "POST", body: JSON.stringify(params || {}) });
}

export async function processSingleDocument(documentId: string) {
  return request<{ detail: string }>(`/api/processing/single/${documentId}`, { method: "POST" });
}

export async function getProcessingStats(projectId?: string): Promise<ProcessingStats> {
  const qs = projectId ? `?project_id=${projectId}` : "";
  return request<ProcessingStats>(`/api/processing/stats${qs}`);
}

// ─── Search ───────────────────────────────────────────────────────────

export interface SearchResult {
  id: string; filename: string;
  /** Human-readable title (e.g. "Planning Board — Meeting Minutes February 4, 2026").
   *  Populated by services.title_extractor; may be null on docs not yet backfilled. */
  title?: string | null;
  /** ISO YYYY-MM-DD when extractable from content or filename. */
  doc_date?: string | null;
  /** AI-generated one-paragraph summary (truncated to ~240 chars).
   *  Distinct from `snippet` which highlights the matching text excerpt. */
  summary?: string | null;
  doc_type: string | null; category: string | null;
  fiscal_year: string | null; department: string | null;
  status: string; score: number; snippet: string | null;
  /** "phrase" = quoted, "hybrid" = semantic+keyword+rerank, "fts" = keyword-only,
   *  "filename" = ILIKE fallback (no chunks ingested yet). */
  match_type: "phrase" | "fts" | "hybrid" | "filename";
  /** Up to 2 more snippets from other chunks in the same doc. */
  additional_snippets?: string[] | null;
  /** Page numbers (page_start from matched chunks) — for deep-linking. */
  pages?: number[] | null;
  /** Total chunks in this doc that matched — shown as an "N matches" badge. */
  match_count?: number | null;
}

export interface ParsedFilters {
  fiscal_year: string | null;
  category: string | null;
  doc_type: string | null;
  department: string | null;
  min_amount: number | null;
  max_amount: number | null;
  /** Human-readable list of what was auto-extracted from the query
   *  (e.g. ["FY 2024", "School", "audit"]) — shown as chips. */
  hits: string[];
}

export interface SearchResponse {
  results: SearchResult[];
  /** pg_trgm suggestion when the result set is sparse. */
  did_you_mean: string | null;
  /** Filters auto-extracted from the natural-language query. */
  parsed_filters: ParsedFilters | null;
  /** UUID for analytics — pass back to /api/search/click on result open. */
  query_id: string | null;
  latency_ms: number | null;
}

export async function searchDocuments(
  query: string,
  params?: {
    project_id?: string; category?: string; doc_type?: string;
    fiscal_year?: string; department?: string; document_id?: string;
    limit?: number;
  },
): Promise<SearchResponse> {
  return request<SearchResponse>("/api/search/", {
    method: "POST", body: JSON.stringify({ query, ...params }),
  });
}

/** Tell the backend which result the user opened — relevance signal for the
 *  analytics log. Fire-and-forget; errors are swallowed. */
export async function recordSearchClick(query_id: string, document_id: string): Promise<void> {
  try {
    await request<{ ok: boolean }>("/api/search/click", {
      method: "POST",
      body: JSON.stringify({ query_id, document_id }),
    });
  } catch {
    // Click tracking is best-effort — never block the user
  }
}

export interface SearchFacets {
  doc_types: Record<string, number>;
  categories: Record<string, number>;
  fiscal_years: Record<string, number>;  // already deduped + 4-digit-sanitized server-side
  departments: Record<string, number>;   // already case-insensitive deduped server-side
}

export async function getSearchFacets(projectId?: string): Promise<SearchFacets> {
  const qs = projectId ? `?project_id=${projectId}` : "";
  return request<SearchFacets>(`/api/search/facets${qs}`);
}

// ─── Scraper ──────────────────────────────────────────────────────────

export interface SiteStats {
  status: "pending" | "running" | "done" | "error";
  documents_found: number;
  documents_uploaded: number;
  documents_skipped: number;
  errors: number;
  started_at: string | null;
  completed_at: string | null;
}

export interface ScraperStatus {
  running: boolean; current_site: string | null;
  documents_found: number; documents_uploaded: number; documents_skipped: number;
  errors: string[]; started_at: string | null; completed_at: string | null;
  per_site?: Record<string, SiteStats>;
  sites_planned?: string[];
  sites_completed?: string[];
}

export async function startScraper(sites?: string[], opts?: { projectId?: string; dryRun?: boolean; historical?: boolean }) {
  return request<{ detail: string; sites?: string[]; mode?: string }>("/api/scraper/run", {
    method: "POST",
    body: JSON.stringify({
      sites,
      project_id: opts?.projectId,
      dry_run: opts?.dryRun,
      historical: opts?.historical,
    }),
  });
}

export interface ScraperSource {
  key: string;
  site_id: string;
  name: string;
  description: string;
  note: string;
  adapter: string;
  entity: string;
  enabled: boolean;
  historical: boolean;
}

export async function getScraperSources(): Promise<ScraperSource[]> {
  return request<ScraperSource[]>("/api/scraper/sources");
}

export async function getScraperStatus(): Promise<ScraperStatus> {
  return request<ScraperStatus>("/api/scraper/status");
}

export interface ScraperRunSummary {
  id: string;
  started_at: string;
  completed_at: string | null;
  sites: string[];
  mode: string;
  triggered_by: string | null;
  documents_found: number;
  documents_uploaded: number;
  documents_skipped: number;
  errors_count: number;
  new_docs: { filename: string; source: string; category?: string; doc_type?: string; url: string }[];
}

export async function getScraperRuns(limit = 20): Promise<ScraperRunSummary[]> {
  return request<ScraperRunSummary[]>(`/api/scraper/runs?limit=${limit}`);
}

// ─── Web Search ───────────────────────────────────────────────────────

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export async function webSearch(query: string, maxResults: number = 5) {
  return request<{ results: WebSearchResult[]; query: string }>("/api/websearch/", {
    method: "POST",
    body: JSON.stringify({ query, max_results: maxResults }),
  });
}

// ─── Chat History ─────────────────────────────────────────────────────

export async function getChatHistory(sessionId: string) {
  return request<{ session_id: string; messages: { role: string; content: string; timestamp: string }[] }>(
    `/api/chat/history?session_id=${sessionId}`
  );
}

export async function getChatSessions() {
  return request<{ session_id: string; scope_type: string; message_count: number; last_activity: string; last_query: string }[]>(
    "/api/chat/sessions"
  );
}

// ─── Reports ──────────────────────────────────────────────────────────

export async function generateReport(reportType: string, entityType?: string, customPrompt?: string) {
  return fetch(`${API_BASE}/api/reports/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ report_type: reportType, entity_type: entityType, custom_prompt: customPrompt }),
  });
}

// ─── Calendar Events ──────────────────────────────────────────────────
// Four flavors live in the same table. Classification happens server-side
// in scripts/scrape_events.classify_borough_event so both apps consume
// the same bucket assignment:
//   event_type='govt'       — town business (council, planning, BOE, etc.)
//                             → only surfaced on the civic app's /calendar
//   event_type='community'  — borough fun: parades, fireworks, holidays
//                             → only surfaced on events.ahnj.info
//   event_type='live_music' — venue-scraped shows (Proving Ground, etc.)
//                             → only surfaced on events.ahnj.info
//   event_type='general'    — legacy, pre-classification. Backfill in
//                             database.py reclassifies these on next API restart.

export type EventType = "govt" | "community" | "live_music" | "general";

// Same keyword list as api/scripts/scrape_events._GOVT_KEYWORDS. Used as a
// fallback so events tagged 'general' (pre-backfill) get classified
// correctly in the UI without waiting for the API to restart.
const GOVT_KEYWORDS = [
  "council", "planning board", "commission",
  "board of education", "boe", "reorganization",
  "offices closed", "offices are closed",
  "borough hall", "town hall", "court",
  "zoning board", "shade tree",
  "environmental commission", "recreation commission",
];

/** True if the event is a govt/town meeting — either explicitly tagged or
 * matched against the keyword list (covers legacy 'general' rows). */
export function isGovtCalendarEvent(ev: { event_type?: EventType | null; title: string }): boolean {
  if (ev.event_type === "govt") return true;
  if (ev.event_type === "community" || ev.event_type === "live_music") return false;
  const t = (ev.title || "").toLowerCase();
  return GOVT_KEYWORDS.some(k => t.includes(k));
}

export interface CalendarEvent {
  id: string;
  date: string;
  title: string;
  time: string | null;
  end_time?: string | null;
  location: string | null;
  description: string | null;
  source: string;
  source_url?: string | null;
  venue?: string | null;
  city?: string | null;
  event_type?: EventType | null;
  ticket_url?: string | null;
}

export async function getCalendarEvent(id: string): Promise<CalendarEvent> {
  return request<CalendarEvent>(`/api/calendar/events/${encodeURIComponent(id)}`);
}

// ─── Event submissions (crowdsourced) ──────────────────────────────
// Anyone logged in can submit; admins approve / reject.

export type SubmissionStatus = "pending" | "approved" | "rejected";

export interface EventSubmission {
  id: string;
  submitter_user_id: string | null;
  submitter_email: string | null;
  title: string;
  event_date: string;
  event_time: string | null;
  end_time: string | null;
  venue_name: string;
  city: string | null;
  description: string | null;
  ticket_url: string | null;
  submitter_note: string | null;
  status: SubmissionStatus;
  admin_note: string | null;
  calendar_event_id: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export interface EventSubmissionCreate {
  title: string;
  event_date: string;  // YYYY-MM-DD
  event_time?: string;
  end_time?: string;
  venue_name: string;
  city?: string;
  description?: string;
  ticket_url?: string;
  submitter_note?: string;
}

export async function createEventSubmission(payload: EventSubmissionCreate): Promise<EventSubmission> {
  return request<EventSubmission>("/api/event-submissions/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listEventSubmissions(status?: SubmissionStatus): Promise<EventSubmission[]> {
  const qs = status ? `?status=${status}` : "";
  return request<EventSubmission[]>(`/api/event-submissions/${qs}`);
}

export async function approveEventSubmission(id: string): Promise<EventSubmission> {
  return request<EventSubmission>(`/api/event-submissions/${id}/approve`, { method: "POST" });
}

export async function rejectEventSubmission(id: string, reason?: string): Promise<EventSubmission> {
  return request<EventSubmission>(`/api/event-submissions/${id}/reject`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
}

export async function deleteEventSubmission(id: string): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/event-submissions/${id}`, { method: "DELETE", headers });
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Delete failed: ${res.status}`);
  }
}

// ─── Band profiles (admin-curated) ─────────────────────────────────
// Overrides the static bandGuide.ts when an admin fills in real URLs.

export interface BandProfile {
  name: string;
  facebook_url: string | null;
  instagram_url: string | null;
  website_url: string | null;
  bandsintown_url: string | null;
  bio: string | null;
  photo_url: string | null;
  /** Comma-separated ("Classic Rock, Blues"). Only ever set from a page
   *  that states it — see scripts/enrich_band_genres.py. */
  genres: string | null;
  genre_source_url: string | null;
  /** As published by the source, never computed by us. The API withholds
   *  the rating entirely when there's no source URL to attribute it to. */
  rating: number | null;
  rating_count: number | null;
  rating_source_url: string | null;
  /** A YouTube URL for the act; the band page embeds it when the shape is
   *  convertible (see lib/youtube.ts). */
  video_url: string | null;
}

/** Returns null when no curated profile exists (404 → null). */
export async function getBandProfile(name: string): Promise<BandProfile | null> {
  try {
    return await request<BandProfile>(`/api/bands/profile/${encodeURIComponent(name)}`);
  } catch (e) {
    if (e instanceof Error && /404|not yet curated|Not found/i.test(e.message)) return null;
    throw e;
  }
}

export async function upsertBandProfile(name: string, payload: Omit<BandProfile, "name">): Promise<BandProfile> {
  return request<BandProfile>(`/api/bands/profile/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ─── Event RSVPs ─────────────────────────────────────────────────────
// "I'm planning to go." Distinct from check-ins (present-tense, expires 4h).

export interface RsvpUser {
  user_id: string;
  display_name: string | null;
  picture_url: string | null;
}

export type RsvpStatus = "going" | "tentative" | "follow_up";

export interface RsvpSummary {
  event_id: string;
  count: number;                  // everyone with any status
  going_count: number;            // firm headcount (status='going')
  is_going: boolean;              // back-compat: my_status === 'going'
  my_status: RsvpStatus | null;   // caller's status, or null when not signed in / not RSVP'd
  sample_users: RsvpUser[];
}

export async function getEventRsvp(eventId: string): Promise<RsvpSummary> {
  return request<RsvpSummary>(`/api/calendar/events/${encodeURIComponent(eventId)}/rsvp`);
}

export async function rsvpToEvent(
  eventId: string,
  status: RsvpStatus = "going",
): Promise<RsvpSummary> {
  return request<RsvpSummary>(`/api/calendar/events/${encodeURIComponent(eventId)}/rsvp`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export async function unrsvpFromEvent(eventId: string): Promise<RsvpSummary> {
  return request<RsvpSummary>(`/api/calendar/events/${encodeURIComponent(eventId)}/rsvp`, {
    method: "DELETE",
  });
}

// ── Admin: ingest a venue's calendar image (Claude Vision) ────────────

export interface ParsedIngestEvent {
  date: string;             // YYYY-MM-DD
  title: string;
  time?: string | null;
  end_time?: string | null;
}

export interface IngestParseResponse {
  events: ParsedIngestEvent[];
  notes: string;
  dropped: number;
  model: string;
}

export interface IngestCommitResponse {
  inserted: number;
  skipped: number;
  events_in: number;
}

export async function parseCalendarImage(payload: {
  file: File;
  venue: string;
  city?: string;
  hint_month?: string;
}): Promise<IngestParseResponse> {
  const fd = new FormData();
  fd.append("image", payload.file);
  fd.append("venue", payload.venue);
  if (payload.city) fd.append("city", payload.city);
  if (payload.hint_month) fd.append("hint_month", payload.hint_month);
  return request<IngestParseResponse>("/api/event-ingest/parse", {
    method: "POST",
    body: fd,
  });
}

export async function commitIngestedEvents(payload: {
  venue: string;
  city?: string;
  events: ParsedIngestEvent[];
  source?: string;
}): Promise<IngestCommitResponse> {
  return request<IngestCommitResponse>("/api/event-ingest/commit", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── My calendar ────────────────────────────────────────────────────────

export interface SavedEvent {
  rsvp_id: string;
  status: RsvpStatus;
  saved_at: string;
  event_id: string;
  title: string | null;
  date: string | null;
  time: string | null;
  end_time: string | null;
  venue: string | null;
  city: string | null;
  event_type: string | null;
  ticket_url: string | null;
}

export async function getMyCalendar(opts: { upcoming_only?: boolean } = {}): Promise<SavedEvent[]> {
  const qs = opts.upcoming_only === false ? "?upcoming_only=false" : "";
  return request<SavedEvent[]>(`/api/calendar/me/calendar${qs}`);
}

export async function getCalendarEvents(
  year?: number,
  month?: number,
  opts: {
    event_type?: EventType;
    city?: string;
    venue?: string;
    q?: string;
    upcoming_only?: boolean;
    limit?: number;
  } = {},
): Promise<CalendarEvent[]> {
  const params = new URLSearchParams();
  if (year) params.set("year", String(year));
  if (month) params.set("month", String(month));
  if (opts.event_type) params.set("event_type", opts.event_type);
  if (opts.city) params.set("city", opts.city);
  if (opts.venue) params.set("venue", opts.venue);
  if (opts.q) params.set("q", opts.q);
  if (opts.upcoming_only) params.set("upcoming_only", "true");
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<CalendarEvent[]>(`/api/calendar/events${qs ? `?${qs}` : ""}`);
}

/** Free-text event search. Returns up to `limit` hits (default 100),
 *  sorted by date ascending. Set upcoming_only=true to skip past events. */
export async function searchEvents(
  q: string,
  opts: { upcoming_only?: boolean; limit?: number } = {},
): Promise<CalendarEvent[]> {
  return getCalendarEvents(undefined, undefined, {
    q, upcoming_only: opts.upcoming_only, limit: opts.limit ?? 100,
  });
}

// ─── Admin ────────────────────────────────────────────────────────────

export interface AdminStats {
  total_users: number; total_projects: number; total_documents: number; total_statements: number;
  pending_users: number;
  documents_ocrd?: number;
  documents_vector_indexed?: number;
  cost_last_30d_usd?: number;
  cost_total_usd?: number;
  llm_calls_last_30d?: number;
}

export interface Invite {
  id: string; token: string; email: string | null; is_used: boolean;
  used_by: string | null; expires_at: string; created_at: string;
}

export interface AdminUser extends User {
  created_at: string;
}

export async function getAdminStats(): Promise<AdminStats> { return request<AdminStats>("/api/admin/stats"); }
export async function getAdminUsers() { return request<AdminUser[]>("/api/admin/users"); }
export async function approveUser(userId: string) { return request(`/api/admin/users/${userId}/approve`, { method: "PATCH" }); }
export async function toggleUserActive(userId: string) { return request(`/api/admin/users/${userId}/toggle-active`, { method: "PATCH" }); }
export async function toggleUserAdmin(userId: string) { return request(`/api/admin/users/${userId}/toggle-admin`, { method: "PATCH" }); }
export async function deleteUser(userId: string) { return request(`/api/admin/users/${userId}`, { method: "DELETE" }); }
export async function createInvite(email?: string, expiresHours: number = 72) {
  return request<{ token: string; invite_url: string; email: string | null; expires_at: string }>("/api/admin/invites", {
    method: "POST", body: JSON.stringify({ email: email || null, expires_hours: expiresHours }),
  });
}
export async function getInvites() { return request<Invite[]>("/api/admin/invites"); }
export async function deleteInvite(inviteId: string) { return request(`/api/admin/invites/${inviteId}`, { method: "DELETE" }); }

// ─── Admin: corpus health ─────────────────────────────────────────────

export interface AdminDocumentRow {
  id: string;
  filename: string;
  project_id: string | null;
  project_name: string | null;
  doc_type: string | null;
  fiscal_year: string | null;
  status: string;
  file_size: number;
  page_count: number | null;
  is_ocrd: boolean;
  ocr_chars: number;
  is_vector_indexed: boolean;
  chunk_count: number;
  embedded_chunk_count: number;
  uploaded_by: string | null;
  uploaded_by_email: string | null;
  created_at: string;
}

export async function getAdminDocuments(params: {
  search?: string;
  has_ocr?: "yes" | "no";
  has_vector?: "yes" | "no";
  limit?: number;
  offset?: number;
} = {}): Promise<AdminDocumentRow[]> {
  const qs = new URLSearchParams();
  if (params.search) qs.set("search", params.search);
  if (params.has_ocr) qs.set("has_ocr", params.has_ocr);
  if (params.has_vector) qs.set("has_vector", params.has_vector);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request<AdminDocumentRow[]>(`/api/admin/documents${s ? `?${s}` : ""}`);
}

// ─── Admin: cost tracker ──────────────────────────────────────────────

export interface UsageBreakdownRow {
  cost: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  source?: string;
  model?: string;
}

export interface UsageUserRow {
  user_id: string | null;
  email: string;
  cost: number;
  calls: number;
}

export interface UsageDailyRow {
  date: string;
  cost: number;
  calls: number;
}

export interface UsageSummary {
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_calls: number;
  by_source: UsageBreakdownRow[];
  by_model: UsageBreakdownRow[];
  by_user: UsageUserRow[];
  daily: UsageDailyRow[];
}

export async function getUsageSummary(days: number = 30): Promise<UsageSummary> {
  return request<UsageSummary>(`/api/admin/usage/summary?days=${days}`);
}

export interface UsageRow {
  id: string;
  source: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  user_email: string | null;
  resource_type: string | null;
  resource_id: string | null;
  created_at: string;
}

export async function getUsageRows(params: {
  source?: string;
  model?: string;
  user_id?: string;
  days?: number;
  limit?: number;
  offset?: number;
} = {}): Promise<UsageRow[]> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return request<UsageRow[]>(`/api/admin/usage${s ? `?${s}` : ""}`);
}

// ─── Parcels ──────────────────────────────────────────────────────────
// NJ MOD-IV property records for Atlantic Highlands borough. Owner identity
// fields are omitted under NJ Daniel's Law (P.L. 2020 c.125) — all bulk
// MOD-IV feeds dropped OWNER_NAME on 2023-01-01. The list endpoint returns
// the trimmed ParcelListItem; getParcel(id) returns the full ParcelDetail.

export interface ParcelListItem {
  id: string;
  block: string;
  lot: string;
  qualifier: string;
  property_location: string | null;
  property_class: string | null;
  total_assessment: number | null;
  tax_amount: number | null;
  lot_size_acres: number | null;
  year_built: number | null;
  last_sale_price: number | null;
  last_sale_date: string | null;
}

export type ParcelSortColumn =
  | "block_lot"
  | "property_location"
  | "property_class"
  | "total_assessment"
  | "tax_amount"
  | "lot_size_acres"
  | "year_built"
  | "last_sale_price"
  | "last_sale_date";

export interface ParcelDetail extends ParcelListItem {
  pams_pin: string | null;
  county_code: string;
  muni_code: string;
  zoning: string | null;
  living_sqft: number | null;
  assessment_year: number | null;
  land_value: number | null;
  improvement_value: number | null;
  exemption_value: number | null;
  last_sale_book: string | null;
  last_sale_page: string | null;
  last_sale_nu_code: string | null;
  data_source: string | null;
}

export async function listParcels(params: {
  q?: string;
  block?: string;
  property_class?: string;
  min_assessment?: number;
  sort_by?: ParcelSortColumn;
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
} = {}): Promise<ParcelListItem[]> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return request<ParcelListItem[]>(`/api/parcels/${s ? `?${s}` : ""}`);
}

export async function countParcels(): Promise<{ count: number }> {
  return request<{ count: number }>("/api/parcels/count");
}

export async function getParcel(id: string): Promise<ParcelDetail> {
  return request<ParcelDetail>(`/api/parcels/${id}`);
}

// ─── Alerts ──────────────────────────────────────────────────────────
// Saved-keyword / new-meeting / new-document subscriptions. The daily
// digest worker (api/scripts/run_digest.py) emails matches via SES.

export type AlertKind = "keyword" | "new_meeting" | "new_document";
export type DigestFrequency = "daily" | "weekly";

export interface SavedAlert {
  id: string;
  kind: AlertKind;
  name: string;
  query: string | null;
  filters: Record<string, string | undefined>;
  frequency: DigestFrequency;
  enabled: boolean;
  last_run_at: string | null;
  last_sent_at: string | null;
  created_at: string;
}

export interface AlertCreate {
  kind: AlertKind;
  name: string;
  query?: string;
  filters?: Record<string, string | undefined>;
  frequency?: DigestFrequency;
  enabled?: boolean;
}

export interface AlertUpdate {
  name?: string;
  query?: string;
  filters?: Record<string, string | undefined>;
  frequency?: DigestFrequency;
  enabled?: boolean;
}

export async function listAlerts(): Promise<SavedAlert[]> {
  return request<SavedAlert[]>("/api/alerts/");
}

export async function createAlert(payload: AlertCreate): Promise<SavedAlert> {
  return request<SavedAlert>("/api/alerts/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAlert(id: string, payload: AlertUpdate): Promise<SavedAlert> {
  return request<SavedAlert>(`/api/alerts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// ─── Check-ins ────────────────────────────────────────────────────────
// "I'm at X right now" markers. Active window is 4 hours server-side.

export interface Checkin {
  id: string;
  user_id: string;
  user_display_name: string | null;
  user_picture_url: string | null;
  venue_name: string;
  city: string | null;
  message: string | null;
  checked_in_at: string;
}

export interface VenueSummary {
  venue_name: string;
  city: string | null;
  active_count: number;
  last_checked_in_at: string;
}

export async function listActiveCheckins(limit = 50): Promise<Checkin[]> {
  return request<Checkin[]>(`/api/checkins/?limit=${limit}`);
}

export async function listCheckinVenues(): Promise<VenueSummary[]> {
  return request<VenueSummary[]>("/api/checkins/venues");
}

export async function listCheckinsAtVenue(venueName: string): Promise<Checkin[]> {
  return request<Checkin[]>(`/api/checkins/by-venue/${encodeURIComponent(venueName)}`);
}

export async function createCheckin(payload: {
  venue_name: string;
  city?: string;
  message?: string;
}): Promise<Checkin> {
  return request<Checkin>("/api/checkins/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ─── Community Chat ───────────────────────────────────────────────────
// Single global feed for events.ahnj.info. Polling-based (~10s).

export type CommunityRefKind = "event" | "checkin";

export interface CommunityRefSnapshot {
  kind: CommunityRefKind;
  id: string;
  title: string;
  subtitle: string | null;
}

export interface CommunityMessage {
  id: string;
  user_id: string;
  user_display_name: string | null;
  user_picture_url: string | null;
  body: string;
  ref: CommunityRefSnapshot | null;
  created_at: string;
}

export async function listCommunityMessages(
  opts: { after?: string; limit?: number } = {},
): Promise<CommunityMessage[]> {
  const params = new URLSearchParams();
  if (opts.after) params.set("after", opts.after);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<CommunityMessage[]>(`/api/community/chat/messages${qs ? `?${qs}` : ""}`);
}

export async function postCommunityMessage(payload: {
  body: string;
  ref_type?: CommunityRefKind;
  ref_id?: string;
}): Promise<CommunityMessage> {
  return request<CommunityMessage>("/api/community/chat/messages", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteCommunityMessage(id: string): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/community/chat/messages/${id}`, {
    method: "DELETE", headers,
  });
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Delete failed: ${res.status}`);
  }
}

export async function deleteCheckin(id: string): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/checkins/${id}`, { method: "DELETE", headers });
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Delete failed: ${res.status}`);
  }
}

export async function deleteAlert(id: string): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/alerts/${id}`, { method: "DELETE", headers });
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Delete failed: ${res.status}`);
  }
}

// ─── Integrity (red flags) ───────────────────────────────────────────

export type FlagSeverity = "low" | "medium" | "high";
export type FlagStatus = "open" | "reviewing" | "dismissed" | "confirmed";

export interface FlagEvidence {
  document_id?: string;
  title?: string | null;
  url?: string | null;
  page?: number | null;
  date?: string;
  quote?: string | null;
  amount?: number | null;
  item?: string | null;
}

export interface IntegrityFlag {
  id: string;
  rule: string;
  severity: FlagSeverity;
  score: number;
  title: string;
  explanation: string | null;
  entity_key: string | null;
  subject: string | null;
  amount: number | null;
  evidence: FlagEvidence[];
  status: FlagStatus;
  reviewer_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  first_seen: string | null;
}

export interface IntegritySummary {
  open_by_severity: Partial<Record<FlagSeverity, number>>;
  by_rule: Record<string, number>;
  by_status: Partial<Record<FlagStatus, number>>;
  awards: number;
  award_total: number;
}

export interface IntegrityVendor {
  vendor_norm: string;
  vendor: string;
  awards: number;
  total: number;
  noncompetitive_total: number;
  noncompetitive_share: number | null;
  first: string | null;
  last: string | null;
  flags: number;
}

export interface IntegrityAward {
  id: string;
  document_id: string;
  meeting_date: string | null;
  item_no: string | null;
  page: number | null;
  title: string | null;
  vendor_name: string | null;
  amount: number | null;
  action_type: string;
  procurement: string;
  bids_received: number | null;
  account_code: string | null;
  quote: string | null;
}

export function getIntegritySummary(): Promise<IntegritySummary> {
  return request<IntegritySummary>("/api/integrity/summary");
}

export function getIntegrityFlags(params: { rule?: string; severity?: string; status?: string; subject?: string } = {}): Promise<IntegrityFlag[]> {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return request<IntegrityFlag[]>(`/api/integrity/flags${qs ? `?${qs}` : ""}`);
}

export function reviewIntegrityFlag(id: string, status: FlagStatus, reviewer_note?: string): Promise<IntegrityFlag> {
  return request<IntegrityFlag>(`/api/integrity/flags/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status, reviewer_note }),
  });
}

export function getIntegrityVendors(): Promise<IntegrityVendor[]> {
  return request<IntegrityVendor[]>("/api/integrity/vendors");
}

export function getIntegrityAwards(params: { vendor?: string; procurement?: string; action?: string } = {}): Promise<IntegrityAward[]> {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return request<IntegrityAward[]>(`/api/integrity/awards${qs ? `?${qs}` : ""}`);
}

// ─── AI keys & settings ───────────────────────────────────────────────

export interface AiProvider {
  id: string; label: string; key_hint: string; docs: string; kinds: string[]; models: string[];
  has_key: boolean; from_env?: boolean; last4?: string; status?: "untested" | "ok" | "invalid";
  status_detail?: string | null; verified_at?: string | null; added_at?: string | null;
}
export interface AiRoute { provider: string; model: string }
export interface MyAi {
  providers: AiProvider[];
  features: { feature: string; role: string; available: boolean; via?: string; reason?: string; sponsored: boolean }[];
  roles: Record<string, string>;
  prefs: Record<string, AiRoute>;
  spend_this_month: { own_keys: number; sponsored: number; sponsored_cap: number };
}
export interface AdminAi {
  providers: AiProvider[];
  routes: Record<string, AiRoute>;
  roles: Record<string, string>;
  features: { feature: string; role: string; interactive: boolean; sponsored: boolean }[];
  caps: { deployment_monthly_usd: number; user_monthly_usd: number };
  rates: Record<string, [number, number]>;
  spend_this_month: { deployment: number; by_user: { user: string; usd: number; calls: number }[];
                      by_feature: { source: string; usd: number; calls: number }[] };
}
export interface KeyTestResult { ok: boolean; detail: string; last4?: string; status?: string }

export const getMyAi = () => request<MyAi>("/api/ai/me");
export const putMyKey = (provider: string, key: string) =>
  request<KeyTestResult>(`/api/ai/keys/${provider}`, { method: "PUT", body: JSON.stringify({ key }) });
export const deleteMyKey = (provider: string) => request<{ deleted: boolean }>(`/api/ai/keys/${provider}`, { method: "DELETE" });
export const testMyKey = (provider: string) => request<KeyTestResult>(`/api/ai/keys/${provider}/test`, { method: "POST" });
export const putMyPrefs = (routes: Record<string, AiRoute | null>) =>
  request<{ routes: Record<string, AiRoute> }>("/api/ai/prefs", { method: "PUT", body: JSON.stringify({ routes }) });

export const getAdminAi = () => request<AdminAi>("/api/admin/ai");
export const putDeploymentKey = (provider: string, key: string) =>
  request<KeyTestResult>(`/api/admin/ai/keys/${provider}`, { method: "PUT", body: JSON.stringify({ key }) });
export const deleteDeploymentKey = (provider: string) =>
  request<{ deleted: boolean }>(`/api/admin/ai/keys/${provider}`, { method: "DELETE" });
export const testDeploymentKey = (provider: string) =>
  request<KeyTestResult>(`/api/admin/ai/keys/${provider}/test`, { method: "POST" });
export const putAdminAiSettings = (body: Partial<{ routes: Record<string, AiRoute>; sponsored: string[];
  caps: Record<string, number>; rates: Record<string, [number, number]> }>) =>
  request<AdminAi>("/api/admin/ai/settings", { method: "PUT", body: JSON.stringify(body) });

export interface DiscoveryResult {
  pages_fetched: number;
  platforms: Record<string, { evidence: string[]; [k: string]: unknown }>;
  sources: { id: string; label?: string; description?: string; adapter: string; evidence?: string[] }[];
  notes: string[]; blocked: string[]; yaml: string; jurisdiction: string;
}
export const discoverSources = (url: string) =>
  request<DiscoveryResult>("/api/admin/ai/discover", { method: "POST", body: JSON.stringify({ url }) });
