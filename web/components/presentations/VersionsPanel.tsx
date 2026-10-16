'use client';

/**
 * VersionsPanel — slide-in side panel listing every PresentationVersion
 * row for the active deck, with Roll-back-to-this-version actions.
 * Mounted by PresentationEditor when the operator clicks the "Versions"
 * toolbar button. Backed by `GET /api/presentations/{id}/versions` and
 * `POST /api/presentations/{id}/rollback-to/{version_no}`.
 */
import React, { useEffect, useState } from 'react';
import { Loader2, X, RotateCcw, Globe, AlertTriangle } from 'lucide-react';
import { getAuthToken } from '@/lib/api';
import { listVersions, rollbackToVersion, type VersionSummary } from '@/lib/presentationsApi';

interface Props {
  presentationId: string;
  open: boolean;
  onClose: () => void;
  /** Fired after a successful rollback so the editor reloads the deck
   *  from the now-current draft (which the rollback wrote into). */
  onAfterRollback?: () => void;
  brandColor?: string;
}

export default function VersionsPanel({
  presentationId, open, onClose, onAfterRollback, brandColor = '#385854',
}: Props) {
  // Reference getAuthToken so the import isn't tree-shaken away — the
  // shared API client below already injects the header for us, but
  // keeping the symbol referenced keeps eslint quiet and matches the
  // pattern used by sibling panels (CitationAuditPanel) that hit the
  // direct fetch path for password-protected resources.
  void getAuthToken;

  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [rollingBack, setRollingBack] = useState<number | null>(null);
  const [confirmingRollback, setConfirmingRollback] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listVersions(presentationId);
      setVersions(data.versions || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load versions');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, presentationId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const rollback = async (versionNo: number) => {
    setRollingBack(versionNo);
    setError(null);
    try {
      await rollbackToVersion(presentationId, versionNo);
      await load();
      if (onAfterRollback) onAfterRollback();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Rollback failed');
    } finally {
      setRollingBack(null);
      setConfirmingRollback(null);
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 bg-black/30 z-40"
        aria-label="Close versions panel"
      />
      <aside className="fixed right-0 top-0 bottom-0 w-[480px] max-w-[90vw] bg-white z-50 shadow-2xl flex flex-col border-l border-gray-200">
        <header className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Publish history</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Each row is an immutable snapshot. Edits stay in draft until you republish.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center gap-2 px-5 py-6 text-sm text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading versions…
            </div>
          )}
          {error && (
            <div className="m-4 px-3 py-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {versions && versions.length === 0 && !loading && (
            <div className="px-5 py-6 text-sm text-gray-500 italic">
              No publish history yet. Click Publish to create v1.
            </div>
          )}
          {versions && versions.map(v => {
            const dt = v.published_at ? new Date(v.published_at) : null;
            const isCurrent = v.is_current_public;
            const isRollback = v.rolled_back_from_version_no != null;
            return (
              <div
                key={v.version_no}
                className={`border-b border-gray-100 px-5 py-4 ${isCurrent ? 'bg-teal-50/40' : ''}`}
              >
                <div className="flex items-center justify-between gap-3 mb-1">
                  <div className="flex items-baseline gap-2">
                    <span className="font-semibold text-gray-900">v{v.version_no}</span>
                    {isCurrent && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold bg-teal-700 text-white rounded">
                        <Globe className="w-2.5 h-2.5" /> CURRENT PUBLIC
                      </span>
                    )}
                    {isRollback && (
                      <span className="text-[10px] text-gray-500 italic">
                        ← rolled back to v{v.rolled_back_from_version_no}
                      </span>
                    )}
                  </div>
                  {!isCurrent && (
                    confirmingRollback === v.version_no ? (
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => rollback(v.version_no)}
                          disabled={rollingBack === v.version_no}
                          className="px-2 py-1 text-xs text-white rounded disabled:opacity-50"
                          style={{ backgroundColor: brandColor }}
                        >
                          {rollingBack === v.version_no
                            ? <Loader2 className="w-3 h-3 animate-spin" />
                            : 'Confirm'}
                        </button>
                        <button
                          onClick={() => setConfirmingRollback(null)}
                          disabled={rollingBack === v.version_no}
                          className="px-2 py-1 text-xs text-gray-600 border border-gray-300 rounded hover:bg-gray-50"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmingRollback(v.version_no)}
                        className="flex items-center gap-1 px-2 py-1 text-xs text-gray-700 border border-gray-300 rounded hover:bg-gray-50"
                        title="Replace draft with this version's content + republish"
                      >
                        <RotateCcw className="w-3 h-3" /> Roll back
                      </button>
                    )
                  )}
                </div>
                <div className="text-sm text-gray-800 truncate" title={v.title}>{v.title}</div>
                <div className="text-[11px] text-gray-500 mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                  {dt && (
                    <span title={dt.toISOString()}>{dt.toLocaleString()}</span>
                  )}
                  {v.published_by && (
                    <span>by {v.published_by}</span>
                  )}
                  <span>{v.section_count} sections</span>
                  <span>{v.doc_snapshot_count} doc snapshots</span>
                </div>
                {confirmingRollback === v.version_no && (
                  <div className="mt-2 px-3 py-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded">
                    Rollback replaces your current draft with v{v.version_no} and creates a new
                    published version. Linear history is preserved — nothing is deleted.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
