'use client';

/**
 * CitationAuditPanel — slide-in side panel that lists every [DOC:id]
 * citation in the current deck alongside the actual filename at the
 * cited document, with per-row "Strip" or "Swap to…" actions.
 *
 * Backed by:
 *   GET  /api/presentations/{id}/audit-citations
 *   POST /api/presentations/{id}/fix-citations
 *
 * Mutates only the DRAFT (presentations.sections). The public viewer
 * keeps serving the previously-published version until the operator
 * clicks Republish, so this panel is safe to use freely.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, X, AlertTriangle, Check, Trash2, RefreshCcw, Search, Clipboard, ClipboardCheck } from 'lucide-react';
import {
  auditCitations,
  applyCitationFixes,
  type CitationAuditResponse,
  type CitationAuditRow,
  type CitationFix,
} from '@/lib/presentationsApi';

function buildCitationAuditMarkdown(audit: CitationAuditResponse, filterLabel: string, rows: CitationAuditRow[]): string {
  const lines: string[] = [];
  lines.push(`**Citation audit** — deck ${audit.presentation_id} (${filterLabel})`);
  lines.push(`- Total citations: ${audit.total_citations}`);
  if (audit.likely_mismatched > 0) lines.push(`- Likely mismatched: ${audit.likely_mismatched}`);
  if (audit.missing > 0) lines.push(`- Missing: ${audit.missing}`);
  lines.push('');
  if (rows.length === 0) {
    lines.push('_No rows in current filter._');
  } else {
    lines.push('| Doc id | Status | Label | Actual filename |');
    lines.push('|---|---|---|---|');
    for (const r of rows) {
      const status = !r.found ? 'missing' : r.looks_mismatched ? `mismatch (score ${r.mismatch_score.toFixed(2)})` : 'ok';
      const label = (r.label || '').replace(/\|/g, '\\|') || '_(no label)_';
      const file = (r.filename || '').replace(/\|/g, '\\|') || '_(not in index)_';
      lines.push(`| ${r.id} | ${status} | ${label} | ${file} |`);
    }
  }
  lines.push('');
  lines.push('Fix: for each mismatch, either Strip the bogus chip or Swap to the correct doc id.');
  return lines.join('\n');
}

type Action = 'keep' | 'strip' | 'swap';

interface Props {
  presentationId: string;
  open: boolean;
  onClose: () => void;
  /** Fired after fixes are applied so the editor invalidates its
   *  cached deck and re-fetches (so the section markdown reloads
   *  with chips removed/swapped). */
  onAfterApply?: () => void;
  brandColor?: string;
}

export default function CitationAuditPanel({
  presentationId, open, onClose, onAfterApply, brandColor = '#385854',
}: Props) {
  const [audit, setAudit] = useState<CitationAuditResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actions, setActions] = useState<Record<string, { action: Action; newId?: string }>>({});
  const [filter, setFilter] = useState<'all' | 'mismatched' | 'missing'>('mismatched');
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<{ edits: number; sections_changed: number } | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    setApplyResult(null);
    try {
      const data = await auditCitations(presentationId);
      setAudit(data);
      const init: Record<string, { action: Action; newId?: string }> = {};
      for (const r of data.rows) {
        init[r.id] = { action: r.looks_mismatched ? 'strip' : 'keep' };
      }
      setActions(init);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Audit failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (open) void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [open, presentationId]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const visibleRows = useMemo(() => {
    if (!audit) return [];
    if (filter === 'missing') return audit.rows.filter(r => !r.found);
    if (filter === 'mismatched') return audit.rows.filter(r => r.looks_mismatched);
    return audit.rows;
  }, [audit, filter]);

  const queuedFixCount = useMemo(() => {
    return Object.values(actions).filter(a => a.action === 'strip' || (a.action === 'swap' && a.newId)).length;
  }, [actions]);

  const apply = async () => {
    setApplying(true);
    setError(null);
    try {
      const fixes: CitationFix[] = Object.entries(actions)
        .filter(([, a]) => a.action === 'strip' || (a.action === 'swap' && a.newId))
        .map(([id, a]) =>
          a.action === 'swap'
            ? { id, action: 'swap', new_id: a.newId as string }
            : { id, action: 'strip' },
        );
      const data = await applyCitationFixes(presentationId, fixes);
      setApplyResult(data);
      if (onAfterApply) onAfterApply();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Fix failed');
    } finally {
      setApplying(false);
    }
  };

  if (!open) return null;

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/30 z-40" aria-label="Close audit panel" />
      <aside className="fixed right-0 top-0 bottom-0 w-[640px] max-w-[95vw] bg-white z-50 shadow-2xl flex flex-col border-l border-gray-200">
        <header className="flex items-start justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Audit citations</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Compare every [DOC:id] chip&rsquo;s label to the actual filename at that PK. Edits the draft only —
              public deck stays untouched until you Republish.
            </p>
            {audit && (
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                  {audit.total_citations} citations
                </span>
                {audit.likely_mismatched > 0 && (
                  <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-200">
                    {audit.likely_mismatched} likely mismatched
                  </span>
                )}
                {audit.missing > 0 && (
                  <span className="px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200">
                    {audit.missing} missing
                  </span>
                )}
              </div>
            )}
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100" aria-label="Close">
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </header>

        {audit && (
          <div className="flex items-center gap-2 px-5 py-2 border-b border-gray-200 bg-gray-50">
            <FilterPill active={filter === 'mismatched'} onClick={() => setFilter('mismatched')}>
              Likely mismatched
            </FilterPill>
            <FilterPill active={filter === 'missing'} onClick={() => setFilter('missing')}>
              Missing
            </FilterPill>
            <FilterPill active={filter === 'all'} onClick={() => setFilter('all')}>
              All
            </FilterPill>
            <div className="ml-auto flex items-center gap-2">
              <CopyButton
                getText={() => buildCitationAuditMarkdown(
                  audit,
                  filter === 'all' ? 'all citations' : filter === 'missing' ? 'missing' : 'likely mismatched',
                  visibleRows,
                )}
              />
              <button
                onClick={load}
                disabled={loading}
                className="flex items-center gap-1 px-2 py-1 text-xs text-gray-700 border border-gray-300 rounded hover:bg-white"
                title="Re-audit"
              >
                <RefreshCcw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Re-audit
              </button>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center gap-2 px-5 py-6 text-sm text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" /> Auditing citations…
            </div>
          )}
          {error && (
            <div className="m-4 px-3 py-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" /><span>{error}</span>
            </div>
          )}
          {applyResult && (
            <div className="m-4 px-3 py-2 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded flex items-start gap-2">
              <Check className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>
                Applied {applyResult.edits} edit{applyResult.edits === 1 ? '' : 's'} across {applyResult.sections_changed} section{applyResult.sections_changed === 1 ? '' : 's'}.
                Republish the deck to push these to the public viewer.
              </span>
            </div>
          )}
          {audit && visibleRows.length === 0 && !loading && (
            <div className="px-5 py-6 text-sm text-gray-500 italic">No rows match the current filter.</div>
          )}
          {visibleRows.map(r => {
            const a = actions[r.id] || { action: 'keep' };
            const setAction = (next: { action: Action; newId?: string }) =>
              setActions(prev => ({ ...prev, [r.id]: next }));
            return (
              <div key={r.id} className="px-5 py-3 border-b border-gray-100">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-xs font-mono font-semibold text-gray-700">DOC:{r.id}</span>
                  {!r.found && (
                    <span className="text-[10px] uppercase font-semibold text-red-600">missing</span>
                  )}
                  {r.found && r.looks_mismatched && (
                    <span className="text-[10px] uppercase font-semibold text-amber-700">mismatch</span>
                  )}
                  {r.found && !r.looks_mismatched && (
                    <span className="text-[10px] uppercase font-semibold text-emerald-700">ok</span>
                  )}
                  {r.mismatch_score > 0 && (
                    <span className="ml-auto text-[10px] text-gray-400">score {r.mismatch_score.toFixed(2)}</span>
                  )}
                </div>
                <div className="text-sm text-gray-800 leading-snug">
                  <span className="text-gray-500">label: </span>
                  <span className="italic">{r.label || <em className="text-gray-400">(no label)</em>}</span>
                </div>
                <div className="text-sm text-gray-800 leading-snug">
                  <span className="text-gray-500">file: </span>
                  <span>{r.filename || <em className="text-gray-400">(not in index)</em>}</span>
                </div>
                <div className="mt-2 flex items-center gap-2 text-xs">
                  <ActionPill
                    label="Keep"
                    active={a.action === 'keep'}
                    onClick={() => setAction({ action: 'keep' })}
                  />
                  <ActionPill
                    label="Strip"
                    icon={<Trash2 className="w-3 h-3" />}
                    active={a.action === 'strip'}
                    onClick={() => setAction({ action: 'strip' })}
                  />
                  <ActionPill
                    label="Swap to…"
                    icon={<Search className="w-3 h-3" />}
                    active={a.action === 'swap'}
                    onClick={() => setAction({ action: 'swap', newId: a.newId || '' })}
                  />
                  {a.action === 'swap' && (
                    <input
                      type="text"
                      placeholder="new doc id"
                      value={a.newId || ''}
                      onChange={e => setAction({ action: 'swap', newId: e.target.value.trim() })}
                      className="px-2 py-1 text-xs border border-gray-300 rounded font-mono w-48"
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <footer className="px-5 py-3 border-t border-gray-200 bg-white flex items-center justify-between gap-2">
          <span className="text-xs text-gray-500">
            {queuedFixCount > 0
              ? `${queuedFixCount} fix${queuedFixCount === 1 ? '' : 'es'} queued`
              : 'No fixes queued — set Strip or Swap on a row above'}
          </span>
          <button
            onClick={apply}
            disabled={applying || queuedFixCount === 0}
            className="px-3 py-1.5 text-sm text-white rounded disabled:opacity-50 flex items-center gap-2"
            style={{ backgroundColor: brandColor }}
          >
            {applying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            Apply fixes
          </button>
        </footer>
      </aside>
    </>
  );
}

function FilterPill({ children, active, onClick }: { children: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-xs rounded border ${active ? 'bg-teal-700 text-white border-teal-700' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}`}
    >
      {children}
    </button>
  );
}

function ActionPill({
  label, icon, active, onClick,
}: {
  label: string;
  icon?: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1 px-2 py-1 rounded border ${active ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}`}
    >
      {icon}{label}
    </button>
  );
}

function CopyButton({ getText }: { getText: () => string }) {
  const [copied, setCopied] = useState(false);
  const handle = async () => {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = getText();
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
  };
  return (
    <button
      onClick={handle}
      className="flex items-center gap-1 px-2 py-1 text-xs text-gray-700 border border-gray-300 rounded hover:bg-white"
      title="Copy audit summary to clipboard (markdown)"
    >
      {copied ? <ClipboardCheck className="w-3 h-3 text-emerald-600" /> : <Clipboard className="w-3 h-3" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
