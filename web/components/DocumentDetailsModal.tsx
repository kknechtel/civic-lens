"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updateDocument, deleteDocument, getDocumentViewUrl, type Document } from "@/lib/api";
import DocumentViewer from "./DocumentViewer";
import {
  XMarkIcon,
  DocumentTextIcon,
  FolderIcon,
  CalendarIcon,
  TagIcon,
  EyeIcon,
  TrashIcon,
  PencilIcon,
  CheckIcon,
  ChatBubbleLeftRightIcon,
} from "@heroicons/react/24/outline";

interface Props {
  document: Document | null;
  isOpen: boolean;
  onClose: () => void;
  onChat?: (doc: Document) => void;
}

export default function DocumentDetailsModal({ document: doc, isOpen, onClose, onChat }: Props) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [viewerUrl, setViewerUrl] = useState<string | null>(null);
  const [showViewer, setShowViewer] = useState(false);

  // Editable fields
  const [docType, setDocType] = useState(doc?.doc_type || "");
  const [category, setCategory] = useState(doc?.category || "");
  const [fiscalYear, setFiscalYear] = useState(doc?.fiscal_year || "");
  const [department, setDepartment] = useState(doc?.department || "");
  const [notes, setNotes] = useState(doc?.notes || "");

  // Reset form when doc changes
  if (doc && docType !== (doc.doc_type || "") && !editing) {
    setDocType(doc.doc_type || "");
    setCategory(doc.category || "");
    setFiscalYear(doc.fiscal_year || "");
    setDepartment(doc.department || "");
    setNotes(doc.notes || "");
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updateDocument(doc!.id, {
        doc_type: docType || undefined,
        category: category || undefined,
        fiscal_year: fiscalYear || undefined,
        department: department || undefined,
        notes: notes || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setEditing(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteDocument(doc!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    },
  });

  const handleView = async () => {
    if (!doc) return;
    const { url } = await getDocumentViewUrl(doc.id);
    setViewerUrl(url);
    setShowViewer(true);
  };

  if (!isOpen || !doc) return null;

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-40">
        <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b">
            <div className="flex items-center gap-3 min-w-0">
              <DocumentTextIcon className="w-6 h-6 text-primary-500 flex-shrink-0" />
              <h2 className="font-semibold text-gray-900 truncate">{doc.filename}</h2>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <XMarkIcon className="w-6 h-6" />
            </button>
          </div>

          {/* Actions */}
          <div className="flex gap-2 px-6 py-3 border-b bg-gray-50">
            <button
              onClick={handleView}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700"
            >
              <EyeIcon className="w-4 h-4" /> View
            </button>
            {onChat && (
              <button
                onClick={() => onChat(doc)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-purple-600 text-white rounded-lg hover:bg-purple-700"
              >
                <ChatBubbleLeftRightIcon className="w-4 h-4" /> Chat
              </button>
            )}
            <button
              onClick={() => setEditing(!editing)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-lg hover:bg-gray-100"
            >
              <PencilIcon className="w-4 h-4" /> {editing ? "Cancel" : "Edit"}
            </button>
            {editing && (
              <button
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700"
              >
                <CheckIcon className="w-4 h-4" /> Save
              </button>
            )}
            <div className="flex-1" />
            <button
              onClick={() => {
                if (confirm("Delete this document permanently?")) deleteMutation.mutate();
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
            >
              <TrashIcon className="w-4 h-4" /> Delete
            </button>
          </div>

          {/* Details */}
          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {/* File info */}
            <Section title="File Information" icon={<DocumentTextIcon className="w-4 h-4" />}>
              <InfoRow label="Filename" value={doc.original_filename} />
              <InfoRow label="Size" value={formatBytes(doc.file_size)} />
              <InfoRow label="Type" value={doc.content_type || "Unknown"} />
              <InfoRow label="Status" value={doc.status} />
              <InfoRow label="Uploaded" value={new Date(doc.created_at).toLocaleString()} />
            </Section>

            {/* Classification */}
            <Section title="Classification" icon={<TagIcon className="w-4 h-4" />}>
              {editing ? (
                <div className="space-y-3">
                  <EditField label="Document Type" value={docType} onChange={setDocType}>
                    <option value="">Select...</option>
                    <option value="agenda">Agenda</option>
                    <option value="minutes">Minutes</option>
                    <option value="budget">Budget</option>
                    <option value="audit">Audit</option>
                    <option value="financial_statement">Financial Statement</option>
                    <option value="resolution">Resolution</option>
                    <option value="legal">Legal</option>
                    <option value="presentation">Presentation</option>
                    <option value="general">General</option>
                  </EditField>
                  <EditField label="Category" value={category} onChange={setCategory}>
                    <option value="">Select...</option>
                    <option value="town">Town</option>
                    <option value="school">School District</option>
                    <option value="general">General</option>
                  </EditField>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Fiscal Year</label>
                    <input
                      type="text"
                      value={fiscalYear}
                      onChange={(e) => setFiscalYear(e.target.value)}
                      className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                      placeholder="e.g. 2024 or 2024-2025"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Department</label>
                    <input
                      type="text"
                      value={department}
                      onChange={(e) => setDepartment(e.target.value)}
                      className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    />
                  </div>
                </div>
              ) : (
                <>
                  <InfoRow label="Document Type" value={doc.doc_type || "-"} />
                  <InfoRow label="Category" value={doc.category || "-"} />
                  <InfoRow label="Fiscal Year" value={doc.fiscal_year || "-"} />
                  <InfoRow label="Department" value={doc.department || "-"} />
                </>
              )}
            </Section>

            {/* Notes */}
            <Section title="Notes" icon={<FolderIcon className="w-4 h-4" />}>
              {editing ? (
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                  className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                  placeholder="Add notes about this document..."
                />
              ) : (
                <p className="text-sm text-gray-600">{doc.notes || "No notes"}</p>
              )}
            </Section>
          </div>
        </div>
      </div>

      {/* Document viewer */}
      <DocumentViewer
        document={doc}
        isOpen={showViewer}
        onClose={() => setShowViewer(false)}
      />
    </>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-gray-400">{icon}</span>
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
      </div>
      <div className="bg-gray-50 rounded-lg p-3">{children}</div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-1 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-900 capitalize">{value}</span>
    </div>
  );
}

function EditField({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 mb-1">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
      >
        {children}
      </select>
    </div>
  );
}
