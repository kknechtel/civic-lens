"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getStatements,
  getStatementLineItems,
  getStatementRawExtraction,
  type FinancialStatement,
  type LineItem,
} from "@/lib/api";
import {
  BuildingOfficeIcon,
  AcademicCapIcon,
  ArrowDownTrayIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from "@heroicons/react/24/outline";
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

const API_BASE = "";
const COLORS = ["#2563eb", "#385854", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#f97316", "#84cc16"];
const fmt = (n: number | null | undefined) => n != null ? `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "-";
const fmtShort = (n: number) => {
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
};

export default function StatementsPage() {
  const [activeEntity, setActiveEntity] = useState<"town" | "school">("town");
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(["revenue", "expenditures", "balance"]));
  const [selectedStmt, setSelectedStmt] = useState<string | null>(null);

  const { data: statements } = useQuery({
    queryKey: ["statements"],
    queryFn: () => getStatements(),
  });

  // Load line items for selected statement
  const { data: lineItems } = useQuery({
    queryKey: ["line-items", selectedStmt],
    queryFn: () => getStatementLineItems(selectedStmt!),
    enabled: !!selectedStmt,
  });

  const { data: rawData } = useQuery({
    queryKey: ["raw-extraction", selectedStmt],
    queryFn: () => getStatementRawExtraction(selectedStmt!),
    enabled: !!selectedStmt,
  });

  const entityStmts = useMemo(() =>
    (statements || [])
      .filter((s) => s.entity_type === activeEntity)
      .sort((a, b) => a.fiscal_year.localeCompare(b.fiscal_year)),
    [statements, activeEntity]
  );

  const stmtsWithData = entityStmts.filter((s) => s.total_revenue || s.total_expenditures || s.fund_balance);

  const toggleSection = (s: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });
  };

  // Chart data
  const revenueExpChart = stmtsWithData.map((s) => ({
    year: `FY${s.fiscal_year}`,
    Revenue: s.total_revenue || 0,
    Expenditures: s.total_expenditures || 0,
  }));

  const fundBalanceChart = stmtsWithData.filter((s) => s.fund_balance).map((s) => ({
    year: `FY${s.fiscal_year}`,
    "Fund Balance": s.fund_balance || 0,
  }));

  const debtChart = stmtsWithData.filter((s) => s.total_debt).map((s) => ({
    year: `FY${s.fiscal_year}`,
    Debt: s.total_debt || 0,
  }));

  // Latest statement for breakdown
  const latest = stmtsWithData[stmtsWithData.length - 1];
  const latestRaw = latest ? (latest as any).raw_extraction : null;

  return (
    <div className="p-3 md:p-8">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4 md:mb-6">
        <div className="min-w-0">
          <h1 className="text-xl md:text-2xl font-bold text-gray-900">Financial Statements</h1>
          <p className="text-xs md:text-sm text-gray-500">P&L, Balance Sheet, and Detailed Analysis</p>
        </div>
        <div className="flex gap-3">
          <a
            href={`${API_BASE}/api/export/financial-statements`}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 w-full sm:w-auto"
          >
            <ArrowDownTrayIcon className="w-4 h-4" /> Download Excel
          </a>
        </div>
      </div>

      {/* Entity toggle */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setActiveEntity("town")}
          className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium ${
            activeEntity === "town" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          <BuildingOfficeIcon className="w-4 h-4" /> Town
        </button>
        <button
          onClick={() => setActiveEntity("school")}
          className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium ${
            activeEntity === "school" ? "bg-orange-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          <AcademicCapIcon className="w-4 h-4" /> School District
        </button>
      </div>

      {/* Charts */}
      {revenueExpChart.length > 1 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {/* Revenue vs Expenditures */}
          <div className="bg-white rounded-xl shadow p-4 md:p-6">
            <h3 className="font-semibold text-gray-900 mb-4">Revenue vs. Expenditures</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={revenueExpChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="year" tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={fmtShort} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => fmt(v as number)} />
                <Legend />
                <Bar dataKey="Revenue" fill="#385854" />
                <Bar dataKey="Expenditures" fill="#ef4444" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Fund Balance Trend */}
          {fundBalanceChart.length > 1 && (
            <div className="bg-white rounded-xl shadow p-4 md:p-6">
              <h3 className="font-semibold text-gray-900 mb-4">Fund Balance Trend</h3>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={fundBalanceChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="year" tick={{ fontSize: 12 }} />
                  <YAxis tickFormatter={fmtShort} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => fmt(v as number)} />
                  <Line type="monotone" dataKey="Fund Balance" stroke="#2563eb" strokeWidth={3} dot={{ r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Debt Trend */}
          {debtChart.length > 1 && (
            <div className="bg-white rounded-xl shadow p-4 md:p-6">
              <h3 className="font-semibold text-gray-900 mb-4">Total Debt Outstanding</h3>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={debtChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="year" tick={{ fontSize: 12 }} />
                  <YAxis tickFormatter={fmtShort} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => fmt(v as number)} />
                  <Line type="monotone" dataKey="Debt" stroke="#ef4444" strokeWidth={3} dot={{ r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Key Ratios */}
          <div className="bg-white rounded-xl shadow p-4 md:p-6">
            <h3 className="font-semibold text-gray-900 mb-4">Key Financial Ratios</h3>
            <div className="space-y-3">
              {stmtsWithData.filter((s) => s.total_revenue && s.total_expenditures).map((s) => {
                const opRatio = s.total_expenditures! / s.total_revenue!;
                const fbRatio = s.fund_balance ? s.fund_balance / s.total_expenditures! : null;
                return (
                  <div key={s.fiscal_year} className="flex items-center gap-4 text-sm">
                    <span className="font-medium w-16">FY{s.fiscal_year}</span>
                    <div className="flex-1">
                      <div className="flex justify-between mb-1">
                        <span className="text-gray-500">Operating Ratio</span>
                        <span className={opRatio > 1 ? "text-red-600 font-medium" : "text-green-600 font-medium"}>
                          {(opRatio * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="w-full bg-gray-200 rounded-full h-1.5">
                        <div
                          className={`h-1.5 rounded-full ${opRatio > 1 ? "bg-red-500" : "bg-green-500"}`}
                          style={{ width: `${Math.min(opRatio * 100, 100)}%` }}
                        />
                      </div>
                    </div>
                    {fbRatio != null && (
                      <div className="text-right w-24">
                        <span className="text-xs text-gray-400">FB Ratio</span>
                        <p className="font-medium">{(fbRatio * 100).toFixed(1)}%</p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* P&L Table - Income Statement */}
      <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
        <button
          onClick={() => toggleSection("revenue")}
          className="w-full flex items-center justify-between px-4 md:px-6 py-3 md:py-4 bg-primary-50 border-b hover:bg-primary-100"
        >
          <h3 className="font-semibold text-primary-700">Income Statement - Revenue</h3>
          {expandedSections.has("revenue") ? <ChevronDownIcon className="w-4 h-4" /> : <ChevronRightIcon className="w-4 h-4" />}
        </button>
        {expandedSections.has("revenue") && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left px-4 py-2 font-medium text-gray-500 w-64">Line Item</th>
                  {entityStmts.map((s) => (
                    <th key={s.fiscal_year} className="text-right px-3 py-2 font-medium text-gray-500 min-w-[110px]">
                      FY {s.fiscal_year}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                <FinancialRow label="Total Revenue" values={entityStmts.map((s) => s.total_revenue)} bold green />
                {entityStmts.some((s) => {
                  const raw = s as any;
                  return raw.raw_extraction?.current_fund?.property_tax_revenue || raw.raw_extraction?.general_fund?.property_tax_levy;
                }) && (
                  <FinancialRow
                    label="  Property Tax Revenue"
                    values={entityStmts.map((s) => {
                      const raw = (s as any).raw_extraction;
                      return raw?.current_fund?.property_tax_revenue || raw?.general_fund?.property_tax_levy || null;
                    })}
                  />
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Expenditures */}
      <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
        <button
          onClick={() => toggleSection("expenditures")}
          className="w-full flex items-center justify-between px-4 md:px-6 py-3 md:py-4 bg-red-50 border-b hover:bg-red-100"
        >
          <h3 className="font-semibold text-red-800">Income Statement - Expenditures</h3>
          {expandedSections.has("expenditures") ? <ChevronDownIcon className="w-4 h-4" /> : <ChevronRightIcon className="w-4 h-4" />}
        </button>
        {expandedSections.has("expenditures") && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left px-4 py-2 font-medium text-gray-500 w-64">Line Item</th>
                  {entityStmts.map((s) => (
                    <th key={s.fiscal_year} className="text-right px-3 py-2 font-medium text-gray-500 min-w-[110px]">
                      FY {s.fiscal_year}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                <FinancialRow label="Total Expenditures" values={entityStmts.map((s) => s.total_expenditures)} bold red />
                <FinancialRow
                  label="Surplus / (Deficit)"
                  values={entityStmts.map((s) =>
                    s.total_revenue && s.total_expenditures
                      ? s.total_revenue - s.total_expenditures
                      : s.surplus_deficit
                  )}
                  bold
                  colorBySign
                />
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Balance Sheet */}
      <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
        <button
          onClick={() => toggleSection("balance")}
          className="w-full flex items-center justify-between px-4 md:px-6 py-3 md:py-4 bg-blue-50 border-b hover:bg-blue-100"
        >
          <h3 className="font-semibold text-blue-800">Balance Sheet</h3>
          {expandedSections.has("balance") ? <ChevronDownIcon className="w-4 h-4" /> : <ChevronRightIcon className="w-4 h-4" />}
        </button>
        {expandedSections.has("balance") && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left px-4 py-2 font-medium text-gray-500 w-64">Line Item</th>
                  {entityStmts.map((s) => (
                    <th key={s.fiscal_year} className="text-right px-3 py-2 font-medium text-gray-500 min-w-[110px]">
                      FY {s.fiscal_year}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                <FinancialRow label="Fund Balance" values={entityStmts.map((s) => s.fund_balance)} bold />
                <FinancialRow label="Total Debt" values={entityStmts.map((s) => s.total_debt)} bold red />
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Summary stats */}
      <div className="bg-white rounded-xl shadow p-4 md:p-6">
        <h3 className="font-semibold text-gray-900 mb-4">Year-over-Year Summary</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left px-4 py-2 font-medium text-gray-500">Metric</th>
                {entityStmts.map((s) => (
                  <th key={s.fiscal_year} className="text-right px-3 py-2 font-medium text-gray-500">
                    FY {s.fiscal_year}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              <FinancialRow label="Revenue" values={entityStmts.map((s) => s.total_revenue)} green />
              <FinancialRow label="Expenditures" values={entityStmts.map((s) => s.total_expenditures)} red />
              <FinancialRow
                label="Surplus/(Deficit)"
                values={entityStmts.map((s) =>
                  s.total_revenue && s.total_expenditures ? s.total_revenue - s.total_expenditures : s.surplus_deficit
                )}
                colorBySign
                bold
              />
              <FinancialRow label="Fund Balance" values={entityStmts.map((s) => s.fund_balance)} />
              <FinancialRow label="Total Debt" values={entityStmts.map((s) => s.total_debt)} red />
              {/* YoY Revenue Change */}
              <tr className="bg-gray-50">
                <td className="px-4 py-2 text-gray-600 italic">Revenue YoY %</td>
                {entityStmts.map((s, i) => {
                  if (i === 0 || !s.total_revenue || !entityStmts[i - 1].total_revenue) {
                    return <td key={s.fiscal_year} className="px-3 py-2 text-right text-gray-400">-</td>;
                  }
                  const pct = ((s.total_revenue - entityStmts[i - 1].total_revenue!) / Math.abs(entityStmts[i - 1].total_revenue!)) * 100;
                  return (
                    <td key={s.fiscal_year} className={`px-3 py-2 text-right font-medium ${pct >= 0 ? "text-green-600" : "text-red-600"}`}>
                      {pct > 0 ? "+" : ""}{pct.toFixed(1)}%
                    </td>
                  );
                })}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-2 text-gray-600 italic">Expenditure YoY %</td>
                {entityStmts.map((s, i) => {
                  if (i === 0 || !s.total_expenditures || !entityStmts[i - 1].total_expenditures) {
                    return <td key={s.fiscal_year} className="px-3 py-2 text-right text-gray-400">-</td>;
                  }
                  const pct = ((s.total_expenditures - entityStmts[i - 1].total_expenditures!) / Math.abs(entityStmts[i - 1].total_expenditures!)) * 100;
                  return (
                    <td key={s.fiscal_year} className={`px-3 py-2 text-right font-medium ${pct > 0 ? "text-red-600" : "text-green-600"}`}>
                      {pct > 0 ? "+" : ""}{pct.toFixed(1)}%
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FinancialRow({
  label, values, bold, green, red, colorBySign,
}: {
  label: string; values: (number | null | undefined)[];
  bold?: boolean; green?: boolean; red?: boolean; colorBySign?: boolean;
}) {
  return (
    <tr className={bold ? "font-semibold" : ""}>
      <td className={`px-4 py-2 ${label.startsWith("  ") ? "pl-8 text-gray-500" : "text-gray-800"}`}>
        {label}
      </td>
      {values.map((v, i) => {
        let color = "text-gray-700";
        if (green && v) color = "text-green-600";
        if (red && v) color = "text-red-600";
        if (colorBySign && v != null) color = v >= 0 ? "text-green-600" : "text-red-600";
        return (
          <td key={i} className={`px-3 py-2 text-right ${color}`}>
            {v != null ? fmt(v) : <span className="text-gray-300">-</span>}
          </td>
        );
      })}
    </tr>
  );
}
