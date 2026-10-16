"use client";

import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getDrillResults, runDrill, type DrillResponse, type FinancialStatement } from "@/lib/api";
import AnomalyBadge from "./AnomalyBadge";
import { ArrowPathIcon, BoltIcon } from "@heroicons/react/24/outline";

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : `$${Math.round(n).toLocaleString()}`;

const pct = (n: number | null | undefined, digits = 1) =>
  n == null ? "—" : `${(n * 100).toFixed(digits)}%`;

interface Props {
  statement: FinancialStatement;
}

type Tab = "synthesis" | "revenue" | "expenditure" | "debt" | "fund_balance" | "anomalies";

export default function DrillPanel({ statement }: Props) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("synthesis");

  const { data, refetch, isLoading } = useQuery({
    queryKey: ["drill", statement.id],
    queryFn: () => getDrillResults(statement.id),
    refetchInterval: (q) => {
      const d = q.state.data as DrillResponse | undefined;
      return d?.status === "drilled" || d?.status === "verified" ? false : 5000;
    },
  });

  const drillMut = useMutation({
    mutationFn: ({ sync }: { sync: boolean }) => runDrill(statement.id, sync),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drill", statement.id] }); },
  });

  const drills: any = data?.drill_results ?? {};
  const anomalies = data?.anomaly_flags ?? [];
  // A drill is REAL only if it produced output (no `error` key). Otherwise we treat it as "not run / failed".
  const synthOk = !!drills.synthesis && !drills.synthesis.error;
  const isDrilled = synthOk;
  const meta = drills._meta ?? null;
  const drillErrors: { drill: string; error: string; msg: string }[] = [];
  for (const k of ["revenue", "expenditure", "debt", "fund_balance", "synthesis"]) {
    const d = drills[k];
    if (d && typeof d === "object" && "error" in d) {
      drillErrors.push({
        drill: k,
        error: String(d.error ?? "unknown"),
        msg: String(d.error_message ?? "")?.slice(0, 200),
      });
    }
  }

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "synthesis", label: "Synthesis" },
    { key: "revenue", label: "Revenue" },
    { key: "expenditure", label: "Expenditure" },
    { key: "debt", label: "Debt" },
    { key: "fund_balance", label: "Fund Balance" },
    { key: "anomalies", label: "Anomalies", count: anomalies.length },
  ];

  return (
    <div className="bg-white rounded-xl shadow border border-gray-200">
      {/* Header */}
      <div className="border-b px-5 py-4 flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-gray-900">{statement.entity_name}</h2>
          <p className="text-xs text-gray-500">
            FY {statement.fiscal_year} · {statement.statement_type} ·
            {data?.accounting_basis === "gaap" ? " GAAP/GASB" : " NJ Regulatory"} ·
            Reconcile: <span className="font-medium">{data?.reconcile_status ?? "—"}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => drillMut.mutate({ sync: false })}
            disabled={drillMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
            title="Background — refresh poll every 5s"
          >
            {drillMut.isPending && drillMut.variables?.sync === false ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <BoltIcon className="w-3.5 h-3.5" />}
            {isDrilled ? "Re-drill" : "Run Drill"}
          </button>
          <button
            onClick={() => drillMut.mutate({ sync: true })}
            disabled={drillMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-primary-300 text-primary-700 text-xs font-medium hover:bg-primary-50 disabled:opacity-50"
            title="Sync mode — blocks for ~30-90s and surfaces errors directly"
          >
            {drillMut.isPending && drillMut.variables?.sync === true ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : "Sync"}
          </button>
        </div>
      </div>

      {/* Meta strip */}
      {(meta || drillErrors.length > 0) && (
        <div className="px-5 py-2 border-b bg-gray-50 flex items-center gap-4 text-xs">
          {meta && (
            <>
              <span className="text-gray-600">
                <span className="font-medium text-green-700">{meta.success_count ?? 0}/4</span> drills OK
                {meta.error_count > 0 && <span className="text-red-700 ml-1">· {meta.error_count} errored</span>}
              </span>
              <span className="text-gray-500">{meta.duration_s ? `${meta.duration_s}s` : ""}</span>
              <span className="text-gray-400 truncate">
                {meta.synthesis_ok ? "synthesis ok" : "synthesis failed"} · models: {(meta.llm_models_attempted || []).join(", ")}
              </span>
            </>
          )}
          {!meta && drillErrors.length > 0 && (
            <span className="text-red-700">{drillErrors.length} drill(s) failed — see error tab</span>
          )}
        </div>
      )}

      {drillErrors.length > 0 && (
        <div className="px-5 py-2 bg-red-50 border-b border-red-200 text-xs text-red-900">
          <strong>Drill errors:</strong>{" "}
          {drillErrors.map((d, i) => (
            <span key={i} className="mr-3">
              <span className="font-medium">{d.drill}:</span> {d.error}
              {d.msg && <span className="text-red-700"> — {d.msg}</span>}
            </span>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b px-3 md:px-5 flex gap-1 overflow-x-auto">
        {tabs.map(({ key, label, count }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-3 py-2.5 text-xs font-medium border-b-2 transition-colors whitespace-nowrap
              ${tab === key
                ? "border-primary-600 text-primary-700"
                : "border-transparent text-gray-500 hover:text-gray-800"}`}
          >
            {label}{count != null && count > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-700 text-[10px]">{count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab body */}
      <div className="p-5 min-h-[300px]">
        {isLoading && <p className="text-sm text-gray-500">Loading…</p>}

        {!isDrilled && !isLoading && tab !== "anomalies" && (
          <div className="text-center py-12">
            <p className="text-sm text-gray-500">Drill agents have not run yet for this statement.</p>
            <button
              onClick={() => drillMut.mutate({ sync: false })}
              disabled={drillMut.isPending}
              className="mt-3 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
            >
              <BoltIcon className="w-4 h-4" /> Run Drill Agents
            </button>
            <p className="text-xs text-gray-400 mt-3">
              4 parallel agents (Revenue · Expenditure · Debt · Fund Balance) + synthesis. Takes ~30s.
            </p>
          </div>
        )}

        {tab === "synthesis" && drills.synthesis && !drills.synthesis.error && (
          <SynthesisView synthesis={drills.synthesis} />
        )}
        {tab === "synthesis" && drills.synthesis?.error && (
          <DrillErrorView label="synthesis" data={drills.synthesis} />
        )}

        {tab === "revenue" && drills.revenue && !drills.revenue.error && (
          <RevenueView revenue={drills.revenue} />
        )}
        {tab === "revenue" && drills.revenue?.error && (
          <DrillErrorView label="revenue" data={drills.revenue} />
        )}

        {tab === "expenditure" && drills.expenditure && !drills.expenditure.error && (
          <ExpenditureView expenditure={drills.expenditure} />
        )}
        {tab === "expenditure" && drills.expenditure?.error && (
          <DrillErrorView label="expenditure" data={drills.expenditure} />
        )}

        {tab === "debt" && drills.debt && !drills.debt.error && (
          <DebtView debt={drills.debt} />
        )}
        {tab === "debt" && drills.debt?.error && (
          <DrillErrorView label="debt" data={drills.debt} />
        )}

        {tab === "fund_balance" && drills.fund_balance && !drills.fund_balance.error && (
          <FundBalanceView fundBalance={drills.fund_balance} accountingBasis={data?.accounting_basis} />
        )}
        {tab === "fund_balance" && drills.fund_balance?.error && (
          <DrillErrorView label="fund_balance" data={drills.fund_balance} />
        )}

        {tab === "anomalies" && (
          <AnomaliesView anomalies={anomalies} reconcileDetails={data?.reconcile_details ?? {}} />
        )}
      </div>
    </div>
  );
}

// ─── Sub-views ─────────────────────────────────────────────────────────────

function SynthesisView({ synthesis }: { synthesis: any }) {
  return (
    <div className="space-y-5">
      {synthesis.headline && (
        <h3 className="text-lg font-semibold text-gray-900">{synthesis.headline}</h3>
      )}
      {synthesis.executive_summary && (
        <p className="text-sm text-gray-700 leading-relaxed">{synthesis.executive_summary}</p>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {synthesis.strengths?.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-green-700 mb-2">Strengths</h4>
            <ul className="space-y-1.5 text-sm text-gray-700">
              {synthesis.strengths.map((s: string, i: number) => <li key={i}>· {s}</li>)}
            </ul>
          </div>
        )}
        {synthesis.concerns?.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-amber-700 mb-2">Concerns</h4>
            <ul className="space-y-1.5 text-sm text-gray-700">
              {synthesis.concerns.map((c: string, i: number) => <li key={i}>· {c}</li>)}
            </ul>
          </div>
        )}
      </div>

      {synthesis.red_flags?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-red-700 mb-2">Red Flags</h4>
          <div className="space-y-2">
            {synthesis.red_flags.map((f: any, i: number) => (
              <AnomalyBadge key={i} flag={{ code: f.flag, severity: f.severity, message: f.evidence }} />
            ))}
          </div>
        </div>
      )}

      {synthesis.questions_to_ask?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-blue-700 mb-2">Questions to Ask</h4>
          <ul className="space-y-1.5 text-sm text-gray-700 list-disc pl-5">
            {synthesis.questions_to_ask.map((q: string, i: number) => <li key={i}>{q}</li>)}
          </ul>
        </div>
      )}

      {synthesis.opra_followups?.length > 0 && (
        <div className="rounded-lg bg-gray-50 border p-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">OPRA Followups</h4>
          <ul className="space-y-1.5 text-sm text-gray-700 list-disc pl-5">
            {synthesis.opra_followups.map((q: string, i: number) => <li key={i}>{q}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function CompositionTable({ rows, valueKey }: { rows: any[]; valueKey: string }) {
  if (!rows?.length) return <p className="text-sm text-gray-400">No data</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-xs text-gray-500 uppercase tracking-wide">
        <tr>
          <th className="text-left py-2">Category</th>
          <th className="text-right py-2">Amount</th>
          <th className="text-right py-2">% of Total</th>
          <th className="text-right py-2">YoY Δ</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        {rows.map((r: any, i: number) => (
          <tr key={i}>
            <td className="py-2 text-gray-800">{r[valueKey] ?? r.category ?? r.function ?? r.object}</td>
            <td className="py-2 text-right font-mono text-gray-900">{fmt(r.amount)}</td>
            <td className="py-2 text-right text-gray-600">{r.pct_of_total != null ? `${Number(r.pct_of_total).toFixed(1)}%` : "—"}</td>
            <td className={`py-2 text-right text-xs ${r.yoy_change_pct == null ? "text-gray-400" : Number(r.yoy_change_pct) > 0 ? "text-green-600" : "text-red-600"}`}>
              {r.yoy_change_pct == null ? "—" : `${Number(r.yoy_change_pct) > 0 ? "+" : ""}${Number(r.yoy_change_pct).toFixed(1)}%`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FindingsList({ findings }: { findings: any[] }) {
  if (!findings?.length) return null;
  return (
    <div className="mt-4 space-y-2">
      {findings.map((f: any, i: number) => (
        <AnomalyBadge key={i} flag={{ code: "finding", severity: f.concern_level || "info", message: `${f.finding}${f.evidence ? ` — ${f.evidence}` : ""}` }} />
      ))}
    </div>
  );
}

function RevenueView({ revenue }: { revenue: any }) {
  return (
    <div className="space-y-4">
      {revenue.composition && <CompositionTable rows={revenue.composition} valueKey="category" />}
      {revenue.tax_levy_cap_analysis && (
        <p className="text-sm text-gray-700 bg-blue-50 border border-blue-200 rounded p-3">
          <strong>2% Tax Levy Cap (NJ):</strong> {revenue.tax_levy_cap_analysis}
        </p>
      )}
      {revenue.etr_cmptra_status && (
        <p className="text-xs text-gray-600 italic">{revenue.etr_cmptra_status}</p>
      )}
      {revenue.trends && <p className="text-sm text-gray-700">{revenue.trends}</p>}
      <FindingsList findings={revenue.key_findings || []} />
    </div>
  );
}

function ExpenditureView({ expenditure }: { expenditure: any }) {
  return (
    <div className="space-y-4">
      {expenditure.by_function && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">By Function</h4>
          <CompositionTable rows={expenditure.by_function} valueKey="function" />
        </div>
      )}
      {expenditure.by_object && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">By Object</h4>
          <CompositionTable rows={expenditure.by_object} valueKey="object" />
        </div>
      )}
      {(expenditure.salary_to_total_ratio != null || expenditure.benefits_to_salary_ratio != null) && (
        <div className="grid grid-cols-2 gap-3">
          {expenditure.salary_to_total_ratio != null && (
            <div className="rounded-lg bg-gray-50 p-3 border">
              <p className="text-[10px] uppercase font-semibold text-gray-500">Salaries / Total</p>
              <p className="text-lg font-mono">{pct(expenditure.salary_to_total_ratio)}</p>
            </div>
          )}
          {expenditure.benefits_to_salary_ratio != null && (
            <div className="rounded-lg bg-gray-50 p-3 border">
              <p className="text-[10px] uppercase font-semibold text-gray-500">Benefits / Salaries</p>
              <p className="text-lg font-mono">{pct(expenditure.benefits_to_salary_ratio)}</p>
            </div>
          )}
        </div>
      )}
      {expenditure.trends && <p className="text-sm text-gray-700">{expenditure.trends}</p>}
      <FindingsList findings={expenditure.key_findings || []} />
    </div>
  );
}

function DebtView({ debt }: { debt: any }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ["Outstanding Debt", fmt(debt.outstanding_debt)],
          ["Annual Debt Service", fmt(debt.annual_debt_service)],
          ["Debt / Revenue", debt.debt_to_revenue_ratio != null ? `${(Number(debt.debt_to_revenue_ratio) * 100).toFixed(0)}%` : "—"],
          ["Debt / Eq Valuation", debt.debt_to_eq_valuation_pct != null ? `${Number(debt.debt_to_eq_valuation_pct).toFixed(2)}%` : "—"],
        ].map(([k, v], i) => (
          <div key={i} className="rounded-lg bg-gray-50 p-3 border">
            <p className="text-[10px] uppercase font-semibold text-gray-500">{k}</p>
            <p className="text-lg font-mono">{v}</p>
          </div>
        ))}
      </div>
      {debt.statutory_cap_pct && (
        <p className="text-xs text-gray-600 italic">
          NJ statutory cap: {debt.statutory_cap_pct}% of equalized valuation. Within cap: {String(debt.within_statutory_cap)}.
        </p>
      )}
      {debt.debt_components?.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 uppercase tracking-wide">
            <tr><th className="text-left py-2">Component</th><th className="text-right py-2">Amount</th><th className="text-right py-2">Maturity</th></tr>
          </thead>
          <tbody className="divide-y">
            {debt.debt_components.map((c: any, i: number) => (
              <tr key={i}>
                <td className="py-2">{c.name}</td>
                <td className="py-2 text-right font-mono">{fmt(c.amount)}</td>
                <td className="py-2 text-right text-gray-500">{c.maturity ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {debt.trends && <p className="text-sm text-gray-700">{debt.trends}</p>}
      <FindingsList findings={debt.key_findings || []} />
    </div>
  );
}

function FundBalanceView({ fundBalance, accountingBasis }: { fundBalance: any; accountingBasis: string | null | undefined }) {
  const isSchool = accountingBasis === "gaap";
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg bg-gray-50 p-3 border">
          <p className="text-[10px] uppercase font-semibold text-gray-500">Total FB</p>
          <p className="text-lg font-mono">{fmt(fundBalance.total_fund_balance ?? fundBalance.total_current_fund_balance)}</p>
        </div>
        {isSchool && fundBalance.statutory_cap != null && (
          <div className="rounded-lg bg-amber-50 p-3 border border-amber-200">
            <p className="text-[10px] uppercase font-semibold text-amber-700">NJ 2%/$250K Cap</p>
            <p className="text-lg font-mono">{fmt(fundBalance.statutory_cap)}</p>
            <p className="text-[10px] text-amber-700 mt-0.5">{fundBalance.cap_status}</p>
          </div>
        )}
        {!isSchool && fundBalance.fund_balance_to_expenditure_ratio != null && (
          <div className="rounded-lg bg-gray-50 p-3 border">
            <p className="text-[10px] uppercase font-semibold text-gray-500">FB / Exp</p>
            <p className="text-lg font-mono">{pct(fundBalance.fund_balance_to_expenditure_ratio)}</p>
            <p className="text-[10px] text-gray-500">target ≥8%, warn &lt;5%</p>
          </div>
        )}
        <div className="rounded-lg bg-gray-50 p-3 border">
          <p className="text-[10px] uppercase font-semibold text-gray-500">Trajectory</p>
          <p className="text-lg capitalize">{fundBalance.trajectory ?? "—"}</p>
        </div>
      </div>

      {isSchool && fundBalance.gasb54_components && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">GASB 54 Components</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2 text-xs">
            {(["nonspendable", "restricted", "committed", "assigned", "unassigned"] as const).map((k) => (
              <div key={k} className="rounded bg-gray-50 p-2 border">
                <p className="text-[10px] uppercase text-gray-500">{k}</p>
                <p className="font-mono">{fmt(fundBalance.gasb54_components[k])}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {isSchool && fundBalance.reserves && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">NJ School Reserves</h4>
          <div className="grid grid-cols-3 md:grid-cols-5 gap-2 text-xs">
            {Object.entries(fundBalance.reserves as Record<string, number>).map(([k, v]) => (
              <div key={k} className="rounded bg-purple-50 p-2 border border-purple-200">
                <p className="text-[10px] uppercase text-purple-700">{k.replace(/_/g, " ")}</p>
                <p className="font-mono text-purple-900">{fmt(v)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {fundBalance.cap_analysis && (
        <p className="text-sm text-gray-700 bg-amber-50 border border-amber-200 rounded p-3">
          <strong>Cap analysis:</strong> {fundBalance.cap_analysis}
        </p>
      )}
      {fundBalance.narrative && <p className="text-sm text-gray-700">{fundBalance.narrative}</p>}
      <FindingsList findings={fundBalance.key_findings || []} />
    </div>
  );
}

function DrillErrorView({ label, data }: { label: string; data: any }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg bg-red-50 border border-red-300 p-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-red-700 mb-1">
          {label} drill failed
        </div>
        <div className="text-sm text-red-900 font-mono">{data.error}</div>
        {data.error_message && (
          <div className="text-xs text-red-800 mt-2 whitespace-pre-wrap">{data.error_message}</div>
        )}
        {data.duration_s != null && (
          <div className="text-[11px] text-red-700 mt-2">duration: {data.duration_s}s</div>
        )}
      </div>
      {data.error_trace && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-600 hover:text-gray-800">Stack trace</summary>
          <pre className="mt-2 p-3 bg-gray-900 text-gray-100 rounded overflow-x-auto text-[11px]">{data.error_trace}</pre>
        </details>
      )}
      <p className="text-xs text-gray-500">
        Try the <strong>Sync</strong> button at the top — it runs the drill inline and surfaces any LLM-side error directly.
        Common causes: missing/invalid <code>ANTHROPIC_API_KEY</code> or <code>GEMINI_API_KEY</code>, rate limit, or LLM returning non-JSON output.
      </p>
    </div>
  );
}

function AnomaliesView({ anomalies, reconcileDetails }: { anomalies: any[]; reconcileDetails: any }) {
  const high = anomalies.filter(a => a.severity === "high");
  const warn = anomalies.filter(a => a.severity === "warn");
  const info = anomalies.filter(a => a.severity === "info");

  return (
    <div className="space-y-4">
      {anomalies.length === 0 && (
        <p className="text-sm text-gray-500">No anomalies detected.</p>
      )}

      {high.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-red-700 mb-2">High Severity ({high.length})</h4>
          <div className="space-y-2">{high.map((f, i) => <AnomalyBadge key={i} flag={f} />)}</div>
        </div>
      )}
      {warn.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-amber-700 mb-2">Warnings ({warn.length})</h4>
          <div className="space-y-2">{warn.map((f, i) => <AnomalyBadge key={i} flag={f} />)}</div>
        </div>
      )}
      {info.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-blue-700 mb-2">Info ({info.length})</h4>
          <div className="space-y-2">{info.map((f, i) => <AnomalyBadge key={i} flag={f} />)}</div>
        </div>
      )}

      {reconcileDetails?.checks?.length > 0 && (
        <div className="mt-6 pt-4 border-t">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">Reconcile Details</h4>
          <table className="w-full text-xs">
            <thead className="text-gray-500">
              <tr><th className="text-left py-1">Section</th><th className="text-right">Extracted</th><th className="text-right">Reported</th><th className="text-right">Δ</th><th className="text-right">Δ%</th><th className="text-right">Status</th></tr>
            </thead>
            <tbody className="divide-y">
              {reconcileDetails.checks.map((c: any, i: number) => (
                <tr key={i}>
                  <td className="py-1">{c.section}</td>
                  <td className="py-1 text-right font-mono">{fmt(c.extracted)}</td>
                  <td className="py-1 text-right font-mono">{fmt(c.reported)}</td>
                  <td className="py-1 text-right font-mono">{c.delta != null ? fmt(c.delta) : "—"}</td>
                  <td className="py-1 text-right">{c.delta_pct != null ? `${c.delta_pct}%` : "—"}</td>
                  <td className="py-1 text-right text-[10px]">{c.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
