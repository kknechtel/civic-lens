'use client';

import React, { useEffect, useState } from 'react';
import { X, Loader2, ExternalLink } from 'lucide-react';
import { resolveCitation, getDocumentViewUrl } from '@/lib/api';

interface DocPreview {
    kind: 'doc';
    filename: string;
    /** External-tab URL (presigned S3 URL or local proxy URL). */
    url?: string;
    /** Iframe `src` — either same as `url` or a blob URL. */
    iframeSrc?: string;
}

interface UrlPreview {
    kind: 'url';
    url: string;
    name?: string;
}

type Preview = DocPreview | UrlPreview;

interface Props {
    /** 'auth' (editor): use authed /api/documents/{id}/view-url.
     *  'public' (slug viewer): use /api/presentations/public/{slug}/citation. */
    mode?: 'auth' | 'public';
    publicSlug?: string;
}

const brandColor = '#385854';

/**
 * Side panel that renders a clicked `[source: filename.pdf]` citation
 * (or an SourceChip-emitted url:). Subscribes to the `ah:open-citation`
 * window event that MarkdownRenderer / SourceChip dispatches when the
 * user clicks a citation link.
 *
 * AH-specific: citations are by filename. In auth mode we resolve via
 * `searchDocuments(filename) → getDocumentViewUrl(id)`. In public mode
 * we hit `/api/presentations/public/{slug}/citation?filename=...` which
 * scope-checks the filename against the deck's cited / attached docs.
 */
export default function CitationPreview({ mode = 'auth', publicSlug }: Props = {}) {
    const [preview, setPreview] = useState<Preview | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const onOpen = async (evt: Event) => {
            const detail = (evt as CustomEvent).detail as
                | { kind: 'doc'; filename: string }
                | { kind: 'url'; url: string; name?: string }
                | undefined;
            if (!detail) return;
            setError(null);
            setPreview(null);
            setLoading(true);

            if (detail.kind === 'url') {
                setPreview({ kind: 'url', url: detail.url, name: detail.name });
                setLoading(false);
                return;
            }

            try {
                if (mode === 'public' && publicSlug) {
                    // Public deck: backend scope-checks the filename against
                    // the deck's citations + attachments and returns a signed
                    // S3 URL (and includes the cached deck password if any).
                    const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
                    const headers: Record<string, string> = {};
                    const pw = sessionStorage.getItem('ah-deck-pw:' + publicSlug);
                    if (pw) headers['X-Deck-Password'] = pw;
                    const qs = new URLSearchParams({ filename: detail.filename });
                    const res = await fetch(
                        `${apiBase}/api/presentations/public/${publicSlug}/citation?${qs.toString()}`,
                        { headers },
                    );
                    if (!res.ok) {
                        if (res.status === 404) setError(`Document "${detail.filename}" is not part of this deck`);
                        else setError(`Could not load document (${res.status})`);
                        setLoading(false);
                        return;
                    }
                    const data = await res.json();
                    setPreview({
                        kind: 'doc',
                        filename: data.filename || detail.filename,
                        url: data.url,
                        iframeSrc: data.url,
                    });
                } else {
                    // Auth mode: resolve filename → docId → signed URL.
                    const best = await resolveCitation(detail.filename);
                    if (!best) {
                        setError(`No document found matching "${detail.filename}"`);
                        setLoading(false);
                        return;
                    }
                    const { url } = await getDocumentViewUrl(best.id);
                    setPreview({
                        kind: 'doc',
                        filename: best.filename,
                        url,
                        iframeSrc: url,
                    });
                }
            } catch (e) {
                setError((e as Error).message);
            } finally {
                setLoading(false);
            }
        };

        window.addEventListener('ah:open-citation', onOpen);
        return () => window.removeEventListener('ah:open-citation', onOpen);
    }, [mode, publicSlug]);

    // Body class so the deck content shifts left when ANY side panel is open.
    useEffect(() => {
        if (typeof document === 'undefined') return;
        const isOpen = !!(preview || loading || error);
        document.body.classList.toggle('ah-preview-open', isOpen);
        return () => { document.body.classList.remove('ah-preview-open'); };
    }, [preview, loading, error]);

    // Esc closes — same UX as WebReferencePreview.
    useEffect(() => {
        if (!(preview || loading || error)) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                setPreview(null);
                setError(null);
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [preview, loading, error]);

    if (!preview && !loading && !error) return null;

    return (
        <div className="fixed inset-0 md:inset-y-0 md:right-0 md:left-auto md:w-[min(960px,60vw)] bg-white md:border-l md:border-gray-200 shadow-2xl z-[60] flex flex-col">
            <div className="flex items-center justify-between p-3 border-b border-gray-200">
                <div className="flex items-center gap-2 min-w-0">
                    {loading && <Loader2 className="w-4 h-4 animate-spin text-gray-500 flex-shrink-0" />}
                    <div className="text-sm font-semibold text-gray-900 truncate">
                        {preview?.kind === 'doc' && (preview.filename)}
                        {preview?.kind === 'url' && (preview.name || preview.url)}
                        {!preview && loading && 'Loading citation…'}
                        {!preview && !loading && error && 'Preview error'}
                    </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                    {preview?.kind === 'doc' && preview.url && (
                        <a href={preview.url} target="_blank" rel="noopener noreferrer" className="p-1 hover:bg-gray-100 rounded text-gray-600" title="Open in new tab">
                            <ExternalLink className="w-4 h-4" />
                        </a>
                    )}
                    {preview?.kind === 'url' && (
                        <a href={preview.url} target="_blank" rel="noopener noreferrer" className="p-1 hover:bg-gray-100 rounded text-gray-600" title="Open in new tab">
                            <ExternalLink className="w-4 h-4" />
                        </a>
                    )}
                    <button onClick={() => { setPreview(null); setError(null); }} className="p-1 hover:bg-gray-100 rounded" title="Close (Esc)">
                        <X className="w-4 h-4" />
                    </button>
                </div>
            </div>

            <div className="flex-1 overflow-auto bg-gray-50" style={{ WebkitOverflowScrolling: 'touch' }}>
                {error && <div className="p-3 text-sm text-red-700 bg-red-50">{error}</div>}
                {preview?.kind === 'doc' && (preview.iframeSrc || preview.url) && (
                    <div className="flex flex-col h-full">
                        {/* Mobile escape hatch — iOS Safari's PDF viewer in iframes
                            captures touch events and breaks single-finger scrolling.
                            The fullscreen link punches into the OS PDF reader. */}
                        <a
                            href={preview.url || preview.iframeSrc}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="md:hidden flex items-center justify-center gap-1.5 mx-3 mt-3 mb-2 px-3 py-2 text-white rounded text-sm font-medium active:opacity-90"
                            style={{ backgroundColor: brandColor }}
                        >
                            <ExternalLink className="w-4 h-4" /> Open PDF fullscreen
                        </a>
                        <iframe
                            src={iframeUrlForDoc(preview.iframeSrc || preview.url || '')}
                            className="w-full flex-1 border-0 min-h-0"
                            title={preview.filename}
                            style={{ touchAction: 'manipulation' }}
                        />
                    </div>
                )}
                {preview?.kind === 'url' && (
                    <iframe
                        src={preview.url}
                        className="w-full h-full border-0"
                        title={preview.name || preview.url}
                        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                    />
                )}
            </div>
        </div>
    );
}

/** Append PDF.js / Chrome PDFium viewer hints so the embedded viewer hides
 *  its outline sidebar and gives the side panel's full width to the doc. */
function iframeUrlForDoc(url: string): string {
    if (!url) return url;
    if (url.startsWith('blob:')) return url;
    const sep = url.includes('#') ? '&' : '#';
    return `${url}${sep}toolbar=1&navpanes=0&statusbar=0&pagemode=none&view=FitH`;
}
