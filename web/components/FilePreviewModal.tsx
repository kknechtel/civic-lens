'use client';

import React, { useMemo, useState, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { X, Download, ExternalLink, Image as ImageIcon, FileText as FileTextIcon, File as FileIcon, Maximize2, Minimize2 } from 'lucide-react';
import XlsxSheetViewer from './XlsxSheetViewer';
import { useIsMobile } from '@/lib/hooks';

// react-pdf pulls in pdfjs-dist, which references DOMMatrix at module load.
// A static import would crash the initial page bundle on every route that
// loads Providers → GlobalChat → FilePreviewModal. Lazy-load with ssr:false
// so the chunk is only fetched on demand.
const PDFViewer = dynamic(() => import('./PDFViewer'), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-gray-400">Loading PDF viewer…</div>,
});

interface FilePreviewModalProps {
    isOpen: boolean;
    url: string;
    filename: string;
    onClose: () => void;
    initialPage?: number;
    mimeType?: string | null;
    /** Skip auth header (for public / signed S3 URLs). */
    noAuth?: boolean;
}

const getExtension = (name: string): string => {
    const idx = name.lastIndexOf('.');
    return idx >= 0 ? name.slice(idx + 1).toLowerCase() : '';
};

export default function FilePreviewModal({ isOpen, url, filename, onClose, initialPage, mimeType, noAuth }: FilePreviewModalProps) {
    const ext = useMemo(() => getExtension(filename), [filename]);
    const isPdf = mimeType === 'application/pdf' || ext === 'pdf';
    const isImage = ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'].includes(ext) || (mimeType?.startsWith('image/') ?? false);
    const isSpreadsheet = ['xlsx', 'xls', 'csv'].includes(ext) || (mimeType?.includes('spreadsheet') ?? false);
    const isTextLike = !isSpreadsheet && (['txt', 'json', 'md', 'log'].includes(ext) || (mimeType?.startsWith('text/') ?? false) || mimeType === 'application/json');

    const [textContent, setTextContent] = useState<string | null>(null);
    const [textError, setTextError] = useState<string | null>(null);
    const [maximized, setMaximized] = useState(false);
    const isMobile = useIsMobile();
    const fullscreen = isMobile || maximized;

    // Reset to default size when a new file opens.
    useEffect(() => { if (isOpen) setMaximized(false); }, [isOpen, url]);

    // Esc to exit maximize first, then close. Matches chat behavior.
    useEffect(() => {
        if (!isOpen) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            if (maximized) setMaximized(false);
            else onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [isOpen, maximized, onClose]);

    useEffect(() => {
        if (!isOpen || !isTextLike) return;
        setTextContent(null);
        setTextError(null);
        let cancelled = false;
        (async () => {
            try {
                const resp = await fetch(url);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const ct = resp.headers.get('content-type') || '';
                const data = ct.includes('application/json')
                    ? JSON.stringify(await resp.json(), null, 2)
                    : await resp.text();
                if (!cancelled) setTextContent(data);
            } catch (e: unknown) {
                if (!cancelled) setTextError(e instanceof Error ? e.message : 'Failed to load text content');
            }
        })();
        return () => { cancelled = true; };
    }, [isOpen, url, isTextLike]);

    if (!isOpen) return null;

    return (
        <div
            className={`fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 ${fullscreen ? 'p-0' : 'p-4'}`}
            onClick={onClose}
        >
            <div
                className={`bg-white shadow-xl flex flex-col ${fullscreen ? 'w-screen h-screen rounded-none' : 'w-full max-w-5xl h-[85vh] rounded-lg'}`}
                onClick={(e) => e.stopPropagation()}
            >
                <div
                    className="flex items-center justify-between p-4 border-b border-gray-200"
                    onDoubleClick={() => setMaximized(m => !m)}
                    title="Double-click to toggle full screen"
                >
                    <div className="flex items-center gap-2 min-w-0">
                        {isPdf ? <FileIcon className="w-5 h-5 text-red-600" />
                            : isImage ? <ImageIcon className="w-5 h-5 text-blue-600" />
                            : isTextLike ? <FileTextIcon className="w-5 h-5 text-gray-600" />
                            : <FileIcon className="w-5 h-5 text-gray-600" />}
                        <div className="truncate text-sm text-gray-800">{filename}</div>
                    </div>
                    <div className="flex items-center gap-1 md:gap-2">
                        <a href={url} target="_blank" rel="noopener noreferrer" className="flex items-center justify-center min-w-[40px] min-h-[40px] md:min-w-0 md:min-h-0 md:px-2 md:py-1 text-xs rounded-md md:border md:border-gray-200 hover:bg-gray-100 text-gray-700" title="Open in new tab">
                            <ExternalLink className="w-5 h-5 md:w-4 md:h-4" />
                        </a>
                        <a href={url} download={filename} className="flex items-center justify-center min-w-[40px] min-h-[40px] md:min-w-0 md:min-h-0 md:px-2 md:py-1 text-xs rounded-md md:border md:border-gray-200 hover:bg-gray-100 text-gray-700" title="Download">
                            <Download className="w-5 h-5 md:w-4 md:h-4" />
                        </a>
                        {!isMobile && (
                            <button
                                onClick={() => setMaximized(m => !m)}
                                className="p-2 hover:bg-gray-100 rounded"
                                title={maximized ? 'Exit full screen' : 'Full screen'}
                            >
                                {maximized ? <Minimize2 className="w-4 h-4 text-gray-500" /> : <Maximize2 className="w-4 h-4 text-gray-500" />}
                            </button>
                        )}
                        <button onClick={onClose} className="flex items-center justify-center min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 md:p-2 hover:bg-gray-100 rounded" title="Close">
                            <X className="w-6 h-6 md:w-5 md:h-5 text-gray-500" />
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-hidden bg-gray-50">
                    {isPdf ? (
                        <PDFViewer isOpen embedded fileUrl={url} filename={filename} onClose={onClose} initialPage={initialPage} noAuth={noAuth} />
                    ) : isSpreadsheet ? (
                        <XlsxSheetViewer fileUrl={url} filename={filename} noAuth={noAuth} />
                    ) : isImage ? (
                        <div className="w-full h-full flex items-center justify-center bg-white">
                            <img src={url} alt={filename} className="max-w-full max-h-full object-contain" />
                        </div>
                    ) : isTextLike ? (
                        <div className="w-full h-full overflow-auto p-4 bg-white">
                            {textError ? <div className="text-sm text-red-600">{textError}</div>
                                : textContent === null ? <div className="text-sm text-gray-500">Loading text…</div>
                                : <pre className="text-xs whitespace-pre-wrap break-words">{textContent}</pre>}
                        </div>
                    ) : (
                        <div className="w-full h-full flex items-center justify-center">
                            <div className="text-center text-gray-600 text-sm">
                                <div className="mb-2">Preview not available for this file type.</div>
                                <div className="space-x-2">
                                    <a href={url} target="_blank" rel="noopener noreferrer" className="px-3 py-2 text-xs rounded-md border border-gray-200 hover:bg-gray-100">Open in new tab</a>
                                    <a href={url} download={filename} className="px-3 py-2 text-xs rounded-md border border-gray-200 hover:bg-gray-100">Download</a>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
