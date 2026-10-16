"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getDocuments, extractFinancialData } from "@/lib/api";
import { XMarkIcon } from "@heroicons/react/24/outline";

interface Props {
  onClose: () => void;
}

export default function ExtractModal({ onClose }: Props) {
  const queryClient = useQueryClient();
  const [selectedDocId, setSelectedDocId] = useState("");
  const [entityType, setEntityType] = useState("town");
  const [statementType, setStatementType] = useState("budget");

  const { data: documents } = useQuery({
    queryKey: ["documents"],
    queryFn: () => getDocuments(),
  });

  const extractMutation = useMutation({
    mutationFn: () => extractFinancialData(selectedDocId, entityType, statementType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["statements"] });
      onClose();
    },
  });

  // Filter to only show PDFs that haven't been processed yet
  const availableDocs = documents?.filter(
    (d) => d.content_type === "application/pdf" || d.filename.endsWith(".pdf")
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Extract Financial Data</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <XMarkIcon className="w-6 h-6" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Document</label>
            <select
              value={selectedDocId}
              onChange={(e) => setSelectedDocId(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="">Select a document...</option>
              {availableDocs?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.filename} {d.fiscal_year ? `(FY ${d.fiscal_year})` : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Entity Type</label>
            <select
              value={entityType}
              onChange={(e) => setEntityType(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="town">Town</option>
              <option value="school">School District</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Statement Type</label>
            <select
              value={statementType}
              onChange={(e) => setStatementType(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="budget">Budget</option>
              <option value="audit">Audit Report</option>
              <option value="cafr">CAFR / Annual Financial Report</option>
              <option value="annual_report">Annual Report</option>
            </select>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600">
              Cancel
            </button>
            <button
              onClick={() => extractMutation.mutate()}
              disabled={!selectedDocId || extractMutation.isPending}
              className="px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {extractMutation.isPending ? "Extracting..." : "Extract"}
            </button>
          </div>

          {extractMutation.isError && (
            <p className="text-sm text-red-500">{(extractMutation.error as Error).message}</p>
          )}
        </div>
      </div>
    </div>
  );
}
