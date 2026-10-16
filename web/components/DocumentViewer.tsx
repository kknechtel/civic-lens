"use client";

import { useQuery } from "@tanstack/react-query";
import { getDocumentViewUrl, type Document } from "@/lib/api";
import {
  XMarkIcon,
  ArrowTopRightOnSquareIcon,
  ArrowDownTrayIcon,
  SparklesIcon,
  DocumentTextIcon,
  TagIcon,
  CalendarIcon,
  FolderIcon,
} from "@heroicons/react/24/outline";

interface DocumentViewerProps {
  document: Document | null;
  isOpen: boolean;
  onClose: () => void;
}

export default function DocumentViewer({ document: doc, isOpen, onClose }: DocumentViewerProps) {
  const { data: viewData } = useQuery({
    queryKey: ["doc-view-url", doc?.id],
    queryFn: () => getDocumentViewUrl(doc!.id),
    enabled: isOpen && !!doc,
  });

  if (!isOpen || !doc) return null;

  const url = viewData?.url;
  const isPdf = doc.filename.toLowerCase().endsWith(".pdf");
  const isImage = /\.(png|jpg|jpeg|gif|webp)$/i.test(doc.filename);
  const tags = (doc as any).metadata_?.ai_tags || [];

  return (
    <div className="fixed inset-0 bg-black/70 flex z-50">
      {/* Document info panel */}
      <div className="w-[380px] bg-white flex flex-col border-r overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 border-b bg-gray-50">
          <div className="flex items-center gap-2 mb-2">
            <DocumentTextIcon className="w-5 h-5 text-primary-500" />
            <h2 className="font-semibold text-gray-900 text-sm truncate">{doc.filename}</h2>
          </div>
          <div className="flex gap-2 text-xs text-gray-500">
            {doc.category && (
              <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full capitalize">{doc.category}</span>
            )}
            {doc.doc_type && (
              <span className="px-2 py-0.5 bg-primary-50 text-primary-600 rounded-full">{doc.doc_type}</span>
            )}
            {doc.fiscal_year && (
              <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full">FY {doc.fiscal_year}</span>
            )}
          </div>
        </div>

        {/* AI Summary */}
        <div className="flex-1 overflow-y-auto">
          {doc.notes && (
            <div className="px-5 py-4 border-b">
              <div className="flex items-center gap-2 mb-2">
                <SparklesIcon className="w-4 h-4 text-purple-500" />
                <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wider">AI Summary</h3>
              </div>
              <p className="text-sm text-gray-700 leading-relaxed">{doc.notes}</p>
            </div>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <div className="px-5 py-3 border-b">
              <div className="flex items-center gap-2 mb-2">
                <TagIcon className="w-4 h-4 text-gray-400" />
                <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Tags</h3>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {tags.map((tag: string, i: number) => (
                  <span key={i} className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full text-xs">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Metadata */}
          <div className="px-5 py-3 border-b">
            <div className="flex items-center gap-2 mb-2">
              <FolderIcon className="w-4 h-4 text-gray-400" />
              <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Details</h3>
            </div>
            <div className="space-y-1.5 text-xs">
              <InfoRow label="Filename" value={doc.original_filename} />
              <InfoRow label="Size" value={formatBytes(doc.file_size)} />
              <InfoRow label="Type" value={doc.content_type || "Unknown"} />
              <InfoRow label="Status" value={doc.status} />
              <InfoRow label="Uploaded" value={new Date(doc.created_at).toLocaleDateString()} />
              {doc.department && <InfoRow label="Department" value={doc.department} />}
            </div>
          </div>

          {/* Actions */}
          <div className="px-5 py-3">
            <div className="flex gap-2">
              {url && (
                <>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 px-3 py-2 text-xs bg-primary-50 text-primary-700 rounded-lg hover:bg-primary-100 border border-primary-200"
                  >
                    <ArrowTopRightOnSquareIcon className="w-3.5 h-3.5" /> Open
                  </a>
                  <a
                    href={url}
                    download={doc.filename}
                    className="flex items-center gap-1.5 px-3 py-2 text-xs bg-gray-50 text-gray-700 rounded-lg hover:bg-gray-100 border border-gray-200"
                  >
                    <ArrowDownTrayIcon className="w-3.5 h-3.5" /> Download
                  </a>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Document viewer */}
      <div className="flex-1 flex flex-col bg-gray-900">
        {/* Viewer header */}
        <div className="flex items-center justify-between px-4 py-2 bg-gray-800">
          <span className="text-sm text-gray-300 truncate">{doc.filename}</span>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-white rounded hover:bg-gray-700"
          >
            <XMarkIcon className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {!url ? (
            <div className="flex items-center justify-center h-full text-gray-500">
              Loading...
            </div>
          ) : isPdf ? (
            <iframe src={url} className="w-full h-full border-0" title={doc.filename} />
          ) : isImage ? (
            <div className="w-full h-full flex items-center justify-center p-8">
              <img src={url} alt={doc.filename} className="max-w-full max-h-full object-contain" />
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <p className="mb-2">Preview not available</p>
                <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary-400 hover:underline">
                  Open in new tab
                </a>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-400">{label}</span>
      <span className="text-gray-700 truncate ml-2 max-w-[200px]">{value}</span>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}
