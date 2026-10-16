'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/app/contexts/AuthContext';
import { Plus, Trash2, Globe, FileText, Loader2, Share2, Eye } from 'lucide-react';
import {
  type Presentation,
  listPresentations,
  createPresentation,
  deletePresentation,
} from '@/lib/presentationsApi';
import {
  listPresentationShares,
  addPresentationShare,
  removePresentationShare,
} from '@/lib/api';
import { ShareDialog } from '@/components/ShareDialog';

const brandColor = '#385854';

export default function PresentationsListPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [decks, setDecks] = useState<Presentation[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [shareTarget, setShareTarget] = useState<Presentation | null>(null);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.push('/'); return; }
    let cancelled = false;
    (async () => {
      try {
        const list = await listPresentations();
        if (!cancelled) setDecks(list);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      }
    })();
    return () => { cancelled = true; };
  }, [user, authLoading, router]);

  const handleCreate = async () => {
    const title = newTitle.trim() || 'Untitled presentation';
    setBusy(true);
    try {
      const p = await createPresentation(title);
      router.push(`/presentations/${p.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create');
      setBusy(false);
    }
  };

  const handleDelete = async (id: string, title: string) => {
    if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
    await deletePresentation(id);
    setDecks((decks || []).filter(d => d.id !== id));
  };

  if (authLoading || !user || decks === null) {
    return <div className="p-6 flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  }

  return (
    <div className="max-w-5xl mx-auto p-3 md:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4 md:mb-6">
        <div className="min-w-0">
          <h1 className="text-xl md:text-2xl font-bold text-gray-900">Presentations</h1>
          <p className="text-xs md:text-sm text-gray-500">Analytical decks and reports built from the document library.</p>
        </div>
        <button onClick={() => setCreating(true)}
          className="px-3 py-2 rounded text-white text-sm flex items-center justify-center gap-1.5 shadow w-full sm:w-auto"
          style={{ backgroundColor: brandColor }}>
          <Plus className="w-4 h-4" /> New presentation
        </button>
      </div>

      {creating && (
        <div className="mb-6 p-4 border border-gray-200 rounded-lg bg-white">
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            placeholder="Presentation title"
            className="w-full text-base border-b border-gray-200 focus:outline-none focus:border-gray-400 pb-1 mb-3"
            autoFocus
          />
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={busy} className="px-3 py-1.5 rounded text-white text-sm" style={{ backgroundColor: brandColor }}>
              {busy ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => { setCreating(false); setNewTitle(''); }} className="px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50">
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {decks.length === 0 ? (
        <div className="text-center py-16 border-2 border-dashed border-gray-200 rounded-lg">
          <FileText className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500">No presentations yet.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {decks.map((d) => {
            const isOwner = d.is_owner !== false;  // default true for backward-compat
            const isReadOnly = !isOwner && d.share_role !== 'editor';
            return (
            <div key={d.id} className="border border-gray-200 rounded-lg bg-white p-4 hover:shadow-md transition-shadow group">
              <Link href={`/presentations/${d.id}`} className="block">
                <h3 className="font-semibold text-gray-900 mb-1 truncate group-hover:underline">{d.title}</h3>
                <div className="flex items-center gap-2 text-xs text-gray-500 mb-2 flex-wrap">
                  <span>{d.sections.length} section{d.sections.length === 1 ? '' : 's'}</span>
                  <span>•</span>
                  <span className={`uppercase tracking-wide ${
                    d.status === 'published' ? 'text-emerald-600' : ''
                  }`}>{d.status}</span>
                  {d.public_slug && <Globe className="w-3 h-3 text-emerald-600" />}
                  {!isOwner && (
                    <span className="ml-auto text-[10px] uppercase tracking-wide bg-gray-100 text-gray-600 rounded px-1.5 py-0.5 flex items-center gap-1">
                      {isReadOnly && <Eye className="w-3 h-3" />}
                      Shared{d.share_role ? ` (${d.share_role})` : ''}
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-gray-400">Updated {new Date(d.updated_at).toLocaleDateString()}</p>
              </Link>
              <div className="mt-3 flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                {isOwner && (
                  <button
                    onClick={() => setShareTarget(d)}
                    className="text-xs text-gray-600 hover:text-gray-900 flex items-center gap-1"
                  >
                    <Share2 className="w-3 h-3" /> Share
                  </button>
                )}
                {isOwner && (
                  <button
                    onClick={() => handleDelete(d.id, d.title)}
                    className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                  >
                    <Trash2 className="w-3 h-3" /> Delete
                  </button>
                )}
              </div>
            </div>
            );
          })}
        </div>
      )}

      {shareTarget && (
        <ShareDialog
          resourceType="presentation"
          resourceId={shareTarget.id}
          resourceTitle={shareTarget.title}
          open={true}
          onClose={() => setShareTarget(null)}
          fetchShares={listPresentationShares}
          addShare={addPresentationShare}
          removeShare={removePresentationShare}
        />
      )}
    </div>
  );
}
