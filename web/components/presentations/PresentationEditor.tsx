'use client';

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowLeft, Save, Globe, Download, Copy, CopyPlus, Check, CheckCircle2,
  Plus, Trash2, Wand2, Loader2, ChevronUp, ChevronDown, ChevronsUpDown,
  Eye, X, Lock, Unlock, ShieldCheck, Upload, FileStack, Paperclip,
  AlignLeft, Table as TableIcon, Sparkles, ExternalLink, RefreshCw,
  History, Search,
} from 'lucide-react';
import {
  type Presentation, type DeckSection, type DeckAttachment, type SectionKind,
  getPresentation, updatePresentation, addAttachment, removeAttachment,
  publishPresentation, unpublishPresentation, setPublicPassword,
  changesSincePublish,
} from '@/lib/presentationsApi';
import { uploadDocument } from '@/lib/api';
import { useDeckChat, type DeckProposal } from '@/app/contexts/DeckChatContext';
import NarrativeBlock from './NarrativeBlock';
import TableBlock from './TableBlock';
import AttachmentBlock from './AttachmentBlock';
import ReactComponentBlock from './ReactComponentBlock';
import FactCheckPanel from './FactCheckPanel';
import PresentationViewer from './PresentationViewer';
import SourceChip from './SourceChip';
import CitationPreview from './CitationPreview';
import WebReferencePreview from './WebReferencePreview';
import VersionsPanel from './VersionsPanel';
import CitationAuditPanel from './CitationAuditPanel';

interface Props {
  presentationId: string;
  initialPreviewing?: boolean;
}

const brandColor = '#385854';

/** Mint a new section id locally — server's `_ensure_section_ids` accepts
 *  any non-empty string so we can add sections without a roundtrip. */
function newSectionId(): string {
  return 'sec_' + Math.random().toString(36).slice(2, 12);
}

/**
 * Apply a section_data_patch proposal to a react_component section's
 * `data` payload. Ops apply in order:
 *   array_patches → scalar_set → scalar_unset → appends → removes
 * The function is total — missing/empty ops are no-ops. Pure (no in-place
 * mutation) so React state updates work as expected.
 */
function applyDataPatches(
  current: unknown,
  patch: DeckProposal,
): Record<string, unknown> {
  const base: Record<string, unknown> = current && typeof current === 'object' && !Array.isArray(current)
    ? { ...(current as Record<string, unknown>) }
    : {};

  // array_patches: shallow-merge `set` and delete `unset` keys on each matched element.
  for (const ap of patch.array_patches || []) {
    const arr = Array.isArray(base[ap.path]) ? (base[ap.path] as unknown[]).slice() : [];
    for (const it of ap.items || []) {
      const idx = arr.findIndex(el =>
        el && typeof el === 'object' && (el as Record<string, unknown>)[ap.key_field] === it.key,
      );
      if (idx < 0) continue;
      const cur = arr[idx] as Record<string, unknown>;
      const next = { ...cur, ...(it.set || {}) };
      for (const u of it.unset || []) delete next[u];
      arr[idx] = next;
    }
    base[ap.path] = arr;
  }

  // scalar_set: top-level field assignments
  if (patch.scalar_set) {
    for (const [k, v] of Object.entries(patch.scalar_set)) base[k] = v;
  }

  // scalar_unset: top-level field deletions
  for (const k of patch.scalar_unset || []) delete base[k];

  // appends: push to arrays
  for (const ap of patch.appends || []) {
    const arr = Array.isArray(base[ap.path]) ? (base[ap.path] as unknown[]).slice() : [];
    arr.push(...ap.items);
    base[ap.path] = arr;
  }

  // removes: drop array elements matching keys
  for (const rm of patch.removes || []) {
    const arr = Array.isArray(base[rm.path]) ? (base[rm.path] as unknown[]) : [];
    base[rm.path] = arr.filter(el => {
      if (!el || typeof el !== 'object') return true;
      const v = (el as Record<string, unknown>)[rm.key_field];
      return !(rm.keys || []).includes(v as never);
    });
  }

  return base;
}

/**
 * PresentationEditor — full-featured deck editor mirroring the
 * bank-processor chrome:
 *
 *   - Local-first state with 1.5s debounced autosave (PUT /{id})
 *   - Cmd/Ctrl-Z undo, Cmd/Ctrl-Shift-Z redo (50-deep, 500ms debounced)
 *   - HTML5 drag-and-drop section reorder
 *   - Insert-between "+" button + per-section Up/Down/Duplicate/Copy/Delete
 *   - AutoGrow titles + section headings
 *   - Full-screen Preview overlay (renders the public PresentationViewer)
 *   - Attachments accordion + file/folder upload
 *   - ExportMenu popover (PPTX/DOCX, ZIP bundles)
 *   - PasswordButton popover for public-deck password
 *   - SourceChip above each section heading (source_label/url/filename)
 *   - Collapse/expand-all sections
 *   - Per-section AI-edit menu (rewrite, expand, polish, plain English, summarize)
 *
 * Sticky CitationPreview + WebReferencePreview side panels mount at the
 * bottom so [source: filename] clicks (in narrative bodies) open the doc
 * preview without leaving the editor.
 */
export default function PresentationEditor({ presentationId, initialPreviewing = false }: Props) {
  const router = useRouter();
  const [deck, setDeck] = useState<Presentation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [previewing, setPreviewing] = useState(initialPreviewing);
  const [showFactCheck, setShowFactCheck] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [showAttachments, setShowAttachments] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  // Total structural changes between the draft and the currently-public
  // version. >0 → button switches from "Published" to "Republish (N)".
  // Null means "not yet checked" (button falls back to a neutral label).
  const [pendingChanges, setPendingChanges] = useState<number | null>(null);
  const [collapsedSet, setCollapsedSet] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const deckChat = useDeckChat();

  // ─── Initial load ───────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await getPresentation(presentationId);
        if (!cancelled) setDeck(p);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      }
    })();
    return () => { cancelled = true; };
  }, [presentationId]);

  // ─── Undo / redo ────────────────────────────────────────────────────────
  const historyRef = useRef<Presentation[]>([]);
  const redoRef = useRef<Presentation[]>([]);
  const lastDeckRef = useRef<Presentation | null>(null);
  const lastPushTimeRef = useRef(0);
  const isReplayingRef = useRef(false);
  const HISTORY_LIMIT = 50;

  // Reset history when switching to a different deck.
  useEffect(() => {
    historyRef.current = [];
    redoRef.current = [];
    lastDeckRef.current = deck;
    lastPushTimeRef.current = 0;
  }, [deck?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Push the previous state onto history on every deck mutation. Leading-edge
  // debounce at 500ms so a typing burst becomes one undo entry.
  useEffect(() => {
    if (!deck) return;
    if (isReplayingRef.current) {
      isReplayingRef.current = false;
      lastDeckRef.current = deck;
      return;
    }
    const now = Date.now();
    if (lastDeckRef.current && (lastPushTimeRef.current === 0 || now - lastPushTimeRef.current > 500)) {
      historyRef.current.push(lastDeckRef.current);
      if (historyRef.current.length > HISTORY_LIMIT) historyRef.current.shift();
      lastPushTimeRef.current = now;
      redoRef.current = [];
    }
    lastDeckRef.current = deck;
  }, [deck]);

  const undo = useCallback(() => {
    if (historyRef.current.length === 0 || !deck) return;
    const prev = historyRef.current.pop()!;
    redoRef.current.push(deck);
    if (redoRef.current.length > HISTORY_LIMIT) redoRef.current.shift();
    isReplayingRef.current = true;
    setDeck(prev);
    setDirty(true);
  }, [deck]);

  const redo = useCallback(() => {
    if (redoRef.current.length === 0 || !deck) return;
    const next = redoRef.current.pop()!;
    historyRef.current.push(deck);
    if (historyRef.current.length > HISTORY_LIMIT) historyRef.current.shift();
    isReplayingRef.current = true;
    setDeck(next);
    setDirty(true);
  }, [deck]);

  // Cmd/Ctrl-Z = undo, Cmd/Ctrl-Shift-Z = redo. Always intercept; native
  // textarea undo doesn't know about React state.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key.toLowerCase() !== 'z') return;
      // Don't intercept while typing in a tiptap editor — Tiptap has its
      // own granular undo and our deck-level undo only sees the saved
      // body, not the in-progress edit.
      const target = e.target as HTMLElement | null;
      if (target?.closest('.ProseMirror')) return;
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [undo, redo]);

  // ─── Autosave ───────────────────────────────────────────────────────────
  const saveNow = useCallback(async () => {
    if (!deck || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await updatePresentation(deck.id, {
        title: deck.title,
        sections: deck.sections,
        attachments: deck.attachments,
      });
      setDeck(prev => prev ? {
        // Preserve any local fields that might have changed during the save
        // round-trip; the server response has the canonical sections/attachments.
        ...prev,
        title: updated.title,
        sections: updated.sections,
        attachments: updated.attachments,
        status: updated.status,
        public_slug: updated.public_slug,
        published_at: updated.published_at,
        has_password: updated.has_password,
      } : updated);
      setDirty(false);
      setLastSavedAt(new Date());
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }, [deck, saving]);

  useEffect(() => {
    if (!dirty || saving) return;
    const t = setTimeout(() => { saveNow(); }, 1500);
    return () => clearTimeout(t);
  }, [dirty, saving, saveNow]);

  // Recompute "unpublished changes" badge whenever the draft is freshly
  // saved or the publish state flips. Skipped if never published — the
  // button just reads "Publish" until a v1 exists.
  useEffect(() => {
    if (!deck?.id) return;
    if (!deck.public_slug) { setPendingChanges(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await changesSincePublish(deck.id);
        if (!cancelled) setPendingChanges(r.ever_published ? r.total_changes : null);
      } catch {
        if (!cancelled) setPendingChanges(null);
      }
    })();
    return () => { cancelled = true; };
  }, [deck?.id, deck?.public_slug, lastSavedAt]);

  // Save on unmount / page hide so a fast Cmd-W doesn't lose work.
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  // ─── Section ops (all local — flushed by autosave) ──────────────────────
  const updateSection = useCallback((sectionId: string, patch: Partial<DeckSection>) => {
    setDeck(prev => prev ? {
      ...prev,
      sections: prev.sections.map(s => s.id === sectionId ? { ...s, ...patch } : s),
    } : prev);
    setDirty(true);
  }, []);

  const addSectionAt = useCallback((kind: SectionKind, insertAt?: number) => {
    setDeck(prev => {
      if (!prev) return prev;
      const titles: Record<SectionKind, string> = {
        narrative: 'New section', table: 'New table',
        attachment: 'Attachment', react_component: 'Custom component',
      };
      const blank: DeckSection = { id: newSectionId(), kind, title: titles[kind] };
      if (typeof insertAt === 'number') {
        const clamped = Math.max(0, Math.min(insertAt, prev.sections.length));
        return { ...prev, sections: [...prev.sections.slice(0, clamped), blank, ...prev.sections.slice(clamped)] };
      }
      return { ...prev, sections: [...prev.sections, blank] };
    });
    setDirty(true);
  }, []);

  const removeSectionLocal = useCallback((sectionId: string) => {
    setDeck(prev => prev ? { ...prev, sections: prev.sections.filter(s => s.id !== sectionId) } : prev);
    setDirty(true);
  }, []);

  const moveSectionLocal = useCallback((sectionId: string, delta: -1 | 1) => {
    setDeck(prev => {
      if (!prev) return prev;
      const i = prev.sections.findIndex(s => s.id === sectionId);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.sections.length) return prev;
      const next = prev.sections.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return { ...prev, sections: next };
    });
    setDirty(true);
  }, []);

  const duplicateSection = useCallback((sectionId: string) => {
    setDeck(prev => {
      if (!prev) return prev;
      const idx = prev.sections.findIndex(s => s.id === sectionId);
      if (idx < 0) return prev;
      const orig = prev.sections[idx];
      const cloned: DeckSection = {
        ...JSON.parse(JSON.stringify(orig)),
        id: newSectionId(),
        title: orig.title ? `${orig.title} (copy)` : 'Copy of section',
      };
      return { ...prev, sections: [...prev.sections.slice(0, idx + 1), cloned, ...prev.sections.slice(idx + 1)] };
    });
    setDirty(true);
  }, []);

  const sectionToMarkdown = (section: DeckSection): string => {
    const out: string[] = [];
    if (section.title) out.push(`# ${section.title}`);
    out.push('');
    if (section.kind === 'narrative') {
      out.push((section.body || '').trim());
    } else if (section.kind === 'table') {
      const headers = section.headers || [];
      const rows = section.rows || [];
      if (section.caption) { out.push(`_${section.caption}_`); out.push(''); }
      if (headers.length) {
        out.push('| ' + headers.join(' | ') + ' |');
        out.push('| ' + headers.map(() => '---').join(' | ') + ' |');
      }
      for (const row of rows) {
        out.push('| ' + row.map(v => String(v ?? '')).join(' | ') + ' |');
      }
    } else if (section.kind === 'attachment') {
      out.push(`_(Attachment: ${section.attachment_id || 'unknown'})_`);
      if (section.caption) out.push(section.caption);
    } else if (section.kind === 'react_component') {
      out.push('_(React component)_');
      if (section.data !== undefined && section.data !== null) {
        out.push(''); out.push('```json');
        try { out.push(JSON.stringify(section.data, null, 2)); } catch { out.push(String(section.data)); }
        out.push('```');
      }
    }
    return out.join('\n').trim() + '\n';
  };

  const copySection = useCallback(async (section: DeckSection) => {
    try {
      await navigator.clipboard.writeText(sectionToMarkdown(section));
      setCopiedId(section.id);
      setTimeout(() => setCopiedId(prev => prev === section.id ? null : prev), 1500);
    } catch (e) {
      alert(`Copy failed: ${(e as Error).message}`);
    }
  }, []);

  // ─── Drag-and-drop reorder ──────────────────────────────────────────────
  const [dragSourceId, setDragSourceId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);

  const reorderSections = useCallback((fromId: string, toId: string) => {
    if (fromId === toId) return;
    setDeck(prev => {
      if (!prev) return prev;
      const from = prev.sections.findIndex(s => s.id === fromId);
      const to = prev.sections.findIndex(s => s.id === toId);
      if (from < 0 || to < 0) return prev;
      const next = prev.sections.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return { ...prev, sections: next };
    });
    setDirty(true);
  }, []);

  // ─── Collapse / expand ──────────────────────────────────────────────────
  const toggleCollapsed = (sectionId: string) => {
    setCollapsedSet(prev => {
      const next = new Set(prev);
      if (next.has(sectionId)) next.delete(sectionId); else next.add(sectionId);
      return next;
    });
  };
  const collapseAll = () => deck && setCollapsedSet(new Set(deck.sections.map(s => s.id)));
  const expandAll = () => setCollapsedSet(new Set());

  // ─── Title edit ─────────────────────────────────────────────────────────
  const setTitle = (next: string) => {
    setDeck(prev => prev ? { ...prev, title: next } : prev);
    setDirty(true);
  };

  // ─── Publish / unpublish / password ─────────────────────────────────────
  const togglePublish = async () => {
    if (!deck) return;
    // Flush pending edits before publishing so the published copy is current.
    if (dirty) await saveNow();
    const updated = deck.public_slug ? await unpublishPresentation(deck.id) : await publishPresentation(deck.id);
    setDeck(updated);
    // A fresh publish/republish zeros the diff; an unpublish clears it.
    setPendingChanges(updated.public_slug ? 0 : null);
  };

  /** Drives the publish button label + style. Three states:
   *  - "Publish"           → never published, neutral brand color
   *  - "Republish (N)"     → published but draft has diverged, amber
   *  - "Published"         → published and draft matches, emerald */
  const publishState: 'unpublished' | 'republish' | 'published' = !deck?.public_slug
    ? 'unpublished'
    : (pendingChanges && pendingChanges > 0 ? 'republish' : 'published');

  const handleSetPassword = async (pw: string) => {
    if (!deck) return null;
    const updated = await setPublicPassword(deck.id, pw);
    setDeck(updated);
    return updated.has_password;
  };

  const publicUrl = deck?.public_slug
    ? `${typeof window !== 'undefined' ? window.location.origin : ''}/p/${deck.public_slug}`
    : null;

  // ─── Export ─────────────────────────────────────────────────────────────
  const handleExport = async (format: 'pptx' | 'docx') => {
    if (!deck) return;
    if (dirty) await saveNow();
    setExporting(format);
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('ah_token') : null;
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`/api/presentations/${deck.id}/export?format=${format}`, { headers });
      if (!res.ok) throw new Error(`Export failed: ${res.status}`);
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="([^"]+)"/);
      const filename = m ? m[1] : `${deck.title || 'presentation'}.${format}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      alert(`Export failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExporting(null);
    }
  };

  // ─── AI-edit (per-section) ──────────────────────────────────────────────
  const handleAIEdit = async (sectionId: string, action: string) => {
    if (!deck) return;
    if (dirty) await saveNow();
    const token = typeof window !== 'undefined' ? localStorage.getItem('ah_token') : null;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`/api/presentations/${deck.id}/ai-edit`, {
      method: 'POST', headers,
      body: JSON.stringify({ section_id: sectionId, action }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`AI edit failed: ${err.detail || res.statusText}`);
      return;
    }
    const updated = await res.json();
    setDeck(updated);
  };

  // ─── Attachments — file/folder upload ───────────────────────────────────
  const uploadFiles = async (files: FileList | File[]) => {
    if (!deck) return;
    const list = Array.from(files);
    if (!list.length) return;
    setUploadProgress({ done: 0, total: list.length });
    setUploadError(null);
    let cur = deck;
    for (let i = 0; i < list.length; i += 1) {
      const f = list[i];
      try {
        const doc = await uploadDocument(f, 'default', { category: 'deck-attachment' });
        const updated = await addAttachment(cur.id, doc.id);
        cur = updated;
        setDeck(updated);
      } catch (e: unknown) {
        setUploadError(`Failed: ${f.name} — ${e instanceof Error ? e.message : String(e)}`);
      }
      setUploadProgress({ done: i + 1, total: list.length });
    }
    setUploadProgress(null);
  };

  const handleRemoveAttachment = async (attId: string) => {
    if (!deck) return;
    if (!confirm('Remove this attachment from the deck?')) return;
    const updated = await removeAttachment(deck.id, attId);
    setDeck(updated);
  };

  // ─── DeckChat binding so the chat panel can propose into this deck ─────
  // Each propose_* tool from deck_chat_service emits a `proposal` SSE event
  // with a `proposal_type` discriminator. Dispatch here. Legacy
  // `propose_section` calls (no proposal_type) fall through to the
  // create-or-replace path keyed on `kind` + `section_id`.
  const handleAcceptProposal = useCallback(async (proposal: DeckProposal) => {
    if (!deck) return false;

    const insertAfterId = (id: string, blank: DeckSection) => {
      setDeck(prev => {
        if (!prev) return prev;
        const idx = prev.sections.findIndex(s => s.id === id);
        if (idx < 0) return { ...prev, sections: [...prev.sections, blank] };
        return {
          ...prev,
          sections: [...prev.sections.slice(0, idx + 1), blank, ...prev.sections.slice(idx + 1)],
        };
      });
      setDirty(true);
    };

    const appendToSectionBody = (sectionId: string, suffix: string) => {
      setDeck(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          sections: prev.sections.map(s => {
            if (s.id !== sectionId) return s;
            const cur = s.body || '';
            const sep = cur && !cur.endsWith('\n\n') ? '\n\n' : '';
            return { ...s, body: cur + sep + suffix };
          }),
        };
      });
      setDirty(true);
    };

    switch (proposal.proposal_type) {
      case 'section_edit': {
        if (!proposal.section_id || proposal.new_markdown == null) return false;
        updateSection(proposal.section_id, { body: proposal.new_markdown });
        return true;
      }
      case 'new_section': {
        const id = newSectionId();
        const blank: DeckSection = {
          id, kind: 'narrative',
          title: proposal.heading || 'New section',
          body: proposal.markdown || '',
        };
        if (proposal.after_section_id) insertAfterId(proposal.after_section_id, blank);
        else {
          setDeck(prev => prev ? { ...prev, sections: [...prev.sections, blank] } : prev);
          setDirty(true);
        }
        return true;
      }
      case 'inline_chart': {
        if (!proposal.section_id || !proposal.headers || !proposal.rows) return false;
        const spec: Record<string, unknown> = {
          type: proposal.chart_type || 'bar',
          headers: proposal.headers,
          rows: proposal.rows,
        };
        if (proposal.title) spec.title = proposal.title;
        if (proposal.x) spec.x = proposal.x;
        if (proposal.y) spec.y = proposal.y;
        appendToSectionBody(proposal.section_id, '```chart\n' + JSON.stringify(spec, null, 2) + '\n```');
        return true;
      }
      case 'diagram': {
        if (!proposal.section_id || !proposal.source) return false;
        appendToSectionBody(proposal.section_id, '```mermaid\n' + proposal.source + '\n```');
        return true;
      }
      case 'react_component': {
        const fields: Partial<DeckSection> = {
          tsx: proposal.tsx,
          data: proposal.data,
        };
        if (proposal.section_id) {
          updateSection(proposal.section_id, {
            kind: 'react_component',
            title: proposal.name || proposal.title,
            ...fields,
          });
        } else {
          const id = newSectionId();
          setDeck(prev => prev ? {
            ...prev,
            sections: [...prev.sections, {
              id, kind: 'react_component',
              title: proposal.name || proposal.title || 'Component',
              ...fields,
            }],
          } : prev);
          setDirty(true);
        }
        return true;
      }
      case 'section_data_edit': {
        if (!proposal.section_id) return false;
        updateSection(proposal.section_id, { data: proposal.new_data });
        return true;
      }
      case 'section_data_patch': {
        if (!proposal.section_id) return false;
        // Apply patches to the CURRENT live data so two patches compose
        // instead of the second clobbering the first.
        setDeck(prev => {
          if (!prev) return prev;
          return {
            ...prev,
            sections: prev.sections.map(s => {
              if (s.id !== proposal.section_id) return s;
              const next = applyDataPatches(s.data, proposal);
              return { ...s, data: next };
            }),
          };
        });
        setDirty(true);
        return true;
      }
      default: {
        // Legacy propose_section path — kind + section_id + body/headers/rows/tsx
        const fields: Partial<DeckSection> = {
          body: proposal.body,
          headers: proposal.headers,
          rows: proposal.rows,
          caption: proposal.caption,
          tsx: proposal.tsx,
          data: proposal.data,
        };
        if (proposal.section_id) {
          updateSection(proposal.section_id, { kind: proposal.kind as SectionKind, title: proposal.title, ...fields });
        } else {
          const id = newSectionId();
          setDeck(prev => prev ? {
            ...prev,
            sections: [...prev.sections, { id, kind: (proposal.kind || 'narrative') as SectionKind, title: proposal.title || '', ...fields }],
          } : prev);
          setDirty(true);
        }
        return true;
      }
    }
  }, [deck, updateSection]);

  useEffect(() => {
    if (!deck) return;
    const summary = [
      `Title: ${deck.title}`,
      `Sections: ${(deck.sections || []).length}`,
      ...(deck.sections || []).slice(0, 12).map((s, i) =>
        `  ${i + 1}. [${s.id}] ${s.kind}: ${s.title || '(untitled)'}`),
    ].join('\n');
    deckChat.bindActiveDeck({
      id: deck.id,
      title: deck.title,
      summary,
      sections: (deck.sections || []).map(s => ({
        id: s.id,
        title: s.title || '',
        kind: s.kind,
      })),
      applyProposal: handleAcceptProposal,
    });
    return () => deckChat.bindActiveDeck(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deck?.id, deck?.title, deck?.sections, handleAcceptProposal]);

  // ─── Render ─────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="p-6 text-red-600">
        <p>Error: {error}</p>
        <button onClick={() => router.push('/presentations')} className="mt-4 underline text-sm">← Back to list</button>
      </div>
    );
  }
  if (!deck) {
    return <div className="p-6 flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  }

  const saveStatusLabel = saveError
    ? 'Save failed'
    : saving
      ? 'Saving…'
      : dirty
        ? 'Unsaved changes'
        : lastSavedAt
          ? 'All changes saved'
          : 'Saved';

  return (
    <div className="flex h-full relative">
      {/* hidden file inputs for upload-from-disk */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg,.tif,.tiff"
        onChange={(e) => { e.target.files && uploadFiles(e.target.files); e.target.value = ''; }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        // @ts-expect-error — webkitdirectory is a standard HTML attr but TS lib doesn't know about it.
        webkitdirectory=""
        directory=""
        onChange={(e) => { e.target.files && uploadFiles(e.target.files); e.target.value = ''; }}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header bar — title row always sits above the action cluster so the
            ~10-button cluster never collides with the title/status pills. */}
        <div className="flex flex-col gap-2 px-3 md:px-4 py-2 border-b border-gray-200 bg-white flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0 w-full">
            <button
              onClick={() => router.push('/presentations')}
              className="p-1.5 hover:bg-gray-100 rounded text-gray-600 flex-shrink-0"
              title="Back to list"
              aria-label="Back to list"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <AutoGrowTextarea
              value={deck.title}
              onChange={setTitle}
              placeholder="Untitled presentation"
              className="text-base md:text-lg font-semibold text-gray-900 bg-transparent border-0 border-b border-transparent focus:border-emerald-400 focus:outline-none focus:ring-0 resize-none flex-1 min-w-0"
            />
            <SaveStatusPill
              label={saveStatusLabel}
              busy={saving}
              dirty={dirty}
              error={!!saveError}
              onClick={dirty ? saveNow : undefined}
            />
            <span className={`hidden sm:inline whitespace-nowrap text-[10px] uppercase tracking-wide px-2 py-0.5 rounded flex-shrink-0 ${
              deck.status === 'published' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-600'
            }`}>{deck.status}</span>
          </div>

          {/* Action cluster: wraps to multiple rows when buttons don't fit.
              Labels hide on small screens so icons still fit. */}
          <div className="flex flex-wrap items-center gap-1 w-full">
            <button
              onClick={collapseAll}
              className="px-2 py-1.5 rounded text-xs flex items-center gap-1 border border-gray-300 text-gray-700 hover:bg-gray-50 flex-shrink-0"
              title="Collapse all sections"
            >
              <ChevronsUpDown className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Collapse</span>
            </button>
            <button
              onClick={expandAll}
              className="px-2 py-1.5 rounded text-xs flex items-center gap-1 border border-gray-300 text-gray-700 hover:bg-gray-50 flex-shrink-0"
              title="Expand all"
            >
              <ChevronsUpDown className="w-3.5 h-3.5 rotate-180" /> <span className="hidden sm:inline">Expand</span>
            </button>
            <button
              onClick={() => setShowAttachments(v => !v)}
              className={`px-2.5 py-1.5 rounded text-xs flex items-center gap-1 flex-shrink-0 ${showAttachments ? 'bg-gray-100 text-gray-800 border border-gray-300' : 'border border-gray-300 text-gray-600 hover:bg-gray-50'}`}
              title="Attachments"
            >
              <FileStack className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Attachments</span> ({deck.attachments.length})
            </button>
            <button
              onClick={() => setShowFactCheck(v => !v)}
              className={`px-2.5 py-1.5 rounded text-xs flex items-center gap-1 flex-shrink-0 ${showFactCheck ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'border border-gray-300 text-gray-600 hover:bg-gray-50'}`}
              title="Fact-check"
            >
              <ShieldCheck className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Fact-check</span>
            </button>
            <button
              onClick={() => setShowAudit(true)}
              className="px-2.5 py-1.5 rounded text-xs flex items-center gap-1 flex-shrink-0 border border-gray-300 text-gray-600 hover:bg-gray-50"
              title="Audit [DOC:id] citation labels vs actual filenames (draft-only)"
            >
              <Search className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Audit cites</span>
            </button>
            <button
              onClick={() => setShowVersions(true)}
              className="px-2.5 py-1.5 rounded text-xs flex items-center gap-1 flex-shrink-0 border border-gray-300 text-gray-600 hover:bg-gray-50"
              title="Publish history (versions + rollback)"
            >
              <History className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Versions</span>
            </button>
            <button
              onClick={() => setPreviewing(true)}
              className="px-2.5 py-1.5 rounded text-xs flex items-center gap-1 border border-gray-300 text-gray-700 hover:bg-gray-50 flex-shrink-0"
              title="Full-screen preview (exactly how this publishes)"
            >
              <Eye className="w-3.5 h-3.5" /> <span className="hidden sm:inline">Preview</span>
            </button>
            <ExportMenu pending={exporting !== null} onPick={(fmt) => handleExport(fmt)} />
            <PasswordButton
              hasPassword={deck.has_password}
              onSetPassword={handleSetPassword}
            />
            <button
              onClick={togglePublish}
              className={`px-2.5 py-1.5 rounded text-xs flex items-center gap-1 flex-shrink-0 ${
                publishState === 'published'
                  ? 'bg-emerald-100 text-emerald-700 border border-emerald-300'
                  : publishState === 'republish'
                  ? 'bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200'
                  : 'text-white'
              }`}
              style={publishState === 'unpublished' ? { backgroundColor: brandColor } : undefined}
              title={
                publishState === 'republish'
                  ? `Draft has ${pendingChanges} unpublished change${pendingChanges === 1 ? '' : 's'} — click to republish`
                  : publishState === 'published'
                  ? 'Draft matches the live published version'
                  : 'Publish to create v1 and a shareable URL'
              }
            >
              <Globe className="w-3.5 h-3.5" />
              {publishState === 'published'
                ? 'Published'
                : publishState === 'republish'
                ? `Republish (${pendingChanges})`
                : 'Publish'}
            </button>
          </div>
        </div>

        {/* Public URL strip */}
        {publicUrl && (
          <div className="px-4 py-2 bg-emerald-50 border-b border-emerald-200 flex items-center gap-2 text-xs flex-shrink-0">
            <Globe className="w-3.5 h-3.5 text-emerald-700" />
            <a href={publicUrl} target="_blank" rel="noopener noreferrer" className="text-emerald-800 hover:underline truncate flex-1">{publicUrl}</a>
            <button onClick={() => navigator.clipboard.writeText(publicUrl)} className="p-1 hover:bg-emerald-100 rounded" title="Copy link">
              <Copy className="w-3 h-3" />
            </button>
            <a href={publicUrl} target="_blank" rel="noopener noreferrer" className="p-1 hover:bg-emerald-100 rounded" title="Open">
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        )}

        {/* Attachments accordion */}
        {showAttachments && (
          <div className="border-b border-gray-200 bg-white px-4 py-3 flex-shrink-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold text-gray-700">Attachments ({deck.attachments.length})</span>
              <div className="flex-1" />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={!!uploadProgress}
                className="text-xs flex items-center gap-1 px-2 py-1 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                <Upload className="w-3 h-3" /> Add files
              </button>
              <button
                onClick={() => folderInputRef.current?.click()}
                disabled={!!uploadProgress}
                className="text-xs flex items-center gap-1 px-2 py-1 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                <Upload className="w-3 h-3" /> Add folder
              </button>
            </div>
            {uploadProgress && (
              <div className="text-xs text-gray-500 flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" />
                Uploading {uploadProgress.done}/{uploadProgress.total}…
              </div>
            )}
            {uploadError && (
              <div className="text-xs text-red-600 mb-2">{uploadError}</div>
            )}
            {deck.attachments.length === 0 ? (
              <p className="text-xs text-gray-400 italic">No attachments yet — add files or a folder above.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {deck.attachments.map(att => (
                  <li key={att.id} className="flex items-center gap-2 py-1.5 text-xs">
                    <Paperclip className="w-3 h-3 text-gray-400 flex-shrink-0" />
                    <span className="truncate flex-1 text-gray-700">{att.filename}</span>
                    <button
                      onClick={() => handleRemoveAttachment(att.id)}
                      className="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded"
                      title="Remove from deck"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Sections */}
        <div className="flex-1 overflow-y-auto px-6 py-4 bg-gray-50">
          <div className="max-w-3xl mx-auto">
            {deck.sections.length === 0 ? (
              <div className="text-center py-16 text-gray-500 bg-white rounded-lg border border-dashed border-gray-300">
                <p className="text-sm">No sections yet.</p>
                <p className="text-xs mt-2">Use the AI Analyst chat or the buttons below to add one.</p>
                <AddSectionButtons onAdd={(k) => addSectionAt(k)} />
              </div>
            ) : (
              <>
                <InsertBetweenButton onInsert={(k) => addSectionAt(k, 0)} />
                {deck.sections.map((section, idx) => {
                  const collapsed = collapsedSet.has(section.id);
                  const isDragOver = dragOverId === section.id && dragSourceId !== section.id;
                  const isDragSource = dragSourceId === section.id;
                  return (
                    <React.Fragment key={section.id}>
                      <div
                        id={`sec-${section.id}`}
                        draggable={isDragSource}
                        onDragStart={(e) => {
                          e.dataTransfer.setData('text/plain', section.id);
                          e.dataTransfer.effectAllowed = 'move';
                        }}
                        onDragOver={(e) => {
                          if (!dragSourceId || dragSourceId === section.id) return;
                          e.preventDefault();
                          e.dataTransfer.dropEffect = 'move';
                          if (dragOverId !== section.id) setDragOverId(section.id);
                        }}
                        onDragLeave={() => { if (dragOverId === section.id) setDragOverId(null); }}
                        onDragEnd={() => { setDragSourceId(null); setDragOverId(null); }}
                        onDrop={(e) => {
                          e.preventDefault();
                          const src = dragSourceId || (() => { try { return e.dataTransfer.getData('text/plain'); } catch { return null; } })();
                          if (src) reorderSections(src, section.id);
                          setDragSourceId(null);
                          setDragOverId(null);
                        }}
                        className={`bg-white rounded-lg border ${isDragOver ? 'border-2' : 'border-gray-200'} mb-3 group relative transition-shadow ${
                          isDragSource ? 'opacity-40' : ''
                        }`}
                        style={isDragOver ? { borderColor: brandColor, boxShadow: `0 0 0 2px ${brandColor}30` } : undefined}
                      >
                        {/* Section toolbar — always visible on touch (mobile/tablet),
                            reveal-on-hover on desktop to keep the deck visually clean. */}
                        <div className="flex flex-wrap items-center gap-1 px-3 py-1.5 border-b border-gray-100 bg-gray-50/60 rounded-t-lg opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                          {/* Drag grip */}
                          <button
                            onMouseDown={() => setDragSourceId(section.id)}
                            onMouseUp={() => { if (dragSourceId === section.id && dragOverId === null) setDragSourceId(null); }}
                            className="p-1 cursor-grab active:cursor-grabbing text-gray-400 hover:text-gray-700"
                            title="Drag to reorder"
                          >
                            <ChevronsUpDown className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={() => moveSectionLocal(section.id, -1)} disabled={idx === 0} className="p-1 hover:bg-gray-200 rounded disabled:opacity-30" title="Move up">
                            <ChevronUp className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={() => moveSectionLocal(section.id, 1)} disabled={idx === deck.sections.length - 1} className="p-1 hover:bg-gray-200 rounded disabled:opacity-30" title="Move down">
                            <ChevronDown className="w-3.5 h-3.5" />
                          </button>
                          <span className="text-[10px] text-gray-400 mx-1">|</span>
                          <button onClick={() => copySection(section)} className="p-1 hover:bg-gray-200 rounded" title="Copy section as markdown">
                            {copiedId === section.id ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
                          </button>
                          <button onClick={() => duplicateSection(section.id)} className="p-1 hover:bg-gray-200 rounded" title="Duplicate">
                            <CopyPlus className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={() => toggleCollapsed(section.id)} className="p-1 hover:bg-gray-200 rounded" title={collapsed ? 'Expand' : 'Collapse'}>
                            {collapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
                          </button>
                          {section.kind === 'narrative' && (
                            <SectionAIMenu sectionId={section.id} onEdit={handleAIEdit} brandColor={brandColor} />
                          )}
                          <div className="flex-1" />
                          <span className="text-[10px] uppercase tracking-wide text-gray-400">
                            {section.kind}{section.is_cover ? ' · cover' : ''}
                          </span>
                          <label className="flex items-center gap-1 text-[10px] text-gray-500 cursor-pointer ml-2">
                            <input
                              type="checkbox"
                              checked={!!section.is_cover}
                              onChange={(e) => updateSection(section.id, { is_cover: e.target.checked })}
                              className="cursor-pointer"
                              style={{ accentColor: brandColor, width: 11, height: 11 }}
                            />
                            Cover
                          </label>
                          <button onClick={() => removeSectionLocal(section.id)} className="p-1 hover:bg-red-50 text-red-500 rounded ml-1" title="Delete section">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        <div className="p-5">
                          {/* SourceChip + heading */}
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            <SourceChip section={section} />
                          </div>
                          <AutoGrowTextarea
                            value={section.title || ''}
                            onChange={(t) => updateSection(section.id, { title: t })}
                            placeholder="Section title"
                            className="w-full text-xl font-semibold border-b border-transparent hover:border-gray-200 focus:border-gray-400 focus:outline-none pb-1 transition-colors resize-none mb-3"
                          />

                          {!collapsed && (
                            <>
                              {section.kind === 'narrative' && (
                                <NarrativeBlock
                                  section={section}
                                  editable
                                  onSave={(p) => updateSection(section.id, p)}
                                  brandColor={brandColor}
                                />
                              )}
                              {section.kind === 'table' && (
                                <TableBlock
                                  section={section}
                                  editable
                                  onSave={(p) => updateSection(section.id, p)}
                                  brandColor={brandColor}
                                />
                              )}
                              {section.kind === 'attachment' && (
                                <AttachmentBlock
                                  section={section}
                                  attachments={deck.attachments}
                                  onPreview={(att) => {
                                    window.dispatchEvent(new CustomEvent('ah:open-citation', {
                                      detail: { kind: 'doc', filename: att.filename },
                                    }));
                                  }}
                                  brandColor={brandColor}
                                />
                              )}
                              {section.kind === 'react_component' && (
                                <ReactComponentBlock
                                  section={section}
                                  editable
                                  onSave={(p) => updateSection(section.id, p)}
                                  brandColor={brandColor}
                                />
                              )}

                              {/* Source-chip editor — three small inputs revealed on hover */}
                              <details className="mt-3 text-xs opacity-60 hover:opacity-100">
                                <summary className="cursor-pointer text-gray-500 select-none">Source chip…</summary>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2">
                                  <input
                                    placeholder="Label (e.g. ACFR FY24)"
                                    value={section.source_label || ''}
                                    onChange={(e) => updateSection(section.id, { source_label: e.target.value })}
                                    className="px-2 py-1 border border-gray-200 rounded"
                                  />
                                  <input
                                    placeholder="Filename (citation lookup)"
                                    value={section.source_filename || ''}
                                    onChange={(e) => updateSection(section.id, { source_filename: e.target.value })}
                                    className="px-2 py-1 border border-gray-200 rounded"
                                  />
                                  <input
                                    placeholder="External URL"
                                    value={section.source_url || ''}
                                    onChange={(e) => updateSection(section.id, { source_url: e.target.value })}
                                    className="px-2 py-1 border border-gray-200 rounded"
                                  />
                                </div>
                              </details>
                            </>
                          )}
                        </div>
                      </div>
                      <InsertBetweenButton onInsert={(k) => addSectionAt(k, idx + 1)} />
                    </React.Fragment>
                  );
                })}
                <AddSectionButtons onAdd={(k) => addSectionAt(k)} />
              </>
            )}
          </div>
        </div>
      </div>

      {/* Right rail — fact-check panel. On mobile renders full-width so the
          340px desktop rail doesn't crush the editor body to ~35px. */}
      {showFactCheck && (
        <div className="w-full md:w-[340px] flex-shrink-0 border-l border-gray-200 absolute inset-0 z-30 bg-white md:static md:z-auto">
          <FactCheckPanel
            presentationId={presentationId}
            initial={deck.last_fact_check}
            onClose={() => setShowFactCheck(false)}
          />
        </div>
      )}

      {/* Side preview panels — citations + external URLs from narrative bodies */}
      {!previewing && <CitationPreview mode="auth" />}
      {!previewing && <WebReferencePreview />}

      {/* Slide-in side panels — versions + citation audit */}
      <VersionsPanel
        presentationId={presentationId}
        open={showVersions}
        onClose={() => setShowVersions(false)}
        brandColor={brandColor}
        onAfterRollback={async () => {
          try {
            const fresh = await getPresentation(presentationId);
            setDeck(fresh);
          } catch { /* swallow — panel error already shown */ }
        }}
      />
      <CitationAuditPanel
        presentationId={presentationId}
        open={showAudit}
        onClose={() => setShowAudit(false)}
        brandColor={brandColor}
        onAfterApply={async () => {
          try {
            const fresh = await getPresentation(presentationId);
            setDeck(fresh);
          } catch { /* swallow */ }
        }}
      />

      {/* Full-screen preview overlay */}
      {previewing && (
        <div className="fixed inset-0 z-50 bg-gray-900/40 backdrop-blur-sm flex flex-col">
          <div className="flex items-center justify-between bg-white border-b border-gray-200 px-4 py-2 shadow-sm">
            <div className="flex items-center gap-2 text-sm text-gray-700">
              <Eye className="w-4 h-4" style={{ color: brandColor }} />
              <span className="font-semibold">Preview</span>
              <span className="text-xs text-gray-500">— exactly how this publishes</span>
            </div>
            <button
              onClick={() => setPreviewing(false)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
            >
              <X className="w-4 h-4" /> Close preview
            </button>
          </div>
          <div className="flex-1 overflow-auto bg-gray-100">
            <PresentationViewer
              title={deck.title}
              sections={deck.sections}
              attachments={deck.attachments}
              mode="auth"
              publishedAt={deck.published_at}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Small subcomponents ───────────────────────────────────────────────────

/** Save-status pill matching bank-processor's Saving/Saved/Unsaved/Error UX. */
function SaveStatusPill({
  label, busy, dirty, error, onClick,
}: {
  label: string; busy: boolean; dirty: boolean; error: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] flex-shrink-0 ${
        error ? 'bg-red-50 text-red-700 border border-red-200'
          : busy ? 'bg-gray-50 text-gray-500'
          : dirty ? 'bg-amber-50 text-amber-700 border border-amber-200 cursor-pointer hover:bg-amber-100'
          : 'text-gray-400'
      }`}
      title={dirty && onClick ? 'Click to save now' : undefined}
    >
      {busy ? <Loader2 className="w-3 h-3 animate-spin" />
        : error ? <X className="w-3 h-3" />
        : dirty ? <Save className="w-3 h-3" />
        : <CheckCircle2 className="w-3 h-3" />}
      {label}
    </button>
  );
}

/** Auto-growing single-line styled textarea. Wraps to additional lines and
 *  grows vertically as the user types. Use in place of <input type="text">
 *  for fields that may overflow (deck title, section headings). */
function AutoGrowTextarea({
  value, onChange, placeholder, className,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={className}
    />
  );
}

/** Add-section button row used at the bottom of the deck and inside the
 *  empty-state placeholder. */
function AddSectionButtons({ onAdd }: { onAdd: (kind: SectionKind) => void }) {
  return (
    <div className="flex gap-2 justify-center pt-4 flex-wrap">
      <button onClick={() => onAdd('narrative')}
        className="flex items-center gap-1 px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-100 text-gray-700 bg-white">
        <AlignLeft className="w-3.5 h-3.5" /> Narrative
      </button>
      <button onClick={() => onAdd('table')}
        className="flex items-center gap-1 px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-100 text-gray-700 bg-white">
        <TableIcon className="w-3.5 h-3.5" /> Table
      </button>
      <button onClick={() => onAdd('react_component')}
        className="flex items-center gap-1 px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-100 text-gray-700 bg-white">
        <Sparkles className="w-3.5 h-3.5" /> Custom component
      </button>
    </div>
  );
}

/** Slim "+" button rendered between sections to insert a new one at this
 *  exact index. Always visible on touch; reveal-on-hover on desktop to keep
 *  the deck visually clean. */
function InsertBetweenButton({ onInsert }: { onInsert: (kind: SectionKind) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="h-6 group flex items-center justify-center relative">
      <div className="h-px flex-1 bg-gray-200 md:bg-transparent md:group-hover:bg-gray-200 transition-colors" />
      <button
        onClick={() => setOpen(o => !o)}
        className="opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity px-2 py-0.5 rounded-full border border-gray-300 bg-white text-gray-500 hover:text-gray-800 hover:border-gray-400 text-[11px] flex items-center gap-1"
        title="Insert section here"
      >
        <Plus className="w-3 h-3" /> Insert
      </button>
      <div className="h-px flex-1 bg-gray-200 md:bg-transparent md:group-hover:bg-gray-200 transition-colors" />
      {open && (
        <div className="absolute top-full mt-1 z-10 bg-white border border-gray-200 rounded-lg shadow-lg py-1 text-xs">
          <button onClick={() => { onInsert('narrative'); setOpen(false); }} className="block w-full text-left px-3 py-1.5 hover:bg-gray-50">Narrative</button>
          <button onClick={() => { onInsert('table'); setOpen(false); }} className="block w-full text-left px-3 py-1.5 hover:bg-gray-50">Table</button>
          <button onClick={() => { onInsert('attachment'); setOpen(false); }} className="block w-full text-left px-3 py-1.5 hover:bg-gray-50">Attachment</button>
          <button onClick={() => { onInsert('react_component'); setOpen(false); }} className="block w-full text-left px-3 py-1.5 hover:bg-gray-50">Custom component</button>
        </div>
      )}
    </div>
  );
}

/** Per-section dropdown that fires AI-edit prompts. */
function SectionAIMenu({
  sectionId, onEdit, brandColor,
}: {
  sectionId: string;
  onEdit: (sectionId: string, action: string) => void;
  brandColor: string;
}) {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const fire = async (action: string) => {
    setOpen(false);
    setRunning(true);
    try { await onEdit(sectionId, action); } finally { setRunning(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        disabled={running}
        className="px-2 py-0.5 text-[11px] rounded hover:bg-gray-200 flex items-center gap-1 text-gray-700 disabled:opacity-50"
        title="AI edit"
      >
        {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" style={{ color: brandColor }} />}
        AI
      </button>
      {open && (
        <div className="absolute left-0 mt-1 w-44 rounded-md shadow-lg bg-white border border-gray-200 z-10 text-xs overflow-hidden">
          {[
            ['rewrite_tighter', 'Rewrite tighter'],
            ['expand', 'Expand with detail'],
            ['fact_polish', 'Polish facts'],
            ['translate_plain', 'Plain English'],
            ['summarize', 'Summarize as bullets'],
          ].map(([k, label]) => (
            <button
              key={k}
              onClick={() => fire(k)}
              className="w-full text-left px-3 py-1.5 hover:bg-gray-50 text-gray-800"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Export popover — PPTX/DOCX/PDF + zip bundles. */
function ExportMenu({ pending, onPick }: { pending: boolean; onPick: (fmt: 'pptx' | 'docx') => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const pick = (fmt: 'pptx' | 'docx') => { setOpen(false); onPick(fmt); };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        disabled={pending}
        className="px-2.5 py-1.5 rounded text-xs flex items-center gap-1 border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        title="Export"
      >
        {pending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />} Export
      </button>
      {open && (
        <div className="absolute right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-40 min-w-[200px]">
          <button onClick={() => pick('pptx')} className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50">PowerPoint (.pptx)</button>
          <button onClick={() => pick('docx')} className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50">Word (.docx)</button>
        </div>
      )}
    </div>
  );
}

/** Public-deck password popover — set/change/clear. */
function PasswordButton({
  hasPassword, onSetPassword,
}: {
  hasPassword: boolean;
  onSetPassword: (pw: string) => Promise<boolean | null>;
}) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const submit = async (clear = false) => {
    setSaving(true); setError(null);
    try {
      await onSetPassword(clear ? '' : password);
      setPassword(''); setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally { setSaving(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className={`px-2.5 py-1.5 rounded text-xs flex items-center gap-1 border ${
          hasPassword ? 'border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100' : 'border-gray-300 hover:bg-gray-50 text-gray-700'
        }`}
        title={hasPassword ? 'Public viewer requires a password' : 'Set a password to protect the public viewer'}
      >
        {hasPassword ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
        {hasPassword ? 'Password set' : 'Password'}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg p-3 z-40 w-72">
          <div className="text-xs font-semibold text-gray-700 mb-2">
            {hasPassword ? 'Change or remove password' : 'Set a public viewer password'}
          </div>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={hasPassword ? 'New password' : 'Password'}
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400"
            autoFocus
          />
          {error && <div className="text-xs text-red-700 mt-1">{error}</div>}
          <div className="flex items-center gap-2 mt-2">
            <button
              type="button"
              disabled={saving || !password}
              onClick={() => submit(false)}
              className="flex-1 px-2 py-1.5 text-xs text-white rounded disabled:opacity-50"
              style={{ backgroundColor: brandColor }}
            >
              {saving ? 'Saving…' : 'Save password'}
            </button>
            {hasPassword && (
              <button
                type="button"
                disabled={saving}
                onClick={() => submit(true)}
                className="px-2 py-1.5 text-xs border border-red-300 text-red-700 rounded hover:bg-red-50 disabled:opacity-50"
              >
                Remove
              </button>
            )}
          </div>
          <div className="text-[10px] text-gray-500 mt-2">
            Stored as a bcrypt hash. Required for the public viewer at /p/{`{slug}`}.
          </div>
        </div>
      )}
    </div>
  );
}
