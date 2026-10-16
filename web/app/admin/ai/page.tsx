"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteDeploymentKey, getAdminAi, putAdminAiSettings, putDeploymentKey, testDeploymentKey, type AdminAi,
} from "@/lib/api";
import { useAuth } from "@/app/contexts/AuthContext";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import KeyRow from "@/components/ai/KeyRow";
import { FEATURE_LABELS } from "@/components/ai/labels";

const usd = (n: number) => `$${n.toFixed(2)}`;

export default function AdminAiPage() {
  const { user } = useAuth();
  const municipality = useMunicipality();
  const brand = municipality.branding.theme_color || "#385854";
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["admin-ai"], queryFn: getAdminAi, enabled: !!user?.is_admin });
  const [draft, setDraft] = useState<AdminAi | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { if (data) setDraft(structuredClone(data)); }, [data]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-ai"] });

  if (!user?.is_admin) return <div className="p-8 text-sm text-gray-600">Admins only.</div>;
  if (error) return <div className="p-8 text-sm text-red-600">Couldn&apos;t load AI settings: {String(error)}</div>;
  if (!data || !draft) return <div className="p-8 text-sm text-gray-500">Loading…</div>;

  const llmProviders = data.providers.filter(p => p.kinds.includes("llm"));
  const save = async () => {
    const res = await putAdminAiSettings({
      routes: draft.routes, caps: draft.caps,
      sponsored: draft.features.filter(f => f.sponsored).map(f => f.feature),
    });
    qc.setQueryData(["admin-ai"], res);
    setSaved("Saved");
    setTimeout(() => setSaved(null), 2000);
  };
  const dirty = JSON.stringify({ r: draft.routes, c: draft.caps, f: draft.features }) !==
                JSON.stringify({ r: data.routes, c: data.caps, f: data.features });

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <Link href="/admin" className="text-xs text-gray-500 hover:underline">← Admin</Link>
          <h1 className="text-2xl font-bold text-gray-900">AI providers &amp; spending</h1>
          <p className="mt-1 text-sm text-gray-600">
            Keys the site uses, which model does each job, and which features {municipality.short_name || "the site"} pays for.
            Anything not paid for here still works for people who add their own key in Settings.
          </p>
        </div>
        <div className="text-right text-sm">
          <div className="text-gray-500">Spent this month</div>
          <div className="text-xl font-semibold text-gray-900">{usd(data.spend_this_month.deployment)}
            <span className="text-sm font-normal text-gray-500"> / {usd(data.caps.deployment_monthly_usd)}</span></div>
        </div>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
        <h2 className="text-base font-semibold text-gray-900">Site keys</h2>
        <p className="mt-1 text-sm text-gray-600">Stored encrypted; only the last four characters are ever shown. A key set here overrides the server environment.</p>
        <div className="mt-2 divide-y divide-gray-100">
          {data.providers.map(p => (
            <KeyRow key={p.id} p={p} brandColor={brand} envNote
                    onSave={async k => { const r = await putDeploymentKey(p.id, k); refresh(); return r; }}
                    onTest={async () => { const r = await testDeploymentKey(p.id); refresh(); return r; }}
                    onDelete={async () => { await deleteDeploymentKey(p.id); refresh(); }} />
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
        <h2 className="text-base font-semibold text-gray-900">Model for each job</h2>
        <div className="mt-3 space-y-2">
          {Object.entries(data.roles).map(([role, desc]) => {
            const r = draft.routes[role];
            return (
              <div key={role} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <span className="text-sm text-gray-800">{desc}</span>
                <select className="rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                        value={`${r.provider}:${r.model}`}
                        onChange={e => {
                          const [provider, model] = e.target.value.split(":");
                          setDraft({ ...draft, routes: { ...draft.routes, [role]: { provider, model } } });
                        }}>
                  {llmProviders.flatMap(p => p.models.map(m => (
                    <option key={`${p.id}:${m}`} value={`${p.id}:${m}`}>
                      {p.label.split(" (")[0]} · {m}{!p.has_key && !p.from_env ? " (no key)" : ""}
                    </option>
                  )))}
                </select>
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
        <h2 className="text-base font-semibold text-gray-900">What the site pays for</h2>
        <p className="mt-1 text-sm text-gray-600">
          Background jobs build the shared document library. Interactive features run per person; leave them off to make visitors bring their own key.
        </p>
        <div className="mt-3 grid gap-1 sm:grid-cols-2">
          {draft.features.map((f, i) => (
            <label key={f.feature} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-gray-50">
              <input type="checkbox" checked={f.sponsored} style={{ accentColor: brand }}
                     onChange={e => {
                       const features = [...draft.features];
                       features[i] = { ...f, sponsored: e.target.checked };
                       setDraft({ ...draft, features });
                     }} />
              <span className="text-gray-800">{FEATURE_LABELS[f.feature] || f.feature}</span>
              <span className="ml-auto text-[11px] text-gray-400">{f.interactive ? "per person" : "background"}</span>
            </label>
          ))}
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {([["deployment_monthly_usd", "Monthly budget for the whole site"],
             ["user_monthly_usd", "Free allowance per person per month"]] as const).map(([k, label]) => (
            <label key={k} className="text-sm text-gray-800">
              {label}
              <div className="mt-1 flex items-center gap-1">
                <span className="text-gray-500">$</span>
                <input type="number" min={0} step={1} value={draft.caps[k]}
                       onChange={e => setDraft({ ...draft, caps: { ...draft.caps, [k]: Number(e.target.value) } })}
                       className="w-28 rounded-md border border-gray-300 px-2 py-1.5 text-sm" />
              </div>
            </label>
          ))}
        </div>
      </section>

      {(data.spend_this_month.by_feature.length > 0 || data.spend_this_month.by_user.length > 0) && (
        <section className="grid gap-4 rounded-xl border border-gray-200 bg-white p-4 md:grid-cols-2 md:p-6">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">By feature</h3>
            {data.spend_this_month.by_feature.map(r => (
              <div key={r.source} className="flex justify-between text-sm text-gray-700">
                <span>{FEATURE_LABELS[r.source] || r.source}</span><span>{usd(r.usd)} · {r.calls}</span>
              </div>
            ))}
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-900">By person (site-paid)</h3>
            {data.spend_this_month.by_user.map(r => (
              <div key={r.user} className="flex justify-between text-sm text-gray-700">
                <span className="truncate">{r.user}</span><span>{usd(r.usd)} · {r.calls}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="sticky bottom-4 flex justify-end">
        <button onClick={save} disabled={!dirty}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white shadow disabled:opacity-40"
                style={{ backgroundColor: brand }}>
          {saved || (dirty ? "Save changes" : "No changes")}
        </button>
      </div>
    </div>
  );
}
