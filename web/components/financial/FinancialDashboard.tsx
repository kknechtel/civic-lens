"use client";

import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import { useMemo, useState } from "react";
import { type Document, type FinancialStatement } from "@/lib/api";
import {
  BuildingOfficeIcon,
  AcademicCapIcon,
  DocumentTextIcon,
  EyeIcon,
} from "@heroicons/react/24/outline";
import { getDocumentViewUrl } from "@/lib/api";

interface Props {
  townDocs: Document[];
  schoolDocs: Document[];
  statements: FinancialStatement[];
}

interface YearRow {
  year: string;
  townBudget?: Document;
  townAudit?: Document;
  townFS?: Document;
  schoolBudget?: Document;
  schoolAudit?: Document;
  schoolFS?: Document;
}

export default function FinancialDashboard({ townDocs, schoolDocs, statements }: Props) {
  const municipality = useMunicipality();
  const [selectedYear, setSelectedYear] = useState<string | null>(null);

  // Build year-over-year comparison grid
  const yearRows = useMemo(() => {
    const years = new Map<string, YearRow>();

    const normalizeYear = (fy: string | null): string => {
      if (!fy) return "Unknown";
      // Extract just the 4-digit year
      const match = fy.match(/^(20\d{2})/);
      return match ? match[1] : fy;
    };

    const addDoc = (doc: Document, entity: "town" | "school") => {
      const year = normalizeYear(doc.fiscal_year);
      if (year === "Unknown") return;
      if (!years.has(year)) years.set(year, { year });
      const row = years.get(year)!;

      const prefix = entity === "town" ? "town" : "school";
      if (doc.doc_type === "budget" && !row[`${prefix}Budget` as keyof YearRow]) {
        (row as any)[`${prefix}Budget`] = doc;
      } else if (doc.doc_type === "audit" && !row[`${prefix}Audit` as keyof YearRow]) {
        (row as any)[`${prefix}Audit`] = doc;
      } else if (doc.doc_type === "financial_statement" && !row[`${prefix}FS` as keyof YearRow]) {
        (row as any)[`${prefix}FS`] = doc;
      }
    };

    townDocs.forEach((d) => addDoc(d, "town"));
    schoolDocs.forEach((d) => addDoc(d, "school"));

    return Array.from(years.values()).sort((a, b) => b.year.localeCompare(a.year));
  }, [townDocs, schoolDocs]);

  const handleView = async (doc: Document) => {
    const { url } = await getDocumentViewUrl(doc.id);
    window.open(url, "_blank");
  };

  // Summary stats
  const townBudgetCount = townDocs.filter((d) => d.doc_type === "budget").length;
  const townAuditCount = townDocs.filter((d) => d.doc_type === "audit").length;
  const schoolBudgetCount = schoolDocs.filter((d) => d.doc_type === "budget").length;
  const schoolAuditCount = schoolDocs.filter((d) => d.doc_type === "audit").length;

  return (
    <div>
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        {/* Town summary */}
        <div className="bg-white rounded-xl shadow p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-100 rounded-lg">
              <BuildingOfficeIcon className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h2 className="font-semibold text-gray-900">{municipality.name}</h2>
              <p className="text-xs text-gray-500">Municipal Financial Records</p>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <StatBadge label="Budgets" count={townBudgetCount} color="blue" />
            <StatBadge label="Audits" count={townAuditCount} color="green" />
            <StatBadge
              label="Financial Stmts"
              count={townDocs.filter((d) => d.doc_type === "financial_statement").length}
              color="purple"
            />
          </div>
        </div>

        {/* School summary */}
        <div className="bg-white rounded-xl shadow p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-orange-100 rounded-lg">
              <AcademicCapIcon className="w-6 h-6 text-orange-600" />
            </div>
            <div>
              <h2 className="font-semibold text-gray-900">School District (AHES / HHRS)</h2>
              <p className="text-xs text-gray-500">Education Financial Records</p>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <StatBadge label="Budgets" count={schoolBudgetCount} color="orange" />
            <StatBadge label="Audits" count={schoolAuditCount} color="green" />
            <StatBadge
              label="Financial Stmts"
              count={schoolDocs.filter((d) => d.doc_type === "financial_statement").length}
              color="purple"
            />
          </div>
        </div>
      </div>

      {/* Year-over-year comparison table */}
      <div className="bg-white rounded-xl shadow overflow-hidden">
        <div className="px-6 py-4 border-b bg-gray-50">
          <h3 className="font-semibold text-gray-900">Year-over-Year Document Matrix</h3>
          <p className="text-xs text-gray-500 mt-1">
            Click any document to view. Green = available, Gray = missing.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 w-20">FY</th>
                <th colSpan={3} className="text-center px-2 py-3 font-medium text-blue-600 border-l">
                  <div className="flex items-center justify-center gap-1">
                    <BuildingOfficeIcon className="w-4 h-4" /> Town
                  </div>
                </th>
                <th colSpan={3} className="text-center px-2 py-3 font-medium text-orange-600 border-l">
                  <div className="flex items-center justify-center gap-1">
                    <AcademicCapIcon className="w-4 h-4" /> School
                  </div>
                </th>
              </tr>
              <tr className="border-b text-xs text-gray-400">
                <th></th>
                <th className="px-2 py-2 border-l">Budget</th>
                <th className="px-2 py-2">Audit</th>
                <th className="px-2 py-2">Fin. Stmt</th>
                <th className="px-2 py-2 border-l">Budget</th>
                <th className="px-2 py-2">Audit</th>
                <th className="px-2 py-2">Fin. Stmt</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {yearRows.map((row) => (
                <tr key={row.year} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5 font-semibold text-gray-900">{row.year}</td>
                  <DocCell doc={row.townBudget} onView={handleView} color="blue" />
                  <DocCell doc={row.townAudit} onView={handleView} color="blue" />
                  <DocCell doc={row.townFS} onView={handleView} color="blue" />
                  <DocCell doc={row.schoolBudget} onView={handleView} color="orange" borderLeft />
                  <DocCell doc={row.schoolAudit} onView={handleView} color="orange" />
                  <DocCell doc={row.schoolFS} onView={handleView} color="orange" />
                </tr>
              ))}
              {yearRows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-gray-400">
                    No financial documents found. Upload budgets, audits, or financial statements.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Extracted statements (if any) */}
      {statements.length > 0 && (
        <div className="mt-8 bg-white rounded-xl shadow overflow-hidden">
          <div className="px-6 py-4 border-b bg-gray-50">
            <h3 className="font-semibold text-gray-900">Extracted Financial Data</h3>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
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
                  <td className="px-4 py-2.5 text-gray-900">{s.entity_name}</td>
                  <td className="px-4 py-2.5 text-gray-500 capitalize">{s.statement_type}</td>
                  <td className="px-4 py-2.5 text-gray-500">{s.fiscal_year}</td>
                  <td className="px-4 py-2.5 text-right text-green-600">
                    {s.total_revenue ? `$${s.total_revenue.toLocaleString()}` : "-"}
                  </td>
                  <td className="px-4 py-2.5 text-right text-red-600">
                    {s.total_expenditures ? `$${s.total_expenditures.toLocaleString()}` : "-"}
                  </td>
                  <td
                    className={`px-4 py-2.5 text-right font-medium ${
                      (s.surplus_deficit || 0) >= 0 ? "text-green-600" : "text-red-600"
                    }`}
                  >
                    {s.surplus_deficit != null ? `$${s.surplus_deficit.toLocaleString()}` : "-"}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-700">
                    {s.fund_balance ? `$${s.fund_balance.toLocaleString()}` : "-"}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs ${
                        s.status === "extracted"
                          ? "bg-green-100 text-green-700"
                          : s.status === "processing"
                          ? "bg-yellow-100 text-yellow-700"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {s.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatBadge({ label, count, color }: { label: string; count: number; color: string }) {
  const colors: Record<string, string> = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-green-50 text-green-700",
    orange: "bg-orange-50 text-orange-700",
    purple: "bg-purple-50 text-purple-700",
  };
  return (
    <div className={`rounded-lg p-2 text-center ${colors[color]}`}>
      <p className="text-lg font-bold">{count}</p>
      <p className="text-xs">{label}</p>
    </div>
  );
}

function DocCell({
  doc,
  onView,
  color,
  borderLeft,
}: {
  doc?: Document;
  onView: (doc: Document) => void;
  color: string;
  borderLeft?: boolean;
}) {
  if (!doc) {
    return (
      <td className={`px-2 py-2.5 text-center ${borderLeft ? "border-l" : ""}`}>
        <span className="text-gray-300">-</span>
      </td>
    );
  }

  const bgColor = color === "blue" ? "bg-blue-100 text-blue-700 hover:bg-blue-200" : "bg-orange-100 text-orange-700 hover:bg-orange-200";

  return (
    <td className={`px-2 py-2.5 text-center ${borderLeft ? "border-l" : ""}`}>
      <button
        onClick={() => onView(doc)}
        className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium ${bgColor} transition-colors`}
        title={doc.filename}
      >
        <EyeIcon className="w-3 h-3" />
        View
      </button>
    </td>
  );
}
