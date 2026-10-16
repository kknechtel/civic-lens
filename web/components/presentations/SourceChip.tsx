'use client';

/**
 * SourceChip — small gray pill rendered above a section's heading
 * indicating the primary source the section is built from.
 *
 * Reads three optional fields from the section:
 *   - source_label: short chip text (e.g. "FY2024 ACFR", "TRD")
 *   - source_url:   external URL (state DOE page, news article, etc.)
 *   - source_filename: filename of an indexed AH document — clicking
 *                      opens it in the CitationPreview side panel via
 *                      the `ah:open-citation` event.
 *
 * If both source_filename and source_url are present, the chip prefers
 * the local doc (cleaner UX). When neither is set, the chip just labels
 * the source statically.
 */

import React from 'react';
import { ExternalLink, FileText } from 'lucide-react';

// Hosts known to send X-Frame-Options: DENY so iframe embedding shows
// blank. Clicking those chips opens in a new tab instead.
const IFRAME_BLOCKED_HOSTS = new Set<string>([
    'pacer.uscourts.gov',
    'ecf.uscourts.gov',
    'sec.gov',
    'state.nj.us',
    'nj.gov',
    'monmouthcountynj.gov',
    'app.com',
    'asburyparkpress.com',
]);

function isIframeBlockedHost(url: string): boolean {
    try {
        const host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
        return IFRAME_BLOCKED_HOSTS.has(host);
    } catch {
        return false;
    }
}

interface Props {
    /** Pulled from section content; tolerates missing/null. */
    section: {
        source_label?: string | null;
        source_url?: string | null;
        source_filename?: string | null;
    } | null | undefined;
}

export default function SourceChip({ section }: Props) {
    const label = String(section?.source_label || '').trim();
    const url = String(section?.source_url || '').trim();
    const filename = String(section?.source_filename || '').trim();
    if (!label) return null;

    const handleClick = (e: React.MouseEvent) => {
        if (filename) {
            e.preventDefault();
            window.dispatchEvent(new CustomEvent('ah:open-citation', {
                detail: { kind: 'doc', filename },
            }));
            return;
        }
        if (url) {
            if (isIframeBlockedHost(url)) {
                // Let the default <a target="_blank"> handler take over.
                return;
            }
            e.preventDefault();
            window.dispatchEvent(new CustomEvent('ah:open-citation', {
                detail: { kind: 'url', url, name: label },
            }));
        }
    };

    const Icon = filename ? FileText : ExternalLink;
    const className =
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-100 hover:bg-gray-200 ' +
        'border border-gray-200 text-[11px] text-gray-700 hover:text-gray-900 transition-colors';

    if (filename || url) {
        const shouldNewTab = !!url && (!filename || isIframeBlockedHost(url));
        return (
            <a
                href={url || '#'}
                target={shouldNewTab ? '_blank' : undefined}
                rel="noopener noreferrer"
                onClick={handleClick}
                className={className}
                title={filename ? 'Open source document' : url ? `Open ${url}` : label}
            >
                <Icon className="w-3 h-3 flex-shrink-0" />
                <span className="font-medium">{label}</span>
            </a>
        );
    }

    return (
        <span className={className.replace(' hover:bg-gray-200', '').replace(' hover:text-gray-900', '').replace(' transition-colors', '')}>
            <Icon className="w-3 h-3 flex-shrink-0" />
            <span className="font-medium">{label}</span>
        </span>
    );
}
