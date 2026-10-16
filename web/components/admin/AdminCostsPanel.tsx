"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getUsageSummary, getUsageRows, type UsageSummary, type UsageRow } from "@/lib/api";

const brandColor = "#385854";

function fmtUsd(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl shadow border border-gray-200 bg-white p-4">
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className="text-sm text-gray-500">{label}</p>
      {sub && <p className="text-[11px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function DailySparkline({ daily }: { daily: UsageSummary["daily"] }) {
  // Simple SVG bar chart of cost per day. Width = 600, height = 60.
  if (!daily.length) return <p className="text-xs text-gray-400">No usage in this window.</p>;
  const max = Math.max(...daily.map((d) => d.cost), 0.0001);
  const W = 600;
  const H = 60;
  const barW = Math.max(2, W / daily.length - 2);
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block">
      {daily.map((d, i) => {
        const h = Math.max(1, (d.cost / max) * (H - 2));
        const x = i * (W / daily.length) + 1;
        return (
          <rect
            key={d.date}
            x={x}
            y={H - h}
            width={barW}
            height={h}
            fill={brandColor}
            opacity={0.85}
          >
            <title>{`${d.date}: ${fmtUsd(d.cost)} (${d.calls} calls)`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

export default function AdminCostsPanel() {
  const [days, setDays] = useState(30);
  const [drillSource, setDrillSource] = useState<string | null>(null);

  const { data: summary, isLoading } = useQuery({
    queryKey: ["admin-usage-summary", days],
    queryFn: () => getUsageSummary(days),
  });

  const { data: rows } = useQuery({
    queryKey: ["admin-usage-rows", days, drillSource],
    queryFn: () => getUsageRows({ days, source: drillSource || undefined, limit: 100 }),
    enabled: !!drillSource,
  });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-white rounded-xl shadow border border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-gray-900">LLM cost tracker</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Estimated based on per-million pricing. Includes chat, OCR,
            embeddings, deck AI, fact-check, OPRA, reports, and financial
            extraction. Batch CLI scripts are not yet instrumented.
          </p>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="text-xs border border-gray-300 rounded px-2 py-1.5"
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last 365 days</option>
        </select>
      </div>

      {isLoading || !summary ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <>
          {/* Totals */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Total cost" value={fmtUsd(summary.total_cost_usd)}
                      sub={`${summary.total_calls} calls`} />
            <StatCard label="Input tokens" value={fmtNum(summary.total_input_tokens)} />
            <StatCard label="Output tokens" value={fmtNum(summary.total_output_tokens)} />
            <StatCard label="Avg / call"
                      value={summary.total_calls
                        ? fmtUsd(summary.total_cost_usd / summary.total_calls)
                        : "$0"} />
          </div>

          {/* Daily chart */}
          <div className="bg-white rounded-xl shadow border border-gray-200 p-4">
            <p className="text-xs font-medium text-gray-500 mb-2">Cost per day</p>
            <DailySparkline daily={summary.daily} />
          </div>

          {/* Breakdown by source */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-900">By source</h3>
                {drillSource && (
                  <button
                    onClick={() => setDrillSource(null)}
                    className="text-[11px] text-gray-500 hover:text-gray-900"
                  >
                    Clear filter
                  </button>
                )}
              </div>
              {summary.by_source.length === 0 ? (
                <p className="px-4 py-3 text-xs text-gray-400">No data.</p>
              ) : (
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-100">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium text-gray-500">Source</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Calls</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Tokens (in/out)</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {summary.by_source.map((r) => (
                      <tr key={r.source} className={`hover:bg-gray-50 cursor-pointer ${
                        drillSource === r.source ? "bg-emerald-50" : ""
                      }`}
                          onClick={() => setDrillSource(drillSource === r.source ? null : r.source!)}>
                        <td className="px-4 py-2 text-gray-700 capitalize">{r.source}</td>
                        <td className="px-4 py-2 text-right text-gray-600">{r.calls}</td>
                        <td className="px-4 py-2 text-right text-gray-500">
                          {fmtNum(r.input_tokens)} / {fmtNum(r.output_tokens)}
                        </td>
                        <td className="px-4 py-2 text-right font-medium text-gray-900">{fmtUsd(r.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100">
                <h3 className="text-sm font-semibold text-gray-900">By model</h3>
              </div>
              {summary.by_model.length === 0 ? (
                <p className="px-4 py-3 text-xs text-gray-400">No data.</p>
              ) : (
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-100">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium text-gray-500">Model</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Calls</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Tokens (in/out)</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {summary.by_model.map((r) => (
                      <tr key={r.model} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-gray-700 truncate max-w-[12rem]" title={r.model}>{r.model}</td>
                        <td className="px-4 py-2 text-right text-gray-600">{r.calls}</td>
                        <td className="px-4 py-2 text-right text-gray-500">
                          {fmtNum(r.input_tokens)} / {fmtNum(r.output_tokens)}
                        </td>
                        <td className="px-4 py-2 text-right font-medium text-gray-900">{fmtUsd(r.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* By user */}
          <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900">By user (top 50)</h3>
            </div>
            {summary.by_user.length === 0 ? (
              <p className="px-4 py-3 text-xs text-gray-400">No data.</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="bg-gray-50 border-b border-gray-100">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">User</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-500">Calls</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-500">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {summary.by_user.map((r) => (
                    <tr key={`${r.user_id ?? "system"}`} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-700">{r.email}</td>
                      <td className="px-4 py-2 text-right text-gray-600">{r.calls}</td>
                      <td className="px-4 py-2 text-right font-medium text-gray-900">{fmtUsd(r.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Drill-down: raw rows for a selected source */}
          {drillSource && rows && (
            <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-900">
                  Recent {drillSource} calls
                </h3>
                <span className="text-[11px] text-gray-400">{rows.length} most recent</span>
              </div>
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-100 sticky top-0">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium text-gray-500">When</th>
                      <th className="text-left px-4 py-2 font-medium text-gray-500">User</th>
                      <th className="text-left px-4 py-2 font-medium text-gray-500">Model</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Tokens</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-500">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {rows.map((r: UsageRow) => (
                      <tr key={r.id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-gray-500 whitespace-nowrap">
                          {new Date(r.created_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 text-gray-700">{r.user_email || "(system)"}</td>
                        <td className="px-4 py-2 text-gray-600 truncate max-w-[14rem]" title={r.model}>{r.model}</td>
                        <td className="px-4 py-2 text-right text-gray-500">
                          {fmtNum(r.input_tokens)}/{fmtNum(r.output_tokens)}
                        </td>
                        <td className="px-4 py-2 text-right font-medium text-gray-900">{fmtUsd(r.estimated_cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
