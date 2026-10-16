"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getAdminStats, getAdminUsers, approveUser, toggleUserActive,
  toggleUserAdmin, deleteUser, createInvite, getInvites, deleteInvite,
} from "@/lib/api";
import { useAuth } from "@/app/contexts/AuthContext";
import {
  CheckIcon, ClipboardIcon, TrashIcon, PlusIcon,
  UserPlusIcon, ShieldCheckIcon,
} from "@heroicons/react/24/outline";
import AdminDocumentsPanel from "@/components/admin/AdminDocumentsPanel";
import AdminCostsPanel from "@/components/admin/AdminCostsPanel";
import DiscoverSourcesPanel from "@/components/admin/DiscoverSourcesPanel";

const brandColor = "#385854";

export default function AdminPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const { data: stats } = useQuery({ queryKey: ["admin-stats"], queryFn: getAdminStats });
  const { data: users } = useQuery({ queryKey: ["admin-users"], queryFn: getAdminUsers });
  const { data: invites } = useQuery({ queryKey: ["admin-invites"], queryFn: getInvites });

  // Mutations
  const approveMut = useMutation({
    mutationFn: approveUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-stats"] });
    },
  });
  const toggleActiveMut = useMutation({
    mutationFn: toggleUserActive,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const toggleAdminMut = useMutation({
    mutationFn: toggleUserAdmin,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const deleteUserMut = useMutation({
    mutationFn: deleteUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-stats"] });
    },
  });
  const deleteInviteMut = useMutation({
    mutationFn: deleteInvite,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-invites"] }),
  });

  // Invite creation
  const [inviteEmail, setInviteEmail] = useState("");
  const [copiedInvite, setCopiedInvite] = useState<string | null>(null);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);

  const createInviteMut = useMutation({
    mutationFn: () => createInvite(inviteEmail || undefined),
    onSuccess: (data) => {
      setLastInviteUrl(data.invite_url);
      setInviteEmail("");
      queryClient.invalidateQueries({ queryKey: ["admin-invites"] });
      handleCopyInvite(data.invite_url);
    },
  });

  const handleCopyInvite = async (url: string) => {
    await navigator.clipboard.writeText(url);
    setCopiedInvite(url);
    setTimeout(() => setCopiedInvite(null), 3000);
  };

  const [tab, setTab] = useState<"users" | "documents" | "costs" | "sources">("users");

  if (!user?.is_admin) {
    return (
      <div className="p-8">
        <p className="text-gray-500">Admin access required.</p>
      </div>
    );
  }

  const pendingUsers = users?.filter((u) => !u.is_active) || [];
  const activeUsers = users?.filter((u) => u.is_active) || [];

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Admin Dashboard</h1>
        <a href="/admin/ai" className="mt-1 inline-block text-sm underline" style={{ color: brandColor }}>AI providers, keys &amp; spending →</a>
        <div className="flex gap-1 border border-gray-200 rounded-lg p-0.5 bg-white">
          {(["users", "documents", "costs", "sources"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1 text-xs font-medium rounded ${
                tab === t ? "text-white" : "text-gray-600 hover:bg-gray-50"
              }`}
              style={tab === t ? { backgroundColor: brandColor } : {}}
            >
              {t === "users" ? "Users" : t === "documents" ? "Documents" : "Costs"}
            </button>
          ))}
        </div>
      </div>

      {/* Stats — always visible across all tabs */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: "Users", value: stats?.total_users },
          { label: "Pending", value: stats?.pending_users, highlight: (stats?.pending_users || 0) > 0 },
          { label: "Projects", value: stats?.total_projects },
          { label: "Documents", value: stats?.total_documents },
          { label: "Statements", value: stats?.total_statements },
        ].map((s) => (
          <div
            key={s.label}
            className={`rounded-xl shadow border p-4 ${
              s.highlight ? "border-amber-300 bg-amber-50" : "border-gray-200 bg-white"
            }`}
          >
            <p className={`text-2xl font-bold ${s.highlight ? "text-amber-700" : "text-gray-900"}`}>
              {s.value ?? "-"}
            </p>
            <p className={`text-sm ${s.highlight ? "text-amber-600" : "text-gray-500"}`}>{s.label}</p>
          </div>
        ))}
      </div>

      {/* Corpus health + cost stats — second row of always-visible cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          {
            label: "OCR'd",
            value: stats?.documents_ocrd ?? 0,
            sub: stats?.total_documents ? `of ${stats.total_documents}` : undefined,
          },
          {
            label: "Vector-indexed",
            value: stats?.documents_vector_indexed ?? 0,
            sub: stats?.total_documents ? `of ${stats.total_documents}` : undefined,
          },
          {
            label: "Cost (30d)",
            value: stats?.cost_last_30d_usd != null
              ? `$${stats.cost_last_30d_usd.toFixed(2)}`
              : "$0.00",
            sub: stats?.llm_calls_last_30d
              ? `${stats.llm_calls_last_30d} calls`
              : undefined,
          },
          {
            label: "Cost (total)",
            value: stats?.cost_total_usd != null
              ? `$${stats.cost_total_usd.toFixed(2)}`
              : "$0.00",
          },
          {
            label: "Avg / call",
            value: stats?.cost_last_30d_usd && stats?.llm_calls_last_30d
              ? `$${(stats.cost_last_30d_usd / stats.llm_calls_last_30d).toFixed(4)}`
              : "—",
            sub: "last 30d",
          },
        ].map((s) => (
          <div
            key={s.label}
            className="rounded-xl shadow border border-gray-200 bg-white p-4"
          >
            <p className="text-2xl font-bold text-gray-900">{s.value}</p>
            <p className="text-sm text-gray-500">{s.label}</p>
            {s.sub && <p className="text-[11px] text-gray-400 mt-0.5">{s.sub}</p>}
          </div>
        ))}
      </div>

      {tab === "documents" && <AdminDocumentsPanel />}
      {tab === "costs" && <AdminCostsPanel />}
      {tab === "sources" && <DiscoverSourcesPanel brandColor={brandColor} />}
      {tab === "users" && (
        <div className="space-y-6">

      {/* Pending Approval */}
      {pendingUsers.length > 0 && (
        <div className="bg-white rounded-xl shadow border border-amber-300 overflow-hidden">
          <div className="px-6 py-4 border-b border-amber-200 bg-amber-50">
            <div className="flex items-center gap-2">
              <ShieldCheckIcon className="w-5 h-5 text-amber-600" />
              <h2 className="font-semibold text-amber-800">
                Pending Approval ({pendingUsers.length})
              </h2>
            </div>
            <p className="text-xs text-amber-700 mt-0.5">
              These users have registered and are waiting for your approval.
            </p>
          </div>
          <div className="divide-y divide-amber-100">
            {pendingUsers.map((u) => (
              <div key={u.id} className="px-4 md:px-6 py-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 hover:bg-amber-50/50">
                <div className="min-w-0">
                  <p className="font-medium text-gray-900 truncate">{u.full_name || u.username}</p>
                  <p className="text-xs text-gray-500 truncate">{u.email}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={() => approveMut.mutate(u.id)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-white rounded-lg font-medium"
                    style={{ backgroundColor: brandColor }}
                  >
                    <CheckIcon className="w-3.5 h-3.5" /> Approve
                  </button>
                  <button
                    onClick={() => { if (confirm(`Delete ${u.email}?`)) deleteUserMut.mutate(u.id); }}
                    className="px-2 py-1.5 text-xs border border-red-300 text-red-600 rounded-lg hover:bg-red-50"
                  >
                    <TrashIcon className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Invite Links */}
      <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <UserPlusIcon className="w-5 h-5" style={{ color: brandColor }} />
            <h2 className="font-semibold text-gray-900">Invite Links</h2>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            Generate links that let people register and auto-approve. Optionally lock to a specific email.
          </p>
        </div>
        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50">
          <div className="flex items-center gap-3">
            <input
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="Optional: lock to email@example.com"
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <button
              onClick={() => createInviteMut.mutate()}
              disabled={createInviteMut.isPending}
              className="flex items-center gap-1.5 px-4 py-2 text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
              style={{ backgroundColor: brandColor }}
            >
              <PlusIcon className="w-4 h-4" />
              {createInviteMut.isPending ? "..." : "Create Invite"}
            </button>
          </div>
          {lastInviteUrl && (
            <div className="mt-3 flex items-center gap-2 p-2 bg-white border border-gray-200 rounded-lg">
              <code className="flex-1 text-xs text-gray-700 truncate">{lastInviteUrl}</code>
              <button
                onClick={() => handleCopyInvite(lastInviteUrl)}
                className="flex items-center gap-1 px-2 py-1 text-xs border rounded hover:bg-gray-50"
              >
                {copiedInvite === lastInviteUrl ? (
                  <><CheckIcon className="w-3 h-3" style={{ color: brandColor }} /> Copied</>
                ) : (
                  <><ClipboardIcon className="w-3 h-3" /> Copy</>
                )}
              </button>
            </div>
          )}
        </div>
        {invites && invites.length > 0 && (
          <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b sticky top-0">
                <tr>
                  <th className="text-left px-6 py-2 font-medium text-gray-500">Email</th>
                  <th className="text-left px-6 py-2 font-medium text-gray-500">Status</th>
                  <th className="text-left px-6 py-2 font-medium text-gray-500">Expires</th>
                  <th className="text-right px-6 py-2 font-medium text-gray-500"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {invites.map((inv) => (
                  <tr key={inv.id} className="hover:bg-gray-50">
                    <td className="px-6 py-2 text-gray-700">{inv.email || "Open invite"}</td>
                    <td className="px-6 py-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        inv.is_used ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"
                      }`}>
                        {inv.is_used ? "Used" : "Active"}
                      </span>
                    </td>
                    <td className="px-6 py-2 text-gray-500">
                      {new Date(inv.expires_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {!inv.is_used && (
                          <button
                            onClick={() => handleCopyInvite(`${window.location.origin}?invite=${inv.token}`)}
                            className="p-1 hover:bg-gray-100 rounded"
                            title="Copy link"
                          >
                            {copiedInvite?.includes(inv.token) ? (
                              <CheckIcon className="w-3 h-3" style={{ color: brandColor }} />
                            ) : (
                              <ClipboardIcon className="w-3 h-3 text-gray-400" />
                            )}
                          </button>
                        )}
                        <button
                          onClick={() => deleteInviteMut.mutate(inv.id)}
                          className="p-1 hover:bg-red-50 rounded"
                          title="Delete"
                        >
                          <TrashIcon className="w-3 h-3 text-gray-400 hover:text-red-500" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Active Users */}
      <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-gray-900">
            Active Users ({activeUsers.length})
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-6 py-3 font-medium text-gray-500">User</th>
                <th className="text-left px-6 py-3 font-medium text-gray-500">Role</th>
                <th className="text-left px-6 py-3 font-medium text-gray-500">Joined</th>
                <th className="text-right px-6 py-3 font-medium text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {activeUsers.map((u) => (
                <tr key={u.id} className="hover:bg-gray-50">
                  <td className="px-6 py-3">
                    <div>
                      <p className="font-medium text-gray-900">{u.full_name || u.username}</p>
                      <p className="text-xs text-gray-500">{u.email}</p>
                    </div>
                  </td>
                  <td className="px-6 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        u.is_admin ? "text-white" : "bg-gray-100 text-gray-700"
                      }`}
                      style={u.is_admin ? { backgroundColor: brandColor } : {}}
                    >
                      {u.is_admin ? "Admin" : "User"}
                    </span>
                  </td>
                  <td className="px-6 py-3 text-xs text-gray-500">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {u.id !== user.id ? (
                        <>
                          <button
                            onClick={() => toggleAdminMut.mutate(u.id)}
                            className="px-2.5 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-100 text-gray-700"
                          >
                            {u.is_admin ? "Remove Admin" : "Make Admin"}
                          </button>
                          <button
                            onClick={() => toggleActiveMut.mutate(u.id)}
                            className="px-2.5 py-1 text-xs border border-red-300 text-red-600 rounded-lg hover:bg-red-50"
                          >
                            Disable
                          </button>
                        </>
                      ) : (
                        <span className="text-xs text-gray-400 italic">You</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
        </div>
      )}
    </div>
  );
}
