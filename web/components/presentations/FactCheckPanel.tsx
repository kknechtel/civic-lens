'use client';

import React, { useMemo, useState } from 'react';
import {
  ShieldCheck, ShieldAlert, ShieldX, HelpCircle, FileQuestion,
  Loader2, X, ChevronRight,
} from 'lucide-react';
import type { FactCheckRecord } from '@/lib/presentationsApi';

interface Props {
  presentationId: string;
  initial?: FactCheckRecord | null;
  onClose?: () => void;
}

type Verdict = 'supported' | 'partial' | 'unsupported' | 'unresolved' | 'no_source';

interface StreamedResult {
  section_id: string | null;
  section_heading?: string | null;
  kind: 'filename' | 'doc_id';
  id: string;
  label: string;
  verdict: Verdict;
  evidence_quote: string;
  claim: string;
  missing: string[];
  conflicting: string[];
}

const VERDICTS: Record<Verdict, { label: string; color: string; bg: string; border: string; Icon: React.ComponentType<{ className?: string }> }> = {
  supported:   { label: 'Supported',   color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-300', Icon: ShieldCheck },
  partial:     { label: 'Partial',     color: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-300',   Icon: ShieldAlert },
  unsupported: { label: 'Unsupported', color: 'text-red-700',     bg: 'bg-red-50',     border: 'border-red-300',     Icon: ShieldX },
  unresolved:  { label: 'Unresolved',  color: 'text-gray-700',    bg: 'bg-gray-50',    border: 'border-gray-300',    Icon: HelpCircle },
  no_source:   { label: 'No source',   color: 'text-gray-500',    bg: 'bg-gray-50',    border: 'border-gray-200',    Icon: FileQuestion },
};

const brandColor = '#385854';

/**
 * Fact-check side panel. Streams per-citation verdicts from the
 * `/fact-check/stream` SSE endpoint so the user sees results land
 * incrementally instead of waiting on a single 30-90s synchronous
 * response. Final 'complete' event carries the full record which is
 * also persisted server-side, so a reload sees `last_fact_check`
 * populated identically to the synchronous path.
 */
export default function FactCheckPanel({ presentationId, initial, onClose }: Props) {
  const [data, setData] = useState<FactCheckRecord | null>(initial || null);
  const [streaming, setStreaming] = useState<StreamedResult[] | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number }>({ done: 0, total: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const results: StreamedResult[] = (streaming
    || (data?.results as StreamedResult[] | undefined)
    || []);

  const summary = useMemo(() => {
    if (data && !streaming) return data.summary;
    const acc: Record<Verdict, number> = { supported: 0, partial: 0, unsupported: 0, unresolved: 0, no_source: 0 };
    for (const r of results) acc[r.verdict] = (acc[r.verdict] || 0) + 1;
    return acc;
  }, [data, streaming, results]);

  const run = async () => {
    setBusy(true);
    setError(null);
    setStreaming([]);
    setProgress({ done: 0, total: 0 });
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('ah_token') : null;
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`/api/presentations/${presentationId}/fact-check/stream`, {
        method: 'POST', headers,
      });
      if (!res.ok || !res.body) throw new Error(await res.text() || `HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const collected: StreamedResult[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames separated by blank line; each frame may have multiple
        // `data:` lines but we only emit one per yield, so single-line works.
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (!payload || payload === '[DONE]') continue;
          let obj: Record<string, unknown>;
          try { obj = JSON.parse(payload); } catch { continue; }

          if (obj.type === 'start') {
            setProgress({ done: 0, total: Number(obj.total) || 0 });
          } else if (obj.type === 'verdict') {
            collected.push(obj.verdict as StreamedResult);
            setStreaming([...collected]);
            setProgress(p => ({ done: collected.length, total: p.total }));
          } else if (obj.type === 'complete') {
            const next: FactCheckRecord = {
              ran_at: String(obj.ran_at),
              summary: obj.summary as FactCheckRecord['summary'],
              results: collected.slice() as FactCheckRecord['results'],
            };
            setData(next);
            // streaming will be cleared on finally
          } else if (obj.type === 'error') {
            setError(typeof obj.message === 'string' ? obj.message : 'Fact-check failed');
          }
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Fact-check failed');
    } finally {
      setBusy(false);
      // Promote streaming state into the canonical `data` view; clearing
      // `streaming` so the persisted summary takes over.
      setStreaming(null);
    }
  };

  const jumpTo = (sectionId: string | null) => {
    if (!sectionId) return;
    const el = document.getElementById(`sec-${sectionId}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Brief amber ring so the user can see which section was targeted.
      el.classList.add('ring-2', 'ring-amber-400');
      setTimeout(() => el.classList.remove('ring-2', 'ring-amber-400'), 1600);
    }
  };

  return (
    <div className="flex flex-col h-full bg-white">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4" style={{ color: brandColor }} />
          <span className="font-semibold text-sm text-gray-800">Fact Check</span>
          {data?.ran_at && !streaming && (
            <span className="text-[10px] text-gray-500">
              {new Date(data.ran_at).toLocaleString()}
            </span>
          )}
        </div>
        {onClose && (
          <button onClick={onClose} className="p-1 hover:bg-gray-200 rounded">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      <div className="p-3 border-b border-gray-200">
        <button
          onClick={run}
          disabled={busy}
          className="w-full px-3 py-2 rounded text-white text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2"
          style={{ backgroundColor: brandColor }}
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
          {busy ? 'Verifying…' : results.length ? 'Re-run fact check' : 'Run fact check'}
        </button>
        {busy && progress.total > 0 && (
          <div className="text-xs text-gray-600 mt-2">
            {progress.done} / {progress.total} citations checked
            <div className="h-1 bg-gray-200 rounded mt-1 overflow-hidden">
              <div
                className="h-1 transition-all duration-200"
                style={{
                  width: `${Math.round((progress.done / progress.total) * 100)}%`,
                  backgroundColor: brandColor,
                }}
              />
            </div>
          </div>
        )}
        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
      </div>

      {results.length > 0 && (
        <div className="p-3 border-b border-gray-200 grid grid-cols-5 gap-1 text-center text-[11px]">
          {(Object.keys(VERDICTS) as Verdict[]).map((k) => {
            const v = VERDICTS[k];
            const count = summary[k] || 0;
            return (
              <div key={k} className={`p-1.5 rounded ${v.bg} ${v.border} border`}>
                <div className={`${v.color} font-semibold text-base leading-tight`}>{count}</div>
                <div className={`${v.color} text-[9px] uppercase tracking-wide`}>{v.label}</div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!results.length && !busy && (
          <p className="p-3 text-xs text-gray-500">
            Click <strong>Run fact check</strong> to verify every <code className="text-[10px]">[source: …]</code> citation against its source document. Results stream in as each is checked.
          </p>
        )}
        {results.map((r, i) => {
          const v = VERDICTS[r.verdict];
          const Icon = v.Icon;
          return (
            <div key={i} className={`mx-3 my-2 p-2 rounded border ${v.bg} ${v.border}`}>
              <div className="flex items-start gap-2">
                <Icon className={`w-4 h-4 mt-0.5 flex-shrink-0 ${v.color}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-[11px] mb-0.5">
                    <span className={`font-semibold ${v.color}`}>{v.label}</span>
                    <span className="text-gray-500 truncate">{r.label}</span>
                    {r.section_heading && (
                      <span className="text-gray-400 truncate">· {r.section_heading}</span>
                    )}
                  </div>
                  {r.evidence_quote && (
                    <p className="text-[11px] text-gray-700 italic mt-0.5 border-l-2 border-gray-200 pl-2">
                      &ldquo;{r.evidence_quote}&rdquo;
                    </p>
                  )}
                  {r.missing && r.missing.length > 0 && (
                    <p className="text-[11px] text-amber-700 mt-1">Missing: {r.missing.join(', ')}</p>
                  )}
                  {r.conflicting && r.conflicting.length > 0 && (
                    <p className="text-[11px] text-red-700 mt-1">Conflicting: {r.conflicting.join(', ')}</p>
                  )}
                </div>
                {r.section_id && (
                  <button
                    onClick={() => jumpTo(r.section_id)}
                    className="flex-shrink-0 text-[11px] hover:underline inline-flex items-center"
                    style={{ color: brandColor }}
                    title="Scroll to this section"
                  >
                    Jump <ChevronRight className="w-3 h-3" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
