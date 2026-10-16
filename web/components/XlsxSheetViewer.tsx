'use client';

import React, { useEffect, useMemo, useState } from 'react';
import * as XLSX from 'xlsx';
import { getAuthToken } from '@/lib/api';
import { Loader2, AlertCircle } from 'lucide-react';

interface XlsxSheetViewerProps {
  fileUrl: string;
  filename: string;
  /** Skip auth header (for public/signed S3 URLs). */
  noAuth?: boolean;
  /** Max rows to render per sheet — protects against giant exports. */
  maxRows?: number;
}

type Sheet = { name: string; rows: (string | number | null)[][] };

/**
 * Lightweight read-only xlsx/csv viewer. Parses client-side via SheetJS so
 * we don't need a backend endpoint. Rendering caps at maxRows to keep large
 * exports from locking the UI; the user can download for full data.
 */
export default function XlsxSheetViewer({ fileUrl, filename, noAuth, maxRows = 500 }: XlsxSheetViewerProps) {
  const [sheets, setSheets] = useState<Sheet[] | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setError(null);
      setSheets(null);
      try {
        const resp = await fetch(fileUrl, noAuth ? undefined : { headers: getAuthToken() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const buf = await resp.arrayBuffer();
        const wb = XLSX.read(buf, { type: 'array' });

        let didTruncate = false;
        const parsed: Sheet[] = wb.SheetNames.map((name) => {
          const ws = wb.Sheets[name];
          const rows = XLSX.utils.sheet_to_json<(string | number | null)[]>(ws, {
            header: 1, raw: false, defval: null,
          });
          if (rows.length > maxRows) didTruncate = true;
          return { name, rows: rows.slice(0, maxRows) };
        });

        if (!cancelled) {
          setSheets(parsed);
          setActive(0);
          setTruncated(didTruncate);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load spreadsheet');
      }
    })();
    return () => { cancelled = true; };
  }, [fileUrl, noAuth, maxRows]);

  const current = useMemo(() => sheets?.[active] || null, [sheets, active]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-6">
        <div className="text-center">
          <AlertCircle className="w-10 h-10 text-red-500 mx-auto mb-3" />
          <p className="text-sm text-gray-700">{error}</p>
          <p className="text-xs text-gray-500 mt-2">Try downloading the file instead.</p>
        </div>
      </div>
    );
  }
  if (!sheets) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin mx-auto mb-3 text-[#385854]" />
          <p className="text-sm text-gray-600">Loading {filename}…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col bg-white">
      {sheets.length > 1 && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-gray-200 bg-gray-50 overflow-x-auto flex-shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setActive(i)}
              className={`px-3 py-1 text-xs rounded ${i === active ? 'bg-[#385854] text-white' : 'text-gray-700 hover:bg-gray-200'}`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        {current && current.rows.length > 0 ? (
          <table className="w-full text-xs border-collapse">
            <tbody>
              {current.rows.map((row, ri) => (
                <tr key={ri} className={ri === 0 ? 'sticky top-0 bg-gray-100 font-semibold' : 'odd:bg-white even:bg-gray-50'}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="border border-gray-200 px-2 py-1 align-top whitespace-pre-wrap break-words">
                      {cell == null ? '' : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="p-6 text-sm text-gray-500">Empty sheet.</div>
        )}
      </div>
      {truncated && (
        <div className="px-3 py-1.5 text-[11px] text-amber-700 bg-amber-50 border-t border-amber-200 flex-shrink-0">
          Showing first {maxRows} rows. Download the file for the complete dataset.
        </div>
      )}
    </div>
  );
}
