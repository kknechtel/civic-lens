'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, Trash2, X, Check } from 'lucide-react';
import {
  type DirectoryUser,
  type ShareEntry,
  getUserDirectory,
} from '@/lib/api';

type Role = 'viewer' | 'editor';

export interface ShareDialogProps {
  resourceType: 'presentation' | 'project';
  resourceId: string;
  resourceTitle: string;
  open: boolean;
  onClose: () => void;
  /** Resolve to current shares — caller passes the right API client. */
  fetchShares: (id: string) => Promise<ShareEntry[]>;
  addShare: (id: string, userId: string, role: Role) => Promise<ShareEntry>;
  removeShare: (id: string, userId: string) => Promise<unknown>;
}

const brandColor = '#385854';

export function ShareDialog({
  resourceType,
  resourceId,
  resourceTitle,
  open,
  onClose,
  fetchShares,
  addShare,
  removeShare,
}: ShareDialogProps) {
  const [shares, setShares] = useState<ShareEntry[] | null>(null);
  const [directory, setDirectory] = useState<DirectoryUser[] | null>(null);
  const [search, setSearch] = useState('');
  const [pickedUser, setPickedUser] = useState<DirectoryUser | null>(null);
  const [role, setRole] = useState<Role>('viewer');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const [s, d] = await Promise.all([fetchShares(resourceId), getUserDirectory()]);
        if (cancelled) return;
        setShares(s);
        setDirectory(d);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      }
    })();
    return () => { cancelled = true; };
  }, [open, resourceId, fetchShares]);

  // Filter directory: hide users who already have a share + simple substring match.
  const candidates = useMemo(() => {
    if (!directory) return [];
    const sharedIds = new Set((shares ?? []).map(s => s.user_id));
    const q = search.trim().toLowerCase();
    return directory
      .filter(u => !sharedIds.has(u.id))
      .filter(u => !q || u.email.toLowerCase().includes(q) ||
                   (u.full_name?.toLowerCase().includes(q) ?? false))
      .slice(0, 8);
  }, [directory, shares, search]);

  const handleAdd = async () => {
    if (!pickedUser) return;
    setBusy(true);
    setError(null);
    try {
      const entry = await addShare(resourceId, pickedUser.id, role);
      setShares(prev => [...(prev ?? []), entry]);
      setPickedUser(null);
      setSearch('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to share');
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (userId: string) => {
    if (!confirm('Remove this person\'s access?')) return;
    setBusy(true);
    try {
      await removeShare(resourceId, userId);
      setShares(prev => (prev ?? []).filter(s => s.user_id !== userId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove');
    } finally {
      setBusy(false);
    }
  };

  const handleRoleChange = async (userId: string, newRole: Role) => {
    setBusy(true);
    try {
      const entry = await addShare(resourceId, userId, newRole);
      setShares(prev => (prev ?? []).map(s => s.user_id === userId ? entry : s));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update');
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-100">
          <div>
            <h2 className="font-semibold text-gray-900">Share {resourceType}</h2>
            <p className="text-xs text-gray-500 truncate max-w-[20rem]">{resourceTitle}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto">
          {/* Add new */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Add a person
            </label>
            <div className="relative">
              <input
                value={pickedUser ? (pickedUser.full_name || pickedUser.email) : search}
                onChange={(e) => { setPickedUser(null); setSearch(e.target.value); }}
                placeholder="Search by name or email…"
                className="w-full text-sm border border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-gray-500"
              />
              {!pickedUser && search && candidates.length > 0 && (
                <div className="absolute left-0 right-0 top-full mt-1 border border-gray-200 rounded bg-white shadow-md z-10 max-h-48 overflow-y-auto">
                  {candidates.map(u => (
                    <button
                      key={u.id}
                      onClick={() => { setPickedUser(u); setSearch(''); }}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex flex-col"
                    >
                      <span className="font-medium text-gray-900">{u.full_name || u.email}</span>
                      {u.full_name && <span className="text-xs text-gray-500">{u.email}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2 mt-2">
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="text-sm border border-gray-300 rounded px-2 py-1.5"
              >
                <option value="viewer">Can view</option>
                <option value="editor">Can edit</option>
              </select>
              <button
                onClick={handleAdd}
                disabled={!pickedUser || busy}
                className="px-3 py-1.5 rounded text-white text-sm disabled:opacity-50 flex items-center gap-1"
                style={{ backgroundColor: brandColor }}
              >
                {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                Add
              </button>
            </div>
          </div>

          {/* Existing shares */}
          <div>
            <p className="text-xs font-medium text-gray-600 mb-2">People with access</p>
            {shares === null ? (
              <p className="text-xs text-gray-400">Loading…</p>
            ) : shares.length === 0 ? (
              <p className="text-xs text-gray-400">Only you can see this {resourceType}.</p>
            ) : (
              <ul className="space-y-1">
                {shares.map(s => (
                  <li key={s.user_id} className="flex items-center justify-between gap-2 text-sm py-1.5 px-2 rounded hover:bg-gray-50">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-gray-900 truncate">{s.full_name || s.email}</p>
                      {s.full_name && <p className="text-[11px] text-gray-500 truncate">{s.email}</p>}
                    </div>
                    <select
                      value={s.role}
                      onChange={(e) => handleRoleChange(s.user_id, e.target.value as Role)}
                      disabled={busy}
                      className="text-xs border border-gray-200 rounded px-1.5 py-0.5"
                    >
                      <option value="viewer">View</option>
                      <option value="editor">Edit</option>
                    </select>
                    <button
                      onClick={() => handleRemove(s.user_id)}
                      disabled={busy}
                      className="text-gray-400 hover:text-red-600 disabled:opacity-50"
                      title="Remove"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>

        <div className="px-4 py-3 border-t border-gray-100 flex justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
