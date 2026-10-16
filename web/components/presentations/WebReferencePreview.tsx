'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Globe, Loader2, X, ShieldAlert } from 'lucide-react';

/**
 * WebReferencePreview — fixed side panel that loads an arbitrary URL in an
 * iframe for side-by-side reading while editing the deck. Mirrors
 * CitationPreview's anchoring + body-class shift so the deck content
 * tiles next to it instead of being overlapped.
 *
 * Open from anywhere by dispatching a window event:
 *   window.dispatchEvent(new CustomEvent('ah:open-web-reference',
 *     { detail: { url: 'https://...', title: 'Optional label' } }));
 *
 * Many sites set X-Frame-Options: DENY, CSP frame-ancestors, OR run
 * adblock detection that shows an overlay when sandboxed. We can't
 * fix those, but we DO know which hosts are persistently hostile (news,
 * paywalls, government court sites) and skip the iframe entirely for
 * those — show a friendly card with "Open in new tab" instead of
 * leaving the user on a useless empty iframe.
 */

const NO_IFRAME_HOSTS = [
    // Government / courts that frame-bust
    'pacer.uscourts.gov',
    'ecf.uscourts.gov',
    'sec.gov',
    'state.nj.us',
    'nj.gov',
    'monmouthcountynj.gov',
    // News / paywall hosts
    'nytimes.com',
    'wsj.com',
    'ft.com',
    'reuters.com',
    'bloomberg.com',
    'forbes.com',
    'economist.com',
    'newyorker.com',
    'washingtonpost.com',
    'cnbc.com',
    'foxnews.com',
    'cnn.com',
    'nbcnews.com',
    'businessinsider.com',
    'marketwatch.com',
    'barrons.com',
    'app.com',
    'asburyparkpress.com',
];

function looksUnembeddable(url: string): boolean {
    try {
        const host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
        return NO_IFRAME_HOSTS.some(h => host === h || host.endsWith('.' + h));
    } catch {
        return false;
    }
}

const brandColor = '#385854';

export default function WebReferencePreview() {
    const [item, setItem] = useState<{ url: string; title?: string } | null>(null);
    const [loading, setLoading] = useState(false);
    const [forceIframe, setForceIframe] = useState(false);

    useEffect(() => {
        const onOpen = (e: Event) => {
            const detail = (e as CustomEvent).detail || {};
            const url = String(detail.url || '').trim();
            if (!url) return;
            setItem({ url, title: detail.title || url });
            setLoading(true);
            setForceIframe(false);
        };
        window.addEventListener('ah:open-web-reference', onOpen);
        // Mounted-flag so MarkdownRenderer can decide whether to intercept
        // external link clicks (route to this panel) or let the browser
        // open them in a new tab when this panel isn't on the page.
        (window as { __ahWebRefPanelMounted?: boolean }).__ahWebRefPanelMounted = true;
        return () => {
            window.removeEventListener('ah:open-web-reference', onOpen);
            try { delete (window as { __ahWebRefPanelMounted?: boolean }).__ahWebRefPanelMounted; } catch {}
        };
    }, []);

    // Body class so .ah-deck shifts left when ANY side panel is open.
    useEffect(() => {
        if (typeof document === 'undefined') return;
        document.body.classList.toggle('ah-preview-open', !!item);
        return () => { document.body.classList.remove('ah-preview-open'); };
    }, [item]);

    // Esc closes — mirrors CitationPreview so both panels behave the same.
    useEffect(() => {
        if (!item) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setItem(null);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [item]);

    const hostInfo = useMemo(() => {
        if (!item?.url) return { host: '', favicon: '' };
        try {
            const host = new URL(item.url).hostname.replace(/^www\./, '');
            return { host, favicon: `https://www.google.com/s2/favicons?domain=${host}&sz=64` };
        } catch {
            return { host: '', favicon: '' };
        }
    }, [item?.url]);

    if (!item) return null;

    const skipIframe = looksUnembeddable(item.url) && !forceIframe;

    return (
        <div className="fixed inset-y-0 right-0 w-[min(960px,60vw)] bg-white border-l border-gray-200 shadow-2xl z-[60] flex flex-col">
            <div className="flex items-center justify-between p-3 border-b border-gray-200">
                <div className="flex items-center gap-2 min-w-0">
                    {loading && !skipIframe && <Loader2 className="w-4 h-4 animate-spin text-gray-500 flex-shrink-0" />}
                    <Globe className="w-4 h-4 flex-shrink-0" style={{ color: brandColor }} />
                    <div className="text-sm font-semibold text-gray-900 truncate" title={item.title || item.url}>
                        {item.title || item.url}
                    </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                    <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 px-2.5 py-1 text-xs text-white rounded font-medium hover:opacity-90"
                        style={{ backgroundColor: brandColor }}
                        title="Open in a new browser tab"
                    >
                        <ExternalLink className="w-3.5 h-3.5" /> Open in new tab
                    </a>
                    <button
                        onMouseDown={(e) => { e.preventDefault(); setItem(null); }}
                        className="p-1 hover:bg-gray-100 rounded ml-1"
                        title="Close panel (Esc)"
                    >
                        <X className="w-4 h-4" />
                    </button>
                </div>
            </div>

            <div className="flex-1 overflow-hidden bg-gray-50 relative">
                {skipIframe ? (
                    <UnembeddableHostCard
                        item={item}
                        host={hostInfo.host}
                        favicon={hostInfo.favicon}
                        onTryAnyway={() => { setForceIframe(true); setLoading(true); }}
                    />
                ) : (
                    <>
                        <iframe
                            src={item.url}
                            className="w-full h-full border-0 bg-white"
                            title={item.title || item.url}
                            onLoad={() => setLoading(false)}
                            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
                            referrerPolicy="no-referrer"
                        />
                        <div className="absolute bottom-2 right-2 bg-white border border-gray-200 rounded-lg shadow-md px-3 py-2 text-xs text-gray-700 flex items-center gap-2 max-w-xs">
                            <ShieldAlert className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" />
                            <span className="text-[11px]">If the page is blank or shows an adblock prompt, the site blocks embedding.</span>
                            <a
                                href={item.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hover:underline whitespace-nowrap font-medium"
                                style={{ color: brandColor }}
                            >
                                Open ↗
                            </a>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

function UnembeddableHostCard({
    item, host, favicon, onTryAnyway,
}: {
    item: { url: string; title?: string };
    host: string;
    favicon: string;
    onTryAnyway: () => void;
}) {
    return (
        <div className="absolute inset-0 flex items-center justify-center p-6">
            <div className="w-full max-w-md bg-white border border-gray-200 rounded-2xl shadow-md p-7 text-center space-y-4">
                <div className="flex items-center justify-center gap-3">
                    {favicon
                        ? <img src={favicon} alt="" className="w-8 h-8 rounded" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                        : <Globe className="w-7 h-7" style={{ color: brandColor }} />}
                    <div className="text-base font-semibold text-gray-900">{host}</div>
                </div>
                <div>
                    <div className="text-sm font-semibold text-gray-900 mb-1">
                        {item.title && item.title !== item.url ? item.title : 'External source'}
                    </div>
                    <p className="text-xs text-gray-600 leading-relaxed">
                        <strong>{host}</strong> blocks side-panel embedding (frame-bust, paywall,
                        or adblock detection). Open it in a new tab to read without losing your
                        place in the deck.
                    </p>
                </div>
                <div className="flex items-center justify-center gap-2 pt-1">
                    <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 px-4 py-2 text-white text-sm rounded-lg hover:opacity-90 font-medium shadow-sm"
                        style={{ backgroundColor: brandColor }}
                    >
                        <ExternalLink className="w-4 h-4" /> Open in new tab
                    </a>
                    <button
                        type="button"
                        onClick={onTryAnyway}
                        className="px-3 py-2 text-xs text-gray-500 hover:text-gray-800"
                        title="Attempt to load in iframe anyway (most sites on this list will still block)"
                    >
                        Try iframe anyway
                    </button>
                </div>
                <div className="text-[10px] text-gray-400 break-all border-t border-gray-100 pt-3">
                    {item.url}
                </div>
            </div>
        </div>
    );
}
