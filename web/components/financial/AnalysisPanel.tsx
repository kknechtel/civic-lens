"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createAnalysis,
  type FinancialStatement,
  type FinancialAnalysisResult,
} from "@/lib/api";

interface Props {
  analyses: FinancialAnalysisResult[];
  statements: FinancialStatement[];
  preSelectedIds: string[];
}

export default function AnalysisPanel({ analyses, statements, preSelectedIds }: Props) {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(preSelectedIds.length > 0);
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState("town");
  const [analysisType, setAnalysisType] = useState("trend");
  const [selectedIds, setSelectedIds] = useState<string[]>(preSelectedIds);

  const createMutation = useMutation({
    mutationFn: () => createAnalysis(name, entityType, analysisType, selectedIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["analyses"] });
      setShowCreate(false);
      setName("");
      setSelectedIds([]);
    },
  });

  const fmt = (n: number | null | undefined) =>
    n != null ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 0 })}` : "-";

  return (
    <div>
      {/* Create new analysis */}
      {showCreate ? (
        <div className="bg-white rounded-xl shadow p-6 mb-6">
          <h3 className="font-semibold text-gray-900 mb-4">New Analysis</h3>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. FY2020-2025 Trend Analysis"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Analysis Type</label>
              <select
                value={analysisType}
                onChange={(e) => setAnalysisType(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="trend">Trend Analysis</option>
                <option value="comparison">Side-by-Side Comparison</option>
                <option value="ratio">Financial Ratios</option>
                <option value="variance">Budget vs. Actual Variance</option>
              </select>
            </div>
          </div>

          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Select Statements ({selectedIds.length} selected)
            </label>
            <div className="max-h-40 overflow-y-auto border rounded-lg divide-y">
              {statements.map((s) => (
                <label
                  key={s.id}
                  className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(s.id)}
                    onChange={() =>
                      setSelectedIds((prev) =>
                        prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id]
                      )
                    }
                    className="rounded"
                  />
                  <span className="text-gray-700">
                    {s.entity_name} - {s.statement_type} FY{s.fiscal_year}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-gray-600">
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!name || selectedIds.length === 0 || createMutation.isPending}
              className="px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {createMutation.isPending ? "Running..." : "Run Analysis"}
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowCreate(true)}
          className="mb-6 px-4 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          + New Analysis
        </button>
      )}

      {/* Existing analyses */}
      <div className="space-y-4">
        {analyses.map((a) => (
          <div key={a.id} className="bg-white rounded-xl shadow p-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-gray-900">{a.name}</h3>
              <span className="text-xs text-gray-400 capitalize">{a.analysis_type}</span>
            </div>
            <p className="text-sm text-gray-500 mb-3">
              Fiscal Years: {a.fiscal_years.join(", ")} | Entity: {a.entity_type}
            </p>

            {a.summary && (
              <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-700 whitespace-pre-line mb-3">
                {a.summary}
              </div>
            )}

            {/* Render results based on type */}
            {a.analysis_type === "trend" && a.results.years && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 pr-4 font-medium text-gray-500">Metric</th>
                      {(a.results.years as string[]).map((y) => (
                        <th key={y} className="text-right py-2 px-3 font-medium text-gray-500">
                          {y}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    <tr>
                      <td className="py-2 pr-4 text-gray-700">Revenue</td>
                      {((a.results.revenue as number[]) || []).map((v, i) => (
                        <td key={i} className="py-2 px-3 text-right">
                          {fmt(v)}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <td className="py-2 pr-4 text-gray-700">Expenditures</td>
                      {((a.results.expenditures as number[]) || []).map((v, i) => (
                        <td key={i} className="py-2 px-3 text-right">
                          {fmt(v)}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <td className="py-2 pr-4 text-gray-700">Surplus/Deficit</td>
                      {((a.results.surplus_deficit as number[]) || []).map((v, i) => (
                        <td
                          key={i}
                          className={`py-2 px-3 text-right font-medium ${
                            v != null && v >= 0 ? "text-green-600" : "text-red-600"
                          }`}
                        >
                          {fmt(v)}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            )}

            {a.analysis_type === "ratio" && a.results.ratios && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 font-medium text-gray-500">Year</th>
                      <th className="text-right py-2 px-3 font-medium text-gray-500">Operating Ratio</th>
                      <th className="text-right py-2 px-3 font-medium text-gray-500">Fund Balance Ratio</th>
                      <th className="text-right py-2 px-3 font-medium text-gray-500">Debt/Revenue</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {(a.results.ratios as any[]).map((r, i) => (
                      <tr key={i}>
                        <td className="py-2 text-gray-700">{r.fiscal_year}</td>
                        <td className="py-2 px-3 text-right">{r.operating_ratio?.toFixed(3) || "-"}</td>
                        <td className="py-2 px-3 text-right">{r.fund_balance_ratio?.toFixed(3) || "-"}</td>
                        <td className="py-2 px-3 text-right">{r.debt_to_revenue?.toFixed(3) || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}

        {analyses.length === 0 && !showCreate && (
          <p className="text-center text-gray-400 py-12">
            No analyses yet. Select statements and run an analysis.
          </p>
        )}
      </div>
    </div>
  );
}
