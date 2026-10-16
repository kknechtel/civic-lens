"use client";

import { useQuery } from "@tanstack/react-query";
import { getFYView, type FYView } from "@/lib/api";
import {
  CheckCircleIcon, XCircleIcon, ExclamationTriangleIcon,
  DocumentMagnifyingGlassIcon,
} from "@heroicons/react/24/outline";

interface Props {
  entityType: "town" | "school";
  fiscalYear: string;
  onClose?: () => void;
}

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : `$${Math.round(n).toLocaleString()}`;

const VARIANT_COLORS: Record<string, string> = {
  adopted: "bg-green-100 text-green-800",
  advertised: "bg-yellow-100 text-yellow-800",
  dlgs_filing: "bg-blue-100 text-blue-800",
  presentation: "bg-purple-100 text-purple-800",
  primary: "bg-gray-100 text-gray-700",
};

const RECONCILE_COLORS: Record<string, string> = {
  balanced: "text-green-700",
  off_lt_1pct: "text-yellow-700",
  off_gt_1pct: "text-orange-700",
  unbalanced: "text-red-700",
  not_attempted: "text-gray-400",
};

export default function CombinedFYView({ entityType, fiscalYear, onClose }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["fy-view", entityType, fiscalYear],
    queryFn: () => getFYView(entityType, fiscalYear),
  });

  if (isLoading) return <p className="text-sm text-gray-500 p-6">Loading combined view…</p>;
  if (error) return <p className="text-sm text-red-700 p-6">Error: {(error as Error).message}</p>;
  if (!data) return null;

  const m = data.merged;

  return (
    <div className="bg-white rounded-xl border border-gray-200">
      <div className="px-3 md:px-5 py-3 md:py-4 border-b flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="font-semibold text-gray-900 flex items-center gap-2 text-sm md:text-base">
            <DocumentMagnifyingGlassIcon className="w-5 h-5 text-primary-600 flex-shrink-0" />
            <span className="truncate">Combined FY {data.fiscal_year} View — {data.entity_type === "school" ? "School" : "Town"}</span>
          </h2>
          <p className="text-[11px] md:text-xs text-gray-500 mt-1">
            Merged from {data.sources.length} source statement(s) ·
            {data.accounting_basis === "gaap" ? " GAAP/GASB" : " NJ Regulatory"} ·
            {data.fiscal_calendar?.replace("_", " ")}
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-base text-gray-500 hover:text-gray-800 flex-shrink-0 min-w-[32px] min-h-[32px]" aria-label="Close combined view">×</button>
        )}
      </div>

      <div className="p-3 md:p-5 space-y-5">
        {/* Merged totals */}
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Merged Totals (best source per metric)</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MergedCard label="Revenue" value={m.total_revenue} sourceId={m.total_revenue_source} sources={data.sources} />
            <MergedCard label="Expenditures" value={m.total_expenditures} sourceId={m.total_expenditures_source} sources={data.sources} />
            <MergedCard label="Fund Balance" value={m.fund_balance} sourceId={m.fund_balance_source} sources={data.sources} />
            <MergedCard label="Total Debt" value={m.total_debt} sourceId={m.total_debt_source} sources={data.sources} />
          </div>
        </section>

        {/* Source provenance */}
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Source statements ({data.sources.length})
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 pr-3">Variant</th>
                  <th className="text-left py-2 pr-3">Type</th>
                  <th className="text-left py-2 pr-3">Entity name</th>
                  <th className="text-right py-2 pr-3">Lines</th>
                  <th className="text-center py-2 pr-3">Reconcile</th>
                  <th className="text-center py-2 pr-1">Rev</th>
                  <th className="text-center py-2 pr-1">Exp</th>
                  <th className="text-center py-2 pr-1">FB</th>
                  <th className="text-center py-2">Debt</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.sources.map((s) => (
                  <tr key={s.statement_id} className="hover:bg-gray-50">
                    <td className="py-2 pr-3">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold ${VARIANT_COLORS[s.variant] ?? "bg-gray-100 text-gray-700"}`}>
                        {s.variant.replace("_", " ")}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-gray-700 capitalize">{s.statement_type}</td>
                    <td className="py-2 pr-3 text-gray-500 truncate max-w-[120px] md:max-w-[180px]" title={s.entity_name ?? ""}>
                      {s.entity_name ?? "—"}
                    </td>
                    <td className="py-2 pr-3 text-right text-gray-700">{s.line_item_count}</td>
                    <td className={`py-2 pr-3 text-center text-[11px] ${RECONCILE_COLORS[s.reconcile_status ?? "not_attempted"] ?? "text-gray-400"}`}>
                      {s.reconcile_status ?? "—"}
                    </td>
                    <td className="py-2 pr-1 text-center"><Tick on={s.has_revenue} /></td>
                    <td className="py-2 pr-1 text-center"><Tick on={s.has_expenditures} /></td>
                    <td className="py-2 pr-1 text-center"><Tick on={s.has_fund_balance} /></td>
                    <td className="py-2 text-center"><Tick on={s.has_debt} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* What's missing */}
        {(data.missing.doc_types.length > 0 || data.missing.fields.length > 0) && (
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-700 mb-2 flex items-center gap-1">
              <ExclamationTriangleIcon className="w-4 h-4" />
              What's missing
            </h3>
            <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 space-y-1.5 text-sm">
              {data.missing.doc_types.length > 0 && (
                <div>
                  <span className="text-xs font-semibold text-amber-800 uppercase">Doc types not yet ingested:</span>
                  <ul className="ml-4 list-disc text-amber-900">
                    {data.missing.doc_types.map(t => <li key={t}>{t.replace("_", " ")}</li>)}
                  </ul>
                  {data.missing.doc_types.includes("audit") && (
                    <p className="text-[11px] text-amber-700 mt-1 italic">
                      School audits/ACFRs file with NJDOE by Dec 5 following FY-end (June 30). For an in-progress
                      FY this is normal — the doc doesn't exist yet.
                    </p>
                  )}
                </div>
              )}
              {data.missing.fields.length > 0 && (
                <div>
                  <span className="text-xs font-semibold text-amber-800 uppercase">Fields no source provided:</span>
                  <ul className="ml-4 list-disc text-amber-900">
                    {data.missing.fields.map(f => <li key={f}>{f.replace(/_/g, " ")}</li>)}
                  </ul>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Merged line items preview */}
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Merged line items ({data.merged_line_item_count} deduped, top 25 by amount)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 border-b">
                <tr>
                  <th className="text-left py-1.5 pr-3">Line</th>
                  <th className="text-left py-1.5 pr-3">Section</th>
                  <th className="text-right py-1.5 pr-3">Amount</th>
                  <th className="text-right py-1.5 pr-3">Prior Year</th>
                  <th className="text-right py-1.5 pr-3">YoY %</th>
                  <th className="text-left py-1.5">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {[...data.merged_line_items]
                  .filter(l => !l.is_total_row && l.amount != null)
                  .sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))
                  .slice(0, 25)
                  .map((l, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="py-1.5 pr-3 text-gray-800 truncate max-w-[160px] md:max-w-[280px]" title={l.line_name}>
                        {l.line_name}
                      </td>
                      <td className="py-1.5 pr-3 text-gray-500">{l.section}</td>
                      <td className="py-1.5 pr-3 text-right font-mono">{fmt(l.amount)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono text-gray-500">{fmt(l.prior_year_amount)}</td>
                      <td className={`py-1.5 pr-3 text-right text-[11px] ${l.yoy_change_pct == null ? "text-gray-400" : l.yoy_change_pct > 0 ? "text-green-700" : "text-red-700"}`}>
                        {l.yoy_change_pct != null ? `${l.yoy_change_pct > 0 ? "+" : ""}${l.yoy_change_pct.toFixed(1)}%` : "—"}
                      </td>
                      <td className="py-1.5 text-[10px] text-gray-400">{l.from_doc_variant}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function Tick({ on }: { on: boolean }) {
  return on
    ? <CheckCircleIcon className="w-4 h-4 text-green-600 inline" />
    : <XCircleIcon className="w-4 h-4 text-gray-300 inline" />;
}

function MergedCard({ label, value, sourceId, sources }: {
  label: string; value: number | null; sourceId: string | null; sources: FYView["sources"];
}) {
  const src = sources.find(s => s.statement_id === sourceId);
  return (
    <div className="rounded-lg border bg-gray-50 p-3">
      <p className="text-[10px] uppercase font-semibold text-gray-500">{label}</p>
      <p className="text-lg font-mono mt-0.5">{fmt(value)}</p>
      {src ? (
        <p className="text-[10px] text-gray-500 mt-0.5 truncate" title={`From ${src.variant} ${src.statement_type}`}>
          from {src.variant} {src.statement_type}
        </p>
      ) : (
        <p className="text-[10px] text-amber-700 mt-0.5">no source</p>
      )}
    </div>
  );
}
