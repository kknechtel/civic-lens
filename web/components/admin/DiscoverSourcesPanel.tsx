"use client";

import { useState } from "react";
import { discoverSources, type DiscoveryResult } from "@/lib/api";

/** Paste a government website; get draft sources for this deployment's jurisdiction.yaml. */
export default function DiscoverSourcesPanel({ brandColor }: { brandColor: string }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<DiscoveryResult | null>(null);
  const [copied, setCopied] = useState(false);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await discoverSources(url)); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
      <h2 className="text-base font-semibold text-gray-900">Find document sources on a website</h2>
      <p className="mt-1 text-sm text-gray-600">
        Crawls up to 40 pages and recognises records platforms (eCode360, CivicPlus, YouTube, calendars) and pages full of documents.
        Nothing is saved: copy the draft into the repo&apos;s <code className="text-xs">jurisdiction.yaml</code> and check it with
        <code className="text-xs"> check_profile --live</code>.
      </p>
      <form onSubmit={run} className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.yourtown.gov"
               className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm" />
        <button disabled={busy || !url.trim()} className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                style={{ backgroundColor: brandColor }}>{busy ? "Crawling… (up to a minute)" : "Discover"}</button>
      </form>
      {err && <div className="mt-2 text-sm text-red-600">{err}</div>}
      {res && (
        <div className="mt-4 space-y-3 text-sm">
          <div className="text-gray-600">
            {res.pages_fetched} pages read · platforms: {Object.keys(res.platforms).join(", ") || "none recognised"}
          </div>
          <ul className="divide-y divide-gray-100 rounded-md border border-gray-100">
            {res.sources.map(s => (
              <li key={s.id} className="px-3 py-2">
                <div className="font-medium text-gray-900">{s.label || s.id} <span className="text-xs font-normal text-gray-400">· {s.adapter}</span></div>
                {s.description && <div className="text-xs text-gray-600">{s.description}</div>}
              </li>
            ))}
          </ul>
          {res.notes.map(n => <div key={n} className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">{n}</div>)}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Draft for {res.jurisdiction}/jurisdiction.yaml</span>
              <button type="button" className="text-xs underline" style={{ color: brandColor }}
                      onClick={async () => { await navigator.clipboard.writeText(res.yaml); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                {copied ? "Copied" : "Copy YAML"}
              </button>
            </div>
            <pre className="max-h-80 overflow-auto rounded-md bg-gray-900 p-3 text-[11px] leading-relaxed text-gray-100">{res.yaml}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
