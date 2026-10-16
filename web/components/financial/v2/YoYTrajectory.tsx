"use client";

import { useMemo } from "react";
import { type FinancialStatement } from "@/lib/api";

interface Props {
  statements: FinancialStatement[];
  entity: "town" | "school" | "all";
}

// Pick ONE statement per fiscal year to represent the entity's "official" numbers.
// Priority: audit > acfr > financial_statement > adopted budget > advertised budget > anything.
const PRIORITY = ["audit", "acfr", "financial_statement", "annual_report", "budget", "ufb"];
function priority(s: FinancialStatement): number {
  const idx = PRIORITY.indexOf((s.statement_type || "").toLowerCase());
  return idx === -1 ? 99 : idx;
}
// Among same-type, prefer "Adopted" over "Advertised"
function variantPriority(s: FinancialStatement): number {
  const n = (s.entity_name || "").toLowerCase();
  if (n.includes("advertised") || n.includes("tentative")) return 2;
  if (n.includes("adopted") || n.includes("final")) return 0;
  return 1;
}

interface YearRow {
  year: string;
  picked: FinancialStatement;
  revenue: number | null;
  expenditures: number | null;
  surplus: number | null;
  fundBalance: number | null;
  debt: number | null;
}

function pickPerYear(statements: FinancialStatement[]): YearRow[] {
  const byYear: Record<string, FinancialStatement[]> = {};
  for (const s of statements) {
    if (!s.fiscal_year || s.status === "needs_reextraction" || s.status === "needs_reprocess") continue;
    const y = s.fiscal_year;
    (byYear[y] ??= []).push(s);
  }
  const out: YearRow[] = [];
  for (const y of Object.keys(byYear).sort()) {
    const candidates = byYear[y].slice().sort(
      (a, b) => priority(a) - priority(b) || variantPriority(a) - variantPriority(b),
    );
    const p = candidates[0];
    out.push({
      year: y,
      picked: p,
      revenue: p.total_revenue,
      expenditures: p.total_expenditures,
      surplus: p.surplus_deficit,
      fundBalance: p.fund_balance,
      debt: p.total_debt,
    });
  }
  return out;
}

const fmt = (n: number | null | undefined, abbrev = false) => {
  if (n == null) return "—";
  if (abbrev) {
    if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  }
  return `$${Math.round(n).toLocaleString()}`;
};

const pctChange = (curr: number | null, prev: number | null): string => {
  if (curr == null || prev == null || prev === 0) return "";
  const p = ((curr - prev) / Math.abs(prev)) * 100;
  return ` (${p >= 0 ? "+" : ""}${p.toFixed(1)}%)`;
};

export default function YoYTrajectory({ statements, entity }: Props) {
  const rows = useMemo(() => pickPerYear(statements), [statements]);

  if (rows.length < 2) {
    return null;
  }

  // Sparkline-like rendering: simple inline bars, no chart library
  const maxRev = Math.max(...rows.map(r => r.revenue ?? 0));
  const maxExp = Math.max(...rows.map(r => r.expenditures ?? 0));
  const maxFB = Math.max(...rows.map(r => r.fundBalance ?? 0).map(Math.abs));

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Year-over-Year Trajectory</h3>
          <p className="text-[11px] text-gray-500">
            One statement chosen per FY (priority: audit/ACFR &gt; financial statement &gt; adopted budget &gt; advertised).
          </p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-gray-500">
            <tr className="border-b">
              <th className="text-left py-2 pr-3">FY</th>
              <th className="text-left py-2 pr-3">Source</th>
              <th className="text-right py-2 pr-3">Revenue</th>
              <th className="text-right py-2 pr-3">Expenditures</th>
              <th className="text-right py-2 pr-3">Surplus/Deficit</th>
              <th className="text-right py-2 pr-3">Fund Balance</th>
              <th className="text-right py-2">Debt</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((r, i) => {
              const prev = rows[i - 1];
              return (
                <tr key={r.year} className="hover:bg-gray-50">
                  <td className="py-2 pr-3 font-medium text-gray-900">{r.year}</td>
                  <td className="py-2 pr-3 text-gray-500 capitalize">{r.picked.statement_type}</td>
                  <td className="py-2 pr-3 text-right font-mono">
                    {fmt(r.revenue, true)}
                    <span className="text-[10px] text-gray-400">{prev ? pctChange(r.revenue, prev.revenue) : ""}</span>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">
                    {fmt(r.expenditures, true)}
                    <span className="text-[10px] text-gray-400">{prev ? pctChange(r.expenditures, prev.expenditures) : ""}</span>
                  </td>
                  <td className={`py-2 pr-3 text-right font-mono ${(r.surplus ?? 0) >= 0 ? "text-green-700" : "text-red-700"}`}>
                    {fmt(r.surplus, true)}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{fmt(r.fundBalance, true)}</td>
                  <td className="py-2 text-right font-mono">{fmt(r.debt, true)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Inline trajectory bars */}
      <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-3">
        <Sparkbar label="Revenue" rows={rows} accessor={r => r.revenue} max={maxRev} color="bg-blue-400" />
        <Sparkbar label="Expenditures" rows={rows} accessor={r => r.expenditures} max={maxExp} color="bg-orange-400" />
        <Sparkbar label="Fund Balance" rows={rows} accessor={r => r.fundBalance} max={maxFB} color="bg-purple-400" />
      </div>
    </div>
  );
}

function Sparkbar({ label, rows, accessor, max, color }: {
  label: string; rows: YearRow[]; accessor: (r: YearRow) => number | null; max: number; color: string;
}) {
  if (max === 0) return null;
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <p className="text-[10px] uppercase font-semibold text-gray-500 mb-2">{label}</p>
      <div className="flex items-end gap-1 h-16">
        {rows.map((r) => {
          const v = accessor(r);
          if (v == null) return <div key={r.year} className="flex-1 bg-gray-100 rounded-sm" style={{ height: "4px" }} title={`${r.year}: —`} />;
          const h = Math.max(4, (Math.abs(v) / max) * 64);
          return (
            <div
              key={r.year}
              className={`flex-1 ${color} rounded-sm`}
              style={{ height: `${h}px` }}
              title={`${r.year}: ${fmt(v, true)}`}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-[9px] text-gray-400 mt-1">
        <span>{rows[0]?.year}</span>
        <span>{rows[rows.length - 1]?.year}</span>
      </div>
    </div>
  );
}
