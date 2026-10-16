'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'isomorphic-dompurify';

export type FactCheckVerdict = 'supported' | 'partial' | 'unsupported' | 'unresolved' | 'no_source';

export interface FactCheckResultItem {
    /** AH cites by filename, but we keep a kind discriminator for symmetry
     *  with the bank-processor shape so the same FactCheckPanel can run
     *  here unchanged. `kind: 'doc'` is the only one in use today. */
    kind: 'doc' | 'email';
    /** For AH this is the filename (e.g. "AHES-performance report 22-23.pdf"). */
    id: string;
    verdict: FactCheckVerdict;
}

interface MarkdownRendererProps {
    content: string;
    /** Called when the user clicks a `[source: filename.pdf]` pill. */
    onCitationClick?: (info: { filename: string }) => void;
    /** When provided, citation pills get a verdict badge (✓/!/✗/?). */
    factCheckResults?: FactCheckResultItem[];
    brandColor?: string;
}

const VERDICT_BADGE: Record<FactCheckVerdict, { glyph: string; cls: string; title: string }> = {
    supported:   { glyph: '✓', cls: 'bg-emerald-100 text-emerald-800 border-emerald-300', title: 'Supported by source' },
    partial:     { glyph: '!', cls: 'bg-amber-100 text-amber-800 border-amber-300',       title: 'Partially supported' },
    unsupported: { glyph: '✗', cls: 'bg-red-100 text-red-800 border-red-300',             title: 'Not supported by source' },
    unresolved:  { glyph: '?', cls: 'bg-gray-200 text-gray-700 border-gray-300',          title: 'Source not found' },
    no_source:   { glyph: '–', cls: 'bg-gray-100 text-gray-500 border-gray-200',          title: 'No source text available' },
};

/**
 * Markdown renderer used across AH chat + presentations. Lazily loads
 * marked + highlight.js + DOMPurify on the client; falls back to plain
 * text on failure.
 *
 * Recognised AH citation syntax: `[source: filename.pdf]` and
 * `[source: a.pdf, b.pdf]` (comma / pipe / semicolon separated). Each
 * filename becomes a clickable pill that fires `onCitationClick` when
 * clicked, OR — when no callback is provided — dispatches an
 * `ah:open-citation` window event so a side-panel CitationPreview can
 * pick it up. This mirrors bank-processor's `rkc:open-citation` flow.
 *
 * If `factCheckResults` is provided, each pill gets a small verdict
 * badge so the author can see which claims have been verified against
 * the source documents.
 */
const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
    content, onCitationClick, factCheckResults, brandColor = '#385854',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [html, setHtml] = useState<string>('');

    const verdictMap = useMemo(() => {
        const m = new Map<string, FactCheckVerdict>();
        for (const r of factCheckResults || []) {
            // Normalize filenames the same way we render them — case-insensitive,
            // trimmed — so a verdict for "report.pdf" still hits "Report.pdf" cites.
            m.set(r.id.trim().toLowerCase(), r.verdict);
        }
        return m;
    }, [factCheckResults]);

    useEffect(() => {
        let isMounted = true;
        const render = async () => {
            try {
                const [{ marked }, hljsMod] = await Promise.all([
                    import('marked'),
                    import('highlight.js'),
                ]);
                const hljs = (hljsMod as any).default || hljsMod;

                marked.setOptions({
                    gfm: true,
                    breaks: true,
                    highlight: function (code: string, lang?: string) {
                        try {
                            if (lang && hljs.getLanguage(lang)) {
                                return hljs.highlight(code, { language: lang }).value;
                            }
                            return hljs.highlightAuto(code).value;
                        } catch {
                            return code;
                        }
                    },
                } as any);

                // Pre-process AH citations BEFORE markdown so they don't get
                // mangled by the markdown parser. tiptap-markdown sometimes
                // backslash-escapes brackets — undo that first.
                const unescaped = (content || '').replace(/\\([\[\]|])/g, '$1');

                // [source: filename] → ah://cite/<encoded filename> markdown link.
                // Comma / pipe / semicolon split inside one pair of brackets so
                // `[source: a.pdf | b.pdf]` becomes two pills.
                const withCitations = unescaped.replace(
                    /\[source:\s*([^\]]+)\]/g,
                    (_match, filenamesGroup: string) => {
                        const filenames = filenamesGroup
                            .split(/\s*[,|;]\s*|\s+\|\s+/)
                            .map(s => s.trim())
                            .filter(Boolean);
                        if (!filenames.length) return _match;
                        return filenames
                            .map(fn => `[📄 ${fn}](ah://cite/${encodeURIComponent(fn)})`)
                            .join(' ');
                    },
                );

                const rendered = await marked.parse(withCitations);
                const sanitized = DOMPurify.sanitize(typeof rendered === 'string' ? rendered : String(rendered), {
                    ADD_ATTR: ['target'],
                    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel|ah):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
                });
                if (isMounted) setHtml(sanitized);
            } catch {
                const escaped = (content || '')
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                if (isMounted) setHtml(escaped.replace(/\n/g, '<br/>'));
            }
        };

        render();
        return () => { isMounted = false; };
    }, [content]);

    // Decorate citation pills (style + optional verdict badge). Runs after
    // every html / verdictMap change. Idempotent — strips prior decoration
    // before reapplying so re-renders don't stack badges.
    useEffect(() => {
        const root = containerRef.current;
        if (!root) return;
        const anchors = root.querySelectorAll<HTMLAnchorElement>('a[href^="ah://cite/"]');
        anchors.forEach(a => {
            // Strip any prior badge (re-render).
            const next = a.nextElementSibling;
            if (next && next.classList.contains('ah-fc-badge')) {
                next.remove();
            }
            // Pill styling — kept inline so it survives without the prose CSS.
            a.style.display = 'inline-flex';
            a.style.alignItems = 'center';
            a.style.gap = '3px';
            a.style.padding = '2px 8px';
            a.style.borderRadius = '5px';
            a.style.fontSize = '0.7rem';
            a.style.fontWeight = '500';
            a.style.margin = '0 2px';
            a.style.textDecoration = 'none';
            a.style.background = `${brandColor}12`;
            a.style.color = brandColor;
            a.style.border = `1px solid ${brandColor}30`;
            a.style.cursor = 'pointer';

            if (verdictMap.size === 0) return;
            const m = a.getAttribute('href')!.match(/^ah:\/\/cite\/(.+)$/);
            if (!m) return;
            const filename = decodeURIComponent(m[1]).trim().toLowerCase();
            const v = verdictMap.get(filename);
            if (!v) return;
            const meta = VERDICT_BADGE[v];
            const badge = document.createElement('span');
            badge.className = `ah-fc-badge inline-flex items-center justify-center w-4 h-4 ml-1 text-[10px] font-bold border rounded align-middle ${meta.cls}`;
            badge.textContent = meta.glyph;
            badge.title = meta.title;
            a.insertAdjacentElement('afterend', badge);
        });
    }, [html, verdictMap, brandColor]);

    const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
        // closest('a') so clicks on inner content (emoji, badge) still
        // resolve to the parent anchor.
        const a = (e.target as HTMLElement | null)?.closest('a') as HTMLAnchorElement | null;
        if (!a) return;
        const href = a.getAttribute('href') || '';

        const m = href.match(/^ah:\/\/cite\/(.+)$/);
        if (m) {
            e.preventDefault();
            e.stopPropagation();
            const filename = decodeURIComponent(m[1]).trim();
            if (onCitationClick) {
                onCitationClick({ filename });
            } else {
                try {
                    window.dispatchEvent(new CustomEvent('ah:open-citation', {
                        detail: { kind: 'doc', filename },
                    }));
                } catch {}
            }
            return;
        }

        // External http(s): route to the side WebReferencePreview panel
        // when it's mounted, otherwise let the browser open it normally.
        if (/^https?:\/\//i.test(href) && (window as { __ahWebRefPanelMounted?: boolean }).__ahWebRefPanelMounted) {
            e.preventDefault();
            e.stopPropagation();
            const title = a.textContent?.trim() || href;
            window.dispatchEvent(new CustomEvent('ah:open-web-reference', {
                detail: { url: href, title },
            }));
        }
    };

    return (
        <div
            ref={containerRef}
            onClick={handleClick}
            className="markdown-body prose prose-sm max-w-none overflow-x-auto"
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
};

export default MarkdownRenderer;
