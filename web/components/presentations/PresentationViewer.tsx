'use client';

import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import React, { useMemo } from 'react';
import dynamic from 'next/dynamic';
import { Globe, FileText } from 'lucide-react';
import type { DeckSection, DeckAttachment } from '@/lib/presentationsApi';
import NarrativeBlock from './NarrativeBlock';
import TableBlock from './TableBlock';
import ReactComponentBlock from './ReactComponentBlock';
import SourceChip from './SourceChip';
import CitationPreview from './CitationPreview';
import WebReferencePreview from './WebReferencePreview';

// Lazy: AttachmentBlock (and the doc preview path it pulls in) drag in
// react-pdf which uses DOMMatrix at module init — not available during
// Node SSR. Loading it client-only avoids the build-time crash.
const AttachmentBlock = dynamic(() => import('./AttachmentBlock'), { ssr: false });

interface Props {
  title: string;
  sections: DeckSection[];
  attachments: DeckAttachment[];
  /** Public viewer (mode='public') uses the slug-scoped citation endpoint;
   *  authenticated preview (mode='auth') uses the regular doc API. */
  mode?: 'auth' | 'public';
  publicSlug?: string;
  /** When provided, attachment previews fetch from this base URL (used by public viewer). */
  publicAttachmentBase?: string;
  /** Optional ISO timestamp shown in the cover banner + footer. */
  publishedAt?: string | null;
}

const brandColor = '#385854';

/** Walk every narrative section body and pull out unique cited filenames.
 *  Used to render an always-visible Sources panel at the bottom — guarantees
 *  doc links remain reachable even if inline citation pills happen to fail. */
function collectCitedFilenames(sections: DeckSection[]): string[] {
  const seen = new Set<string>();
  const re = /\[source:\s*([^\]]+)\]/g;
  for (const s of sections) {
    const body = s.body || '';
    let m: RegExpExecArray | null;
    while ((m = re.exec(body)) !== null) {
      for (const fn of m[1].split(/\s*[,;|]\s*|\s+\|\s+/)) {
        const trimmed = fn.trim();
        if (trimmed) seen.add(trimmed);
      }
    }
  }
  return Array.from(seen).sort();
}

/**
 * Read-only deck experience — used by both `/p/{slug}` (public) and the
 * in-editor Preview overlay. Renders as a continuous document:
 *
 *   ┌────────────────────────────────┐
 *   │      teal cover banner         │  ← deck title + subtitle
 *   └────────────────────────────────┘
 *      Section heading
 *      ─────────────  (teal rule)
 *      content...
 *
 *      Section heading
 *      ─────────────
 *      content...
 *
 *   ─────────────── (footer rule)
 *      <municipality short name>
 */
export default function PresentationViewer({
  title, sections, attachments, mode = 'auth', publicSlug, publicAttachmentBase, publishedAt,
}: Props) {
  const citedFilenames = useMemo(() => collectCitedFilenames(sections), [sections]);
  const municipality = useMunicipality();

  // If a section is flagged as the cover (is_cover=true), fold its
  // title/subtitle/date into the deck-level cover banner and exclude it
  // from the visible flow — otherwise we'd show two competing title
  // blocks. This mirrors bank-processor's cover-section pattern.
  const coverSection = sections.find(s => s.is_cover);
  const subtitle = coverSection?.subtitle?.trim() || '';
  const coverDate = coverSection?.date?.trim() || '';
  const renderedSections = sections.filter(s => !s.is_cover);

  return (
    <div className="ah-deck min-h-screen">
      <main className="max-w-4xl mx-auto px-3 sm:px-6 pt-4 sm:pt-6 pb-2">
        {/* Cover banner — single instance at the top of the deck. */}
        <div className="ah-deck-cover">
          <div className="ah-cover-eyebrow flex items-center gap-1.5">
            <Globe className="w-3 h-3" />
            <span>{municipality.short_name || municipality.branding.app_name}</span>
          </div>
          <div className="ah-cover-title">{title || 'Untitled presentation'}</div>
          {subtitle && <div className="ah-cover-subtitle">{subtitle}</div>}
          <div className="ah-cover-meta">
            {coverDate || (publishedAt
              ? new Date(publishedAt).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
              : new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }))}
          </div>
        </div>

        {/* Body — continuous flow inside one bordered container, no per-section cards. */}
        <div className="bg-white border border-t-0 border-[#dadfdc] rounded-b-xl px-4 sm:px-6 md:px-10 py-6 md:py-10 space-y-8 md:space-y-10">
          {renderedSections.length === 0 && (
            <p className="text-sm text-gray-500 italic">This presentation has no sections.</p>
          )}
          {renderedSections.map((section) => (
            <ViewerSection
              key={section.id}
              section={section}
              attachments={attachments}
              mode={mode}
              publicAttachmentBase={publicAttachmentBase}
            />
          ))}

          {/* Always-visible Sources panel — guarantees doc links are reachable
              even if an inline citation button somehow fails. Lists every
              unique filename cited anywhere in the deck. */}
          {citedFilenames.length > 0 && (
            <section className="ah-section">
              <h2 className="flex items-center gap-2">
                <FileText className="w-4 h-4 inline-block" style={{ color: brandColor }} />
                Sources ({citedFilenames.length})
              </h2>
              <p className="text-xs text-gray-500 mb-3">Click any document to open its preview.</p>
              <div className="flex flex-wrap gap-2">
                {citedFilenames.map((fn) => (
                  <button
                    key={fn}
                    type="button"
                    onClick={() => {
                      window.dispatchEvent(new CustomEvent('ah:open-citation', {
                        detail: { kind: 'doc', filename: fn },
                      }));
                    }}
                    className="text-xs inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded border transition-colors hover:bg-gray-50"
                    style={{
                      color: brandColor,
                      borderColor: `${brandColor}40`,
                      backgroundColor: `${brandColor}08`,
                    }}
                  >
                    <FileText className="w-3 h-3" />
                    <span className="truncate max-w-md">{fn}</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Single deck-level footer */}
          <div className="ah-deck-footer">
            <div className="ah-deck-wordmark">{municipality.short_name || municipality.branding.app_name}</div>
            <div className="ah-deck-meta">
              {publishedAt
                ? `Published ${new Date(publishedAt).toLocaleDateString()}`
                : 'Draft'}
            </div>
          </div>
        </div>
      </main>

      {/* Side panels — both subscribe to ah:open-citation / ah:open-web-reference
          window events. They render as fixed overlays; CSS in globals.css
          slides .ah-deck left when body has class ah-preview-open. */}
      <CitationPreview mode={mode} publicSlug={publicSlug} />
      <WebReferencePreview />
    </div>
  );
}

function ViewerSection({
  section, attachments, mode, publicAttachmentBase,
}: {
  section: DeckSection;
  attachments: DeckAttachment[];
  mode: 'auth' | 'public';
  publicAttachmentBase?: string;
}) {
  const noop = () => {};
  return (
    <section id={`sec-${section.id}`} className="ah-section scroll-mt-4">
      {section.title && (
        <>
          <div className="mb-1.5">
            <SourceChip section={section} />
          </div>
          <h2>{section.title}</h2>
        </>
      )}
      {section.kind === 'narrative' && (
        <NarrativeBlock section={section} editable={false} onSave={noop} brandColor={brandColor} />
      )}
      {section.kind === 'table' && (
        <TableBlock section={section} editable={false} onSave={noop} brandColor={brandColor} />
      )}
      {section.kind === 'attachment' && (
        <AttachmentBlock
          section={section}
          attachments={attachments}
          onPreview={publicAttachmentBase ? (att) => {
            // Public viewer: open via the slug-scoped attachment URL.
            window.dispatchEvent(new CustomEvent('ah:open-citation', {
              detail: { kind: 'url', url: `${publicAttachmentBase}/${att.id}`, name: att.filename },
            }));
          } : (att) => {
            // Auth viewer: open via the filename citation (resolves through searchDocuments).
            window.dispatchEvent(new CustomEvent('ah:open-citation', {
              detail: { kind: 'doc', filename: att.filename },
            }));
          }}
          brandColor={brandColor}
        />
      )}
      {section.kind === 'react_component' && (
        <ReactComponentBlock section={section} editable={false} onSave={noop} brandColor={brandColor} />
      )}
    </section>
  );
}
