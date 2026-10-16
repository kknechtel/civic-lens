"use client";

import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { uploadMultipleDocuments } from "@/lib/api";
import { XMarkIcon, CloudArrowUpIcon, CheckCircleIcon, ExclamationTriangleIcon } from "@heroicons/react/24/outline";

const brandColor = "#385854";

interface UploadModalProps {
  projectId: string;
  onClose: () => void;
}

interface FileResult {
  filename: string;
  status: string;
}

export default function UploadModal({ projectId, onClose }: UploadModalProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [category, setCategory] = useState("");
  const [results, setResults] = useState<FileResult[] | null>(null);
  const queryClient = useQueryClient();

  const upload = useMutation({
    mutationFn: () => uploadMultipleDocuments(files, projectId, category || undefined),
    onSuccess: (data) => {
      setResults(data.files);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      // Auto-close only on full success
      setTimeout(onClose, 2000);
    },
    onError: (error: any) => {
      if (error.results) {
        setResults(error.results);
      }
    },
  });

  const onDrop = useCallback((accepted: File[]) => {
    setFiles((prev) => [...prev, ...accepted]);
    setResults(null);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"], "image/*": [".png", ".jpg", ".jpeg"] },
  });

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const failedCount = results?.filter((r) => r.status.startsWith("error")).length || 0;
  const successCount = results ? results.length - failedCount : 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Upload Documents</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <XMarkIcon className="w-6 h-6" />
          </button>
        </div>

        {/* Dropzone */}
        {!results && (
          <div
            {...getRootProps()}
            className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors border-gray-300 hover:border-gray-400"
            style={isDragActive ? { borderColor: brandColor, backgroundColor: `${brandColor}08` } : {}}
          >
            <input {...getInputProps()} />
            <CloudArrowUpIcon className="w-10 h-10 mx-auto text-gray-400 mb-2" />
            <p className="text-sm text-gray-600">Drag & drop files here, or click to select</p>
            <p className="text-xs text-gray-400 mt-1">PDF, PNG, JPG</p>
          </div>
        )}

        {/* File list (before upload) */}
        {!results && files.length > 0 && (
          <ul className="mt-4 space-y-2 max-h-40 overflow-y-auto">
            {files.map((f, i) => (
              <li key={i} className="flex items-center justify-between text-sm bg-gray-50 rounded px-3 py-2">
                <span className="truncate flex-1">{f.name}</span>
                <span className="text-xs text-gray-400 ml-2">{(f.size / 1024).toFixed(0)} KB</span>
                <button onClick={() => removeFile(i)} className="text-gray-400 hover:text-red-500 ml-2">
                  <XMarkIcon className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Results (after upload) */}
        {results && (
          <div className="mt-2">
            <div className="mb-3 flex items-center gap-2 text-sm">
              {failedCount === 0 ? (
                <>
                  <CheckCircleIcon className="w-5 h-5 text-green-600" />
                  <span className="font-medium text-green-700">All {successCount} files uploaded</span>
                </>
              ) : (
                <>
                  <ExclamationTriangleIcon className="w-5 h-5 text-amber-600" />
                  <span className="font-medium text-amber-700">
                    {successCount} succeeded, {failedCount} failed
                  </span>
                </>
              )}
            </div>
            <ul className="space-y-1.5 max-h-60 overflow-y-auto">
              {results.map((r, i) => {
                const isError = r.status.startsWith("error");
                return (
                  <li key={i} className={`flex items-start gap-2 text-xs px-3 py-2 rounded ${isError ? "bg-red-50" : "bg-green-50"}`}>
                    {isError ? (
                      <ExclamationTriangleIcon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                    ) : (
                      <CheckCircleIcon className="w-4 h-4 text-green-600 flex-shrink-0 mt-0.5" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className={`font-medium truncate ${isError ? "text-red-800" : "text-green-800"}`}>{r.filename}</p>
                      {isError && <p className="text-red-600 mt-0.5 break-words">{r.status.replace(/^error:\s*/, "")}</p>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Category */}
        {!results && (
          <div className="mt-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="">Select category...</option>
              <option value="town">Town</option>
              <option value="school">School District</option>
              <option value="general">General</option>
            </select>
          </div>
        )}

        {/* Buttons */}
        <div className="mt-6 flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
            {results ? "Close" : "Cancel"}
          </button>
          {!results && (
            <button
              onClick={() => upload.mutate()}
              disabled={files.length === 0 || upload.isPending}
              className="px-4 py-2 text-sm text-white rounded-lg hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
              style={{ backgroundColor: brandColor }}
            >
              {upload.isPending ? "Uploading..." : `Upload ${files.length} file${files.length !== 1 ? "s" : ""}`}
            </button>
          )}
          {results && failedCount > 0 && (
            <button
              onClick={() => {
                setResults(null);
                setFiles((prev) => prev.filter((f) => results.find((r) => r.filename === f.name)?.status.startsWith("error")));
              }}
              className="px-4 py-2 text-sm text-white rounded-lg hover:opacity-90"
              style={{ backgroundColor: brandColor }}
            >
              Retry Failed
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
