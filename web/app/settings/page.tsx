"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteMyKey, getMyAi, putMyKey, putMyPrefs, testMyKey, type AiRoute } from "@/lib/api";
import { useAuth } from "@/app/contexts/AuthContext";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import KeyRow from "@/components/ai/KeyRow";
import { FEATURE_LABELS } from "@/components/ai/labels";


const usd = (n: number) => `$${n.toFixed(n < 1 ? 3 : 2)}`;

export default function SettingsPage() {
  const { user } = useAuth();
  const municipality = useMunicipality();
  const brand = municipality.branding.theme_color || "#385854";
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["my-ai"], queryFn: getMyAi, enabled: !!user });
  const refresh = () => qc.invalidateQueries({ queryKey: ["my-ai"] });

  if (!user) return <div className="p-8 text-sm text-gray-600">Sign in to manage your settings.</div>;
  if (isLoading) return <div className="p-8 text-sm text-gray-500">Loading…</div>;
  if (error || !data) return <div className="p-8 text-sm text-red-600">Couldn&apos;t load settings: {String(error)}</div>;

  const llms = data.providers.filter(p => p.kinds.includes("llm"));
  const myKeys = llms.filter(p => p.has_key && p.status !== "invalid");
  const setPref = async (role: string, route: AiRoute | null) => {
    await putMyPrefs({ ...data.prefs, [role]: route });
    refresh();
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-600">{user.email}</p>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
        <h2 className="text-base font-semibold text-gray-900">Your AI keys</h2>
        <p className="mt-1 text-sm text-gray-600">
          Add your own key to use AI features {municipality.short_name ? `${municipality.short_name} doesn't` : "the site doesn't"} pay for,
          or to use them without limits. Your key is used only for your requests, is stored encrypted, and is never shown again.
        </p>
        <div className="mt-2 divide-y divide-gray-100">
          {llms.map(p => (
            <KeyRow key={p.id} p={p} brandColor={brand}
                    onSave={async k => { const r = await putMyKey(p.id, k); refresh(); return r; }}
                    onTest={async () => { const r = await testMyKey(p.id); refresh(); return r; }}
                    onDelete={async () => { await deleteMyKey(p.id); refresh(); }} />
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
        <h2 className="text-base font-semibold text-gray-900">What you can use</h2>
        <table className="mt-3 w-full text-sm">
          <tbody className="divide-y divide-gray-100">
            {data.features.map(f => (
              <tr key={f.feature}>
                <td className="py-2 pr-3 text-gray-800">{FEATURE_LABELS[f.feature] || f.feature}</td>
                <td className="py-2 text-right">
                  {f.available
                    ? <span className="text-emerald-700">{f.via?.includes("your key") ? "On — your key" : `On — paid by ${municipality.short_name || "the site"}`}</span>
                    : <span className="text-gray-500">Needs your own key</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-gray-500">
          This month: {usd(data.spend_this_month.sponsored)} of your {usd(data.spend_this_month.sponsored_cap)} free allowance used
          {data.spend_this_month.own_keys > 0 && ` · ${usd(data.spend_this_month.own_keys)} on your own keys`}.
        </p>
      </section>

      {myKeys.length > 0 && (
        <section className="rounded-xl border border-gray-200 bg-white p-4 md:p-6">
          <h2 className="text-base font-semibold text-gray-900">Models for your key</h2>
          <p className="mt-1 text-sm text-gray-600">Applies only when your own key pays. Leave on default to use the site&apos;s choice.</p>
          <div className="mt-3 space-y-2">
            {Object.entries(data.roles).filter(([r]) => ["chat", "deep"].includes(r)).map(([role, desc]) => {
              const cur = data.prefs[role];
              const value = cur ? `${cur.provider}:${cur.model}` : "";
              return (
                <label key={role} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                  <span className="text-sm text-gray-800">{desc}</span>
                  <select value={value} className="rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                          onChange={e => {
                            const [provider, model] = e.target.value.split(":");
                            setPref(role, e.target.value ? { provider, model } : null);
                          }}>
                    <option value="">Default</option>
                    {myKeys.flatMap(p => p.models.map(m => (
                      <option key={`${p.id}:${m}`} value={`${p.id}:${m}`}>{p.label.split(" (")[0]} · {m}</option>
                    )))}
                  </select>
                </label>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
