'use client';

import React from 'react';

/** Grounding result from the chat `citations` event. */
export interface CitationSources {
    verified: { filename: string; title?: string | null; date?: string | null; document_id?: string | null }[];
    unverified: string[];
}

/** Citation filenames in order of first appearance. */
export function citationOrder(content: string): string[] {
    const order: string[] = [];
    const re = /\[(unverified )?source:\s*([^\]]+)\]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(content || ''))) {
        for (const fn of m[2].split(/\s*[,|;]\s*/).map(x => x.trim()).filter(Boolean)) {
            if (!order.includes(fn)) order.push(fn);
        }
    }
    return order;
}

/** Numbered Sources list under an answer. Unverified citations are labelled, not linked. */
export function CitationSourcesList({ order, content, sources, brandColor, onOpen }: {
    order: string[]; content: string; sources?: CitationSources; brandColor: string; onOpen: (filename: string) => void;
}) {
    if (!order.length) return null;
    const verified = new Map((sources?.verified || []).map(v => [v.filename.toLowerCase(), v]));
    const unverified = new Set((sources?.unverified || []).map(u => u.toLowerCase()));
    return (
        <div className="mt-3 border-t border-gray-100 pt-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1">Sources</div>
            <ol className="space-y-0.5">
                {order.map((fn, i) => {
                    const unv = unverified.has(fn.toLowerCase()) || content.includes(`[unverified source: ${fn}]`);
                    const info = verified.get(fn.toLowerCase());
                    return (
                        <li key={fn} className="flex items-start gap-1.5 text-xs leading-snug">
                            <span className={`mt-px shrink-0 font-semibold ${unv ? 'text-amber-700' : ''}`}
                                  style={unv ? undefined : { color: brandColor }}>{unv ? '?' : `${i + 1}.`}</span>
                            {unv ? (
                                <span className="text-amber-800">
                                    {prettyDocName(fn)} <span className="text-amber-600">— not verified: not among the documents searched</span>
                                </span>
                            ) : (
                                <button type="button" onClick={() => onOpen(fn)} className="text-left text-gray-700 hover:underline">
                                    {info?.title || prettyDocName(fn)}
                                    {info?.date && <span className="text-gray-400"> · {info.date}</span>}
                                </button>
                            )}
                        </li>
                    );
                })}
            </ol>
        </div>
    );
}

/** "sites - {GUID} - uploads - LIFEGUARD HQ - ACME.pdf" → "Lifeguard HQ - Acme" */
export function prettyDocName(filename: string): string {
    let n = filename
        .replace(/^.*?\buploads - /i, '')
        .replace(/^sites - \{[^}]+\} - /i, '')
        .replace(/^(Resolutions|Minutes|Agendas|Budgets|Legislation|Misc\.? Documents) — /i, '')
        .replace(/\(\d+\)(?=\.\w+$)/, '')
        .replace(/\.(pdf|docx?|xlsx?|txt|html?)$/i, '')
        .trim();
    if (n === n.toUpperCase() && /[A-Z]{4}/.test(n)) {
        n = n.toLowerCase().replace(/\b([a-z])/g, c => c.toUpperCase()).replace(/\b(Of|And|The|For|To|In|A)\b/g, w => w.toLowerCase());
    }
    return n || filename;
}

