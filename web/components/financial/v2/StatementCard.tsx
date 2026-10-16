"use client";

import { type FinancialStatement, type AnomalyFlag, getDocumentViewUrl } from "@/lib/api";
import { CheckCircleIcon, ExclamationTriangleIcon, BoltIcon, DocumentTextIcon } from "@heroicons/react/24/outline";

interface Props {
  stmt: FinancialStatement;
  anomalyFlags?: AnomalyFlag[];
  reconcileStatus?: string;
  selected?: boolean;
  onClick?: () => void;
  onDrill?: () => void;
}

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : `$${Math.round(n).toLocaleString()}`;

const RECONCILE_STYLES: Record<string, string> = {
  balanced: "text-green-600",
  off_lt_1pct: "text-yellow-600",
  off_gt_1pct: "text-orange-600",
  unbalanced: "text-red-600",
  not_attempted: "text-gray-400",
};

// Identify whether a statement_type/entity_name combination is "Advertised" vs
// "Adopted" / final, so multiple budgets for the same FY don't look like duplicates.
function budgetVariant(stmt: FinancialStatement): string | null {
  const name = (stmt.entity_name || "").toLowerCase();
  if (stmt.statement_type !== "budget") return null;
  if (name.includes("advertised") || name.includes("tentative")) return "Advertised";
  if (name.includes("adopted") || name.includes("final")) return "Adopted";
  if (name.includes("monmouth")) return "DLGS / Monmouth Filing";
  return null;
}

async function openDocument(documentId: string) {
  try {
    const { url } = await getDocumentViewUrl(documentId);
    window.open(url, "_blank", "noopener,noreferrer");
  } catch (e) {
    alert(`Could not open document: ${(e as Error).message}`);
  }
}

export default function StatementCard({ stmt, anomalyFlags, reconcileStatus, selected, onClick, onDrill }: Props) {
  const highCount = (anomalyFlags ?? []).filter(f => f.severity === "high").length;
  const warnCount = (anomalyFlags ?? []).filter(f => f.severity === "warn").length;
  const isSchool = stmt.entity_type === "school";
  const variant = budgetVariant(stmt);

  return (
    <div
      onClick={onClick}
      className={`rounded-xl border-2 p-4 cursor-pointer transition-all
        ${selected ? "border-primary-500 bg-primary-50/30" : "border-gray-200 bg-white hover:border-gray-300"}`}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded
              ${isSchool ? "bg-orange-100 text-orange-700" : "bg-blue-100 text-blue-700"}`}>
              {isSchool ? "School (GAAP)" : "Town (NJ Reg)"}
            </span>
            <span className="text-xs text-gray-500 capitalize">{stmt.statement_type}</span>
            {variant && (
              <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                {variant}
              </span>
            )}
          </div>
          <h3 className="font-semibold text-gray-900 text-sm leading-tight">{stmt.entity_name}</h3>
          <p className="text-xs text-gray-500">FY {stmt.fiscal_year}</p>
        </div>
        {reconcileStatus && (
          <span className={`text-[10px] font-medium ${RECONCILE_STYLES[reconcileStatus] ?? "text-gray-400"}`} title={`Reconcile: ${reconcileStatus}`}>
            {reconcileStatus === "balanced" ? <CheckCircleIcon className="w-4 h-4" /> :
             reconcileStatus === "not_attempted" ? null :
             <ExclamationTriangleIcon className="w-4 h-4" />}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <dt className="text-gray-500">Revenue</dt>
        <dd className="text-right font-medium text-gray-900">{fmt(stmt.total_revenue)}</dd>
        <dt className="text-gray-500">Expenditures</dt>
        <dd className="text-right font-medium text-gray-900">{fmt(stmt.total_expenditures)}</dd>
        <dt className="text-gray-500">Surplus</dt>
        <dd className={`text-right font-medium ${(stmt.surplus_deficit ?? 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
          {fmt(stmt.surplus_deficit)}
        </dd>
        <dt className="text-gray-500">Fund Balance</dt>
        <dd className="text-right font-medium text-gray-900">{fmt(stmt.fund_balance)}</dd>
      </dl>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex gap-1.5 text-[10px]">
          {highCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">{highCount} high</span>
          )}
          {warnCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">{warnCount} warn</span>
          )}
          <span className={`px-1.5 py-0.5 rounded-full font-medium ${
            stmt.status === "drilled" ? "bg-purple-100 text-purple-700" :
            stmt.status === "extracted" ? "bg-green-100 text-green-700" :
            stmt.status === "processing" ? "bg-yellow-100 text-yellow-700" :
            "bg-gray-100 text-gray-500"}`}>
            {stmt.status}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {stmt.document_id && (
            <button
              onClick={(e) => { e.stopPropagation(); openDocument(stmt.document_id); }}
              className="flex items-center gap-1 px-2 py-1 rounded-md border border-gray-300 text-gray-700 text-[10px] font-medium hover:bg-gray-50"
              title="View source PDF"
            >
              <DocumentTextIcon className="w-3 h-3" /> View
            </button>
          )}
          {onDrill && stmt.status === "extracted" && (
            <button
              onClick={(e) => { e.stopPropagation(); onDrill(); }}
              className="flex items-center gap-1 px-2 py-1 rounded-md bg-primary-600 text-white text-[10px] font-medium hover:bg-primary-700"
            >
              <BoltIcon className="w-3 h-3" /> Drill
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
