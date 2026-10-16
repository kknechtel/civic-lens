"use client";

import { useState } from "react";
import type { AiProvider, KeyTestResult } from "@/lib/api";

/** One provider: status, add/replace, test, remove. The key is never shown again after saving. */
export default function KeyRow({ p, onSave, onTest, onDelete, brandColor, envNote }: {
  p: AiProvider;
  onSave: (key: string) => Promise<KeyTestResult>;
  onTest: () => Promise<KeyTestResult>;
  onDelete: () => Promise<unknown>;
  brandColor: string;
  envNote?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async (fn: () => Promise<KeyTestResult | unknown>) => {
    setBusy(true); setMsg(null);
    try {
      const r = (await fn()) as KeyTestResult | undefined;
      if (r && typeof r === "object" && "ok" in r) setMsg({ ok: r.ok, text: r.ok ? "Key works" : r.detail });
      setEditing(false); setValue("");
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  };

  const status = !p.has_key
    ? (envNote && p.from_env ? { t: "Set on the server (environment)", c: "text-gray-500" } : { t: "Not set", c: "text-gray-400" })
    : p.status === "ok" ? { t: `Working · •••• ${p.last4}`, c: "text-emerald-700" }
    : p.status === "invalid" ? { t: `Rejected · •••• ${p.last4}`, c: "text-red-600" }
    : { t: `Saved · •••• ${p.last4}`, c: "text-gray-600" };

  return (
    <div className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-900">{p.label}</div>
        <div className={`text-xs ${status.c}`}>{status.t}
          {p.status === "invalid" && p.status_detail ? ` — ${p.status_detail}` : ""}</div>
        {msg && <div className={`mt-0.5 text-xs ${msg.ok ? "text-emerald-700" : "text-red-600"}`}>{msg.text}</div>}
      </div>
      {editing ? (
        <form className="flex w-full gap-2 sm:w-auto" onSubmit={e => { e.preventDefault(); if (value.trim()) run(() => onSave(value.trim())); }}>
          <input autoFocus type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)}
                 placeholder={p.key_hint} className="min-w-0 flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm sm:w-64" />
          <button disabled={busy || !value.trim()} className="rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-50"
                  style={{ backgroundColor: brandColor }}>{busy ? "Checking…" : "Save"}</button>
          <button type="button" onClick={() => { setEditing(false); setValue(""); }}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-600">Cancel</button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <a href={p.docs} target="_blank" rel="noreferrer" className="px-2 py-1.5 text-xs text-gray-500 hover:underline">Get a key</a>
          <button onClick={() => setEditing(true)} className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
            {p.has_key ? "Replace" : "Add key"}
          </button>
          {p.has_key && <button disabled={busy} onClick={() => run(onTest)}
                                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">Test</button>}
          {p.has_key && <button disabled={busy} onClick={() => { if (confirm(`Remove the ${p.label} key?`)) run(onDelete); }}
                                className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50">Remove</button>}
        </div>
      )}
    </div>
  );
}
