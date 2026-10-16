"use client";

import { type FinancialStatement } from "@/lib/api";
import { ChartBarSquareIcon } from "@heroicons/react/24/outline";

interface Props {
  statements: FinancialStatement[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onAnalyze: () => void;
}

export default function StatementsList({ statements, selectedIds, onSelectionChange, onAnalyze }: Props) {
  const toggleSelection = (id: string) => {
    onSelectionChange(
      selectedIds.includes(id) ? selectedIds.filter((s) => s !== id) : [...selectedIds, id]
    );
  };

  const fmt = (n: number | null) => (n != null ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 0 })}` : "-");

  return (
    <div>
      {selectedIds.length > 0 && (
        <div className="mb-4 flex items-center gap-3 bg-primary-50 border border-primary-200 rounded-lg px-4 py-3">
          <span className="text-sm text-primary-700">
            {selectedIds.length} statement{selectedIds.length > 1 ? "s" : ""} selected
          </span>
          <button
            onClick={onAnalyze}
            className="flex items-center gap-1 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700"
          >
            <ChartBarSquareIcon className="w-4 h-4" /> Run Analysis
          </button>
        </div>
      )}

      <div className="bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-4 py-3 w-10">
                <input
                  type="checkbox"
                  checked={selectedIds.length === statements.length && statements.length > 0}
                  onChange={() =>
                    onSelectionChange(
                      selectedIds.length === statements.length ? [] : statements.map((s) => s.id)
                    )
                  }
                  className="rounded"
                />
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Entity</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Type</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">FY</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Revenue</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Expenditures</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Surplus/Deficit</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Fund Balance</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {statements.map((s) => (
              <tr key={s.id} className="hover:bg-gray-50">
                <td className="px-4 py-3">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(s.id)}
                    onChange={() => toggleSelection(s.id)}
                    className="rounded"
                  />
                </td>
                <td className="px-4 py-3 font-medium text-gray-900">{s.entity_name}</td>
                <td className="px-4 py-3 text-gray-500 capitalize">{s.statement_type}</td>
                <td className="px-4 py-3 text-gray-500">{s.fiscal_year}</td>
                <td className="px-4 py-3 text-right text-gray-700">{fmt(s.total_revenue)}</td>
                <td className="px-4 py-3 text-right text-gray-700">{fmt(s.total_expenditures)}</td>
                <td
                  className={`px-4 py-3 text-right font-medium ${
                    s.surplus_deficit != null && s.surplus_deficit >= 0
                      ? "text-green-600"
                      : "text-red-600"
                  }`}
                >
                  {fmt(s.surplus_deficit)}
                </td>
                <td className="px-4 py-3 text-right text-gray-700">{fmt(s.fund_balance)}</td>
                <td className="px-4 py-3">
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs ${
                      s.status === "extracted"
                        ? "bg-green-100 text-green-700"
                        : s.status === "processing"
                        ? "bg-yellow-100 text-yellow-700"
                        : s.status === "error"
                        ? "bg-red-100 text-red-700"
                        : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {s.status}
                  </span>
                </td>
              </tr>
            ))}
            {statements.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-12 text-center text-gray-400">
                  No financial statements yet. Upload documents and extract financial data.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
