'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  Sparkles, Send, X, Wand2, Check, Loader2, ChevronDown,
  Paperclip, Brain, Image as ImageIcon, FileText,
} from 'lucide-react';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';

import type { DeckSection } from '@/lib/presentationsApi';
import type { DeckProposal } from '@/app/contexts/DeckChatContext';

type Proposal = DeckProposal;

/** Single thinking-or-text-or-tool entry recorded for a turn. We keep
 *  thinking separate from text so the UI can render it inside a disclosure. */
interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  thinking: string;          // accumulated thinking_delta content
  toolCalls: { name: string; summary?: string }[];
  proposals: Proposal[];
  attachments?: { name: string; type: 'image' | 'document' }[];
}

interface AttachmentDraft {
  id: string;
  type: 'image' | 'document';
  filename: string;
  media_type: string;
  data_base64: string;     // raw base64, no data: prefix
  size: number;
}

interface AskAIPanelProps {
  presentationId: string;
  sections: DeckSection[];
  brandColor?: string;
  onAcceptProposal: (proposal: Proposal) => void;
  onClose?: () => void;
  /** Optional initial section scope — set when opened via a per-section ✨ button. */
  initialTargetSectionId?: string | null;
}

const API_BASE = typeof window !== 'undefined' ? '' : (process.env.NEXT_PUBLIC_API_URL || '');
const PANEL_WIDTH_KEY = 'ah_askai_width';
const PANEL_WIDTH_DEFAULT = 460;
const PANEL_WIDTH_MIN = 360;
const PANEL_WIDTH_MAX = 900;
const MAX_ATTACH_SIZE = 10 * 1024 * 1024;   // 10 MB per file — Anthropic accepts up to ~30, we leave headroom

export default function AskAIPanel({
  presentationId, sections, brandColor = '#385854',
  onAcceptProposal, onClose, initialTargetSectionId = null,
}: AskAIPanelProps) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [targetSectionId, setTargetSectionId] = useState<string | null>(initialTargetSectionId);
  const [deepThinking, setDeepThinking] = useState(false);
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollEndRef = useRef<HTMLDivElement>(null);

  // Switch focus when parent updates the initial target section (per-section ✨).
  useEffect(() => {
    if (initialTargetSectionId !== undefined) {
      setTargetSectionId(initialTargetSectionId);
    }
  }, [initialTargetSectionId]);

  // Resizable width — drag the left edge wider; persisted in localStorage so
  // the next session opens at the same width.
  const [widthPx, setWidthPx] = useState<number>(() => {
    if (typeof window === 'undefined') return PANEL_WIDTH_DEFAULT;
    const stored = parseInt(localStorage.getItem(PANEL_WIDTH_KEY) || '', 10);
    return Number.isFinite(stored) && stored >= PANEL_WIDTH_MIN ? stored : PANEL_WIDTH_DEFAULT;
  });
  const [resizing, setResizing] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    localStorage.setItem(PANEL_WIDTH_KEY, String(widthPx));
  }, [widthPx]);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startW = widthPx;
    const onMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX;       // drag left → wider
      const next = Math.max(PANEL_WIDTH_MIN, Math.min(PANEL_WIDTH_MAX, startW + delta));
      setWidthPx(next);
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Autoscroll on each new turn or delta.
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [turns]);

  // ─── Attachment plumbing ─────────────────────────────────────────────────
  const addFile = async (file: File) => {
    if (file.size > MAX_ATTACH_SIZE) {
      // eslint-disable-next-line no-alert
      alert(`${file.name} is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Limit is 10 MB.`);
      return;
    }
    const type: 'image' | 'document' = file.type.startsWith('image/') ? 'image' : 'document';
    // Only accept PDFs for the document path — Anthropic doesn't accept arbitrary
    // mime types as documents, and silently dropping them produces a confusing error.
    if (type === 'document' && file.type !== 'application/pdf') {
      // eslint-disable-next-line no-alert
      alert(`${file.name}: only images and PDFs can be attached to chat.`);
      return;
    }
    const data_base64 = await new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => {
        const result = r.result as string;
        // strip "data:<mime>;base64," prefix
        const comma = result.indexOf(',');
        resolve(comma >= 0 ? result.slice(comma + 1) : result);
      };
      r.onerror = () => reject(r.error);
      r.readAsDataURL(file);
    });
    setAttachments(prev => [...prev, {
      id: `att_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      type, filename: file.name, media_type: file.type, data_base64, size: file.size,
    }]);
  };

  const handleFilePick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    for (const f of files) await addFile(f);
    e.target.value = '';   // allow re-selecting the same file
  };

  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData?.items || []);
    for (const it of items) {
      if (it.kind === 'file') {
        const f = it.getAsFile();
        if (f) await addFile(f);
      }
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer?.files || []);
    for (const f of files) await addFile(f);
  };

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  };

  // ─── Send ────────────────────────────────────────────────────────────────
  const send = async () => {
    const message = input.trim();
    if ((!message && attachments.length === 0) || busy) return;
    setInput('');
    setBusy(true);

    const userTurn: ChatTurn = {
      role: 'user', text: message, thinking: '', toolCalls: [], proposals: [],
      attachments: attachments.length
        ? attachments.map(a => ({ name: a.filename, type: a.type }))
        : undefined,
    };
    const aiTurn: ChatTurn = { role: 'assistant', text: '', thinking: '', toolCalls: [], proposals: [] };
    setTurns(t => [...t, userTurn, aiTurn]);

    // Snapshot the attachments for the request; clear the input draft now so
    // a follow-up turn doesn't accidentally resend them.
    const requestAttachments = attachments;
    setAttachments([]);

    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('ah_token') : null;
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      // History only carries text — attachments are sent as content blocks on
      // the current turn. Previous attachments are described in the assistant's
      // reply, so we don't need to resend them.
      const history = turns.map(t => ({ role: t.role, content: t.text }));
      const resp = await fetch(`${API_BASE}/api/presentations/${presentationId}/ai-chat`, {
        method: 'POST', headers,
        body: JSON.stringify({
          message,
          history,
          target_section_id: targetSectionId,
          deep_thinking: deepThinking,
          attachments: requestAttachments.map(a => ({
            type: a.type, media_type: a.media_type, data_base64: a.data_base64,
            filename: a.filename,
          })),
        }),
      });
      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No response stream');
      const decoder = new TextDecoder();
      let pending = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = pending.indexOf('\n\n')) !== -1) {
          const frame = pending.slice(0, sep);
          pending = pending.slice(sep + 2);
          if (!frame.trim() || frame.startsWith(':')) continue;
          let dataLine = '';
          for (const line of frame.split('\n')) {
            if (line.startsWith('data: ')) dataLine += line.slice(6);
            else if (line.startsWith('data:')) dataLine += line.slice(5);
          }
          if (!dataLine) continue;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          let d: any;
          try { d = JSON.parse(dataLine); } catch { continue; }

          setTurns(prev => prev.map((t, i) => {
            if (i !== prev.length - 1) return t;
            switch (d.type) {
              case 'delta': return { ...t, text: t.text + (d.content || '') };
              case 'thinking': return { ...t, thinking: t.thinking + (d.content || '') };
              case 'tool_use': return { ...t, toolCalls: [...t.toolCalls, { name: d.name }] };
              case 'tool_result':
                return {
                  ...t,
                  toolCalls: t.toolCalls.map((tc, idx) =>
                    idx === t.toolCalls.length - 1 && tc.name === d.name
                      ? { ...tc, summary: d.summary }
                      : tc
                  ),
                };
              case 'proposal':
                return { ...t, proposals: [...t.proposals, d.input as Proposal] };
              default: return t;
            }
          }));
        }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setTurns(prev => prev.map((t, i) => i === prev.length - 1 ? { ...t, text: `Error: ${msg}` } : t));
    } finally {
      setBusy(false);
    }
  };

  const targetSection = sections.find(s => s.id === targetSectionId) || null;

  return (
    <div
      className={`flex flex-col h-full bg-white border-l border-gray-200 relative ${resizing ? 'select-none' : ''}`}
      style={{ width: widthPx }}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* Left-edge resize handle */}
      <div
        onMouseDown={startResize}
        className="absolute top-0 bottom-0 left-0 w-1.5 cursor-col-resize hover:bg-gray-300/60 z-10"
        title="Drag to resize"
      />

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 text-white" style={{ backgroundColor: brandColor }}>
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4" />
          <span className="font-semibold text-sm">AI Assistant</span>
        </div>
        {onClose && (
          <button onClick={onClose} className="p-1 hover:bg-white/20 rounded" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Scope + options strip */}
      <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-gray-200 bg-gray-50 text-xs flex-wrap">
        <label className="text-gray-600 font-medium">Scope:</label>
        <div className="relative flex-1 min-w-[140px]">
          <select
            value={targetSectionId || ''}
            onChange={(e) => setTargetSectionId(e.target.value || null)}
            className="w-full appearance-none border border-gray-300 rounded pl-2 pr-7 py-1 bg-white"
            title="Restrict the model to a specific section"
          >
            <option value="">Whole deck</option>
            {sections.map(s => (
              <option key={s.id} value={s.id}>
                {s.title?.slice(0, 40) || s.kind} {s.title && s.title.length > 40 ? '…' : ''}
              </option>
            ))}
          </select>
          <ChevronDown className="w-3 h-3 text-gray-500 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
        </div>
        <button
          type="button"
          onClick={() => setDeepThinking(v => !v)}
          className={`flex items-center gap-1 px-2 py-1 rounded border ${
            deepThinking ? 'bg-purple-50 border-purple-300 text-purple-700' : 'border-gray-300 text-gray-600 hover:bg-gray-100'
          }`}
          title="Use Opus 4.7 with adaptive extended thinking"
        >
          <Brain className="w-3 h-3" /> Think
        </button>
      </div>

      {targetSection && (
        <div className="px-3 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-800">
          Focused on <span className="font-semibold">{targetSection.title || targetSection.id}</span> — proposals will target this section.
        </div>
      )}

      {/* Drop-zone overlay when a file is dragged over the panel */}
      {dragOver && (
        <div className="absolute inset-0 bg-blue-50/80 border-2 border-dashed border-blue-400 z-20 flex items-center justify-center pointer-events-none">
          <span className="text-sm text-blue-700 font-medium">Drop image or PDF to attach</span>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {turns.length === 0 && (
          <div className="text-xs text-gray-500 leading-relaxed">
            Ask me to draft a section, rewrite for tone, or pull figures from the document corpus.
            <br /><br />
            Try: <em className="text-gray-700">&ldquo;Write an executive summary of FY24 borough finances&rdquo;</em>
            &nbsp;or paste an image and ask to summarize it.
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === 'user' ? 'flex justify-end' : ''}>
            <div
              className={`max-w-[92%] rounded-lg px-3 py-2 text-sm ${
                t.role === 'user' ? 'text-white' : 'bg-gray-50 border border-gray-200 text-gray-800'
              }`}
              style={t.role === 'user' ? { backgroundColor: brandColor } : {}}
            >
              {/* User-turn attachment chips */}
              {t.role === 'user' && t.attachments && t.attachments.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-1">
                  {t.attachments.map((a, ai) => (
                    <span key={ai} className="inline-flex items-center gap-1 text-[10px] bg-white/20 rounded px-1.5 py-0.5">
                      {a.type === 'image' ? <ImageIcon className="w-3 h-3" /> : <FileText className="w-3 h-3" />}
                      {a.name}
                    </span>
                  ))}
                </div>
              )}

              {/* Tool activity */}
              {t.role === 'assistant' && t.toolCalls.length > 0 && (
                <div className="mb-2 space-y-0.5">
                  {t.toolCalls.map((tc, idx) => (
                    <div key={idx} className="text-[11px] text-gray-500 flex items-center gap-1">
                      <Wand2 className="w-3 h-3" />
                      <span>{tc.name}</span>
                      {tc.summary && <span className="text-gray-400">— {tc.summary}</span>}
                    </div>
                  ))}
                </div>
              )}

              {/* Collapsible thinking — only render the <details> if any thinking
                  content actually streamed; the disclosure label shows the first
                  ~80 chars so the user sees what's behind it without clicking. */}
              {t.role === 'assistant' && t.thinking && (
                <details className="mb-2 text-[11px] text-gray-500">
                  <summary className="cursor-pointer flex items-center gap-1 select-none hover:text-gray-700">
                    <Brain className="w-3 h-3" />
                    <span>Show reasoning ({t.thinking.length.toLocaleString()} chars)</span>
                  </summary>
                  <pre className="mt-1 whitespace-pre-wrap bg-purple-50/50 border border-purple-100 rounded p-2 text-purple-900">
                    {t.thinking}
                  </pre>
                </details>
              )}

              {t.role === 'assistant' ? (
                t.text
                  ? <MarkdownWithCharts content={t.text} brandColor={brandColor} />
                  : t.thinking
                    ? <span className="text-xs text-gray-400 italic">thinking…</span>
                    : <span className="text-xs text-gray-400 italic">working…</span>
              ) : (
                <span className="whitespace-pre-wrap">{t.text}</span>
              )}

              {t.role === 'assistant' && t.proposals.length > 0 && (
                <div className="mt-3 space-y-2">
                  {t.proposals.map((p, pi) => (
                    <ProposalCard key={pi} proposal={p} onAccept={() => onAcceptProposal(p)} brandColor={brandColor} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={scrollEndRef} />
      </div>

      {/* Attachment chips above the input */}
      {attachments.length > 0 && (
        <div className="px-2 pt-2 flex flex-wrap gap-1">
          {attachments.map(a => (
            <span key={a.id} className="inline-flex items-center gap-1 text-[11px] bg-blue-50 border border-blue-200 text-blue-800 rounded pl-2 pr-1 py-0.5">
              {a.type === 'image' ? <ImageIcon className="w-3 h-3" /> : <FileText className="w-3 h-3" />}
              <span className="max-w-[160px] truncate" title={a.filename}>{a.filename}</span>
              <span className="text-blue-500">({(a.size / 1024).toFixed(0)}k)</span>
              <button onClick={() => removeAttachment(a.id)} className="hover:bg-blue-100 rounded" title="Remove">
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="p-2 border-t border-gray-200 flex gap-1.5 items-end">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,application/pdf"
          className="hidden"
          onChange={handleFilePick}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="p-2 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50"
          title="Attach image or PDF (or paste / drag-drop into the panel)"
          disabled={busy}
        >
          <Paperclip className="w-4 h-4" />
        </button>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          onPaste={handlePaste}
          placeholder={
            targetSection
              ? `Ask about "${(targetSection.title || targetSection.id).slice(0, 30)}"…`
              : 'Ask the AI…'
          }
          rows={1}
          className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:border-gray-400 resize-none"
          disabled={busy}
        />
        <button
          onClick={send}
          disabled={(!input.trim() && attachments.length === 0) || busy}
          className="px-3 py-2 rounded-lg text-white disabled:opacity-50"
          style={{ backgroundColor: brandColor }}
          title={deepThinking ? 'Send (Opus 4.7 + extended thinking)' : 'Send'}
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

function ProposalCard({ proposal, onAccept, brandColor }: { proposal: Proposal; onAccept: () => void; brandColor: string }) {
  const { label, headline, body, meta } = describeProposal(proposal);
  return (
    <div className="border border-amber-300 bg-amber-50 rounded-lg p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">
          AI proposal — {label}
        </span>
        <button
          onClick={onAccept}
          className="text-xs px-2 py-0.5 rounded text-white flex items-center gap-1"
          style={{ backgroundColor: brandColor }}
        >
          <Check className="w-3 h-3" /> Apply
        </button>
      </div>
      {headline && <p className="text-xs font-medium text-gray-800">{headline}</p>}
      {proposal.rationale && <p className="text-[11px] text-gray-600 italic mt-1">{proposal.rationale}</p>}
      {body && (
        <pre className={`text-[11px] text-gray-700 whitespace-pre-wrap mt-1 max-h-32 overflow-y-auto${body.kind === 'code' ? ' font-mono' : ''}`}>
          {body.text.slice(0, 600)}{body.text.length > 600 ? '…' : ''}
        </pre>
      )}
      {meta && <div className="text-[11px] text-gray-700 mt-1">{meta}</div>}
    </div>
  );
}

/** Map a proposal to display fields. Same logic as before. */
function describeProposal(p: Proposal): {
  label: string;
  headline?: string;
  body?: { kind: 'prose' | 'code'; text: string };
  meta?: string;
} {
  switch (p.proposal_type) {
    case 'section_edit':
      return {
        label: 'edit section',
        headline: p.section_id ? `Rewrite section ${p.section_id}` : 'Rewrite section',
        body: p.new_markdown ? { kind: 'prose', text: p.new_markdown } : undefined,
      };
    case 'new_section':
      return {
        label: 'new section',
        headline: p.heading,
        body: p.markdown ? { kind: 'prose', text: p.markdown } : undefined,
      };
    case 'inline_chart':
      return {
        label: `inline ${p.chart_type || 'bar'} chart`,
        headline: p.title || 'Chart',
        meta: p.headers ? `${p.headers.length} cols × ${(p.rows || []).length} rows` : undefined,
      };
    case 'diagram':
      return {
        label: 'mermaid diagram',
        headline: p.section_id ? `Add diagram to ${p.section_id}` : 'Add diagram',
        body: p.source ? { kind: 'code', text: p.source } : undefined,
      };
    case 'react_component':
      return {
        label: p.section_id ? 'replace component' : 'new component',
        headline: p.name || p.title,
        body: p.tsx ? { kind: 'code', text: p.tsx } : undefined,
      };
    case 'section_data_edit':
      return {
        label: 'replace component data',
        headline: p.section_id ? `Update data on ${p.section_id}` : 'Update data',
        body: p.new_data
          ? { kind: 'code', text: safeStringify(p.new_data) }
          : undefined,
      };
    case 'section_data_patch':
      return {
        label: 'patch component data',
        headline: p.section_id ? `Patch data on ${p.section_id}` : 'Patch data',
        meta: summarizePatchOps(p),
      };
    default:
      return {
        label: `${p.kind || 'section'} ${p.section_id ? '(rewrite)' : '(new)'}`,
        headline: p.title,
        body: p.body ? { kind: 'prose', text: p.body }
            : p.tsx ? { kind: 'code', text: p.tsx }
            : undefined,
        meta: p.kind === 'table' && p.headers
          ? `${p.headers.length} cols × ${(p.rows || []).length} rows`
          : undefined,
      };
  }
}

function safeStringify(v: unknown): string {
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function summarizePatchOps(p: Proposal): string {
  const parts: string[] = [];
  const ap = (p.array_patches || []).reduce((n, x) => n + (x.items?.length || 0), 0);
  if (ap) parts.push(`${ap} row patch${ap === 1 ? '' : 'es'}`);
  if (p.scalar_set && Object.keys(p.scalar_set).length) parts.push(`${Object.keys(p.scalar_set).length} field set`);
  if (p.scalar_unset?.length) parts.push(`${p.scalar_unset.length} field unset`);
  const apx = (p.appends || []).reduce((n, x) => n + (x.items?.length || 0), 0);
  if (apx) parts.push(`${apx} append${apx === 1 ? '' : 'es'}`);
  const rmx = (p.removes || []).reduce((n, x) => n + (x.keys?.length || 0), 0);
  if (rmx) parts.push(`${rmx} remove${rmx === 1 ? '' : 'es'}`);
  return parts.join(', ') || 'no-op';
}
