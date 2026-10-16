"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ShieldExclamationIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DocumentTextIcon,
  ArrowTopRightOnSquareIcon,
} from "@heroicons/react/24/outline";
import {
  getIntegrityAwards,
  getIntegrityFlags,
  getIntegritySummary,
  getIntegrityVendors,
  reviewIntegrityFlag,
  type FlagStatus,
  type IntegrityFlag,
} from "@/lib/api";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";

const RULE_LABELS: Record<string, string> = {
  near_threshold: "Just under bid threshold",
  split_purchases: "Possible split purchases",
  emergency_procurement: "Emergency procurement",
  noncompetitive_concentration: "No-bid concentration",
  single_bid: "Single bidder",
  duplicate_award: "Duplicate award",
  payout: "Large payout",
  payout_total: "Payouts by fiscal year",
  change_order_growth: "Change-order growth",
  official_surname_hire: "Hire shares official's surname",
  pay_to_play: "Pay-to-play match",
  oversight_findings: "Audit / oversight findings",
};

const SEV_STYLE: Record<string, string> = {
  high: "bg-red-100 text-red-800 border-red-200",
  medium: "bg-amber-100 text-amber-800 border-amber-200",
  low: "bg-gray-100 text-gray-700 border-gray-200",
};

const STATUS_STYLE: Record<FlagStatus, string> = {
  open: "text-gray-600",
  reviewing: "text-blue-700",
  confirmed: "text-red-700 font-semibold",
  dismissed: "text-gray-400 line-through",
};

const money = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

type Tab = "flags" | "vendors" | "awards";

export default function IntegrityPage() {
  const municipality = useMunicipality();
  const brand = municipality.branding.theme_color;
  const [tab, setTab] = useState<Tab>("flags");
  const [rule, setRule] = useState("");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [vendorFilter, setVendorFilter] = useState("");

  const { data: summary } = useQuery({ queryKey: ["integrity-summary"], queryFn: getIntegritySummary });
  const { data: flags, isLoading } = useQuery({
    queryKey: ["integrity-flags", rule, severity, status],
    queryFn: () => getIntegrityFlags({ rule, severity, status }),
  });

  const sev = summary?.open_by_severity || {};

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto">
      <div className="flex items-start gap-3 mb-2">
        <ShieldExclamationIcon className="w-8 h-8 flex-shrink-0" style={{ color: brand }} />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Integrity</h1>
          <p className="text-sm text-gray-500">
            Patterns in {municipality.short_name || "the"} public records that deserve a closer look — each with the
            documents behind it.
          </p>
        </div>
      </div>
      <p className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-md px-3 py-2 mb-6">
        These are automated <strong>red flags for human review</strong>, not findings of wrongdoing. Most have innocent
        explanations. Read the evidence, check the explanation, and mark each flag reviewed before relying on it.
      </p>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <Tile label="High" value={sev.high || 0} cls="text-red-700" />
        <Tile label="Medium" value={sev.medium || 0} cls="text-amber-700" />
        <Tile label="Low" value={sev.low || 0} cls="text-gray-700" />
        <Tile label="Spending actions" value={summary?.awards ?? 0} cls="text-gray-900" />
        <Tile label="Total authorized" value={money(summary?.award_total)} cls="text-gray-900" />
      </div>

      <div className="border-b border-gray-200 mb-4 flex gap-1">
        {(["flags", "vendors", "awards"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t ? "" : "border-transparent text-gray-500 hover:text-gray-700"}`}
            style={tab === t ? { borderColor: brand, color: brand } : {}}
          >
            {t === "flags" ? "Red flags" : t === "vendors" ? "Vendors" : "Spending actions"}
          </button>
        ))}
      </div>

      {tab === "flags" && (
        <>
          <div className="flex flex-wrap gap-2 mb-4">
            <select value={rule} onChange={(e) => setRule(e.target.value)} className="text-sm border rounded-md px-2 py-1.5">
              <option value="">All rules</option>
              {Object.entries(summary?.by_rule || {}).map(([r, n]) => (
                <option key={r} value={r}>{RULE_LABELS[r] || r} ({n})</option>
              ))}
            </select>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} className="text-sm border rounded-md px-2 py-1.5">
              <option value="">Any severity</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="text-sm border rounded-md px-2 py-1.5">
              <option value="">Any status</option>
              <option value="open">Open</option>
              <option value="reviewing">Reviewing</option>
              <option value="confirmed">Confirmed</option>
              <option value="dismissed">Dismissed</option>
            </select>
          </div>
          {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
          {flags && flags.length === 0 && (
            <p className="text-sm text-gray-500 py-8 text-center">No flags match. Run the integrity pipeline after documents are processed.</p>
          )}
          <div className="space-y-2">
            {flags?.map((f) => <FlagRow key={f.id} flag={f} brand={brand} />)}
          </div>
        </>
      )}

      {tab === "vendors" && <VendorsTab onPick={(v) => { setVendorFilter(v); setTab("awards"); }} />}
      {tab === "awards" && <AwardsTab vendor={vendorFilter} setVendor={setVendorFilter} />}
    </div>
  );
}

function Tile({ label, value, cls }: { label: string; value: number | string; cls: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <div className={`text-2xl font-semibold ${cls}`}>{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function FlagRow({ flag, brand }: { flag: IntegrityFlag; brand: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(flag.reviewer_note || "");
  const qc = useQueryClient();
  const review = useMutation({
    mutationFn: (s: FlagStatus) => reviewIntegrityFlag(flag.id, s, note || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrity-flags"] });
      qc.invalidateQueries({ queryKey: ["integrity-summary"] });
    },
  });

  return (
    <div className="bg-white border border-gray-200 rounded-lg">
      <button onClick={() => setOpen(!open)} className="w-full flex items-start gap-3 p-3 text-left hover:bg-gray-50">
        {open ? <ChevronDownIcon className="w-4 h-4 mt-1 text-gray-400" /> : <ChevronRightIcon className="w-4 h-4 mt-1 text-gray-400" />}
        <span className={`text-[11px] uppercase tracking-wide border rounded px-1.5 py-0.5 mt-0.5 ${SEV_STYLE[flag.severity]}`}>
          {flag.severity}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-900">{flag.title}</div>
          <div className="text-xs text-gray-500 mt-0.5">
            {RULE_LABELS[flag.rule] || flag.rule}
            {flag.amount != null && <> · {money(flag.amount)}</>}
            {" · "}{flag.evidence.length} source{flag.evidence.length === 1 ? "" : "s"}
            {" · "}<span className={STATUS_STYLE[flag.status]}>{flag.status}</span>
          </div>
        </div>
      </button>
      {open && (
        <div className="px-10 pb-4 space-y-3">
          {flag.explanation && <p className="text-sm text-gray-700">{flag.explanation}</p>}
          <div className="space-y-2">
            {flag.evidence.map((e, i) => (
              <div key={i} className="border-l-2 pl-3 py-1" style={{ borderColor: brand }}>
                <div className="flex items-center gap-2 text-xs text-gray-600">
                  <DocumentTextIcon className="w-3.5 h-3.5" />
                  {e.document_id ? (
                    <Link href={`/document-library?doc=${e.document_id}`} className="hover:underline font-medium">
                      {e.title || "Source document"}
                    </Link>
                  ) : (
                    <span className="font-medium">{e.title || "Source"}</span>
                  )}
                  {e.page != null && <span>p. {e.page}</span>}
                  {e.date && <span>{e.date}</span>}
                  {e.amount != null && <span>{money(e.amount)}</span>}
                  {e.url && (
                    <a href={e.url} target="_blank" rel="noopener" className="inline-flex items-center gap-0.5 hover:underline">
                      original <ArrowTopRightOnSquareIcon className="w-3 h-3" />
                    </a>
                  )}
                </div>
                {e.quote && <p className="text-xs text-gray-700 mt-1 italic">&ldquo;{e.quote}&rdquo;</p>}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-gray-100">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Reviewer note (what you checked, what you found)"
              className="flex-1 min-w-[200px] text-sm border rounded-md px-2 py-1.5"
            />
            {(["reviewing", "confirmed", "dismissed", "open"] as FlagStatus[]).map((s) => (
              <button
                key={s}
                disabled={review.isPending || flag.status === s}
                onClick={() => review.mutate(s)}
                className="text-xs border rounded-md px-2.5 py-1.5 hover:bg-gray-50 disabled:opacity-40 capitalize"
              >
                {s === "open" ? "Reopen" : s}
              </button>
            ))}
          </div>
          {flag.reviewed_by && (
            <p className="text-[11px] text-gray-400">Last reviewed by {flag.reviewed_by} {flag.reviewed_at?.slice(0, 10)}</p>
          )}
        </div>
      )}
    </div>
  );
}

function VendorsTab({ onPick }: { onPick: (vendor: string) => void }) {
  const { data } = useQuery({ queryKey: ["integrity-vendors"], queryFn: getIntegrityVendors });
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-gray-500 uppercase bg-gray-50">
          <tr>
            <th className="text-left px-3 py-2">Vendor</th>
            <th className="text-right px-3 py-2">Actions</th>
            <th className="text-right px-3 py-2">Total</th>
            <th className="text-left px-3 py-2 w-48">Without documented competition</th>
            <th className="text-right px-3 py-2">Flags</th>
            <th className="text-left px-3 py-2">Active</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((v) => (
            <tr key={v.vendor_norm} className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer" onClick={() => onPick(v.vendor)}>
              <td className="px-3 py-2 font-medium text-gray-900">{v.vendor}</td>
              <td className="px-3 py-2 text-right">{v.awards}</td>
              <td className="px-3 py-2 text-right">{money(v.total)}</td>
              <td className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-gray-100 rounded">
                    <div className="h-1.5 rounded bg-amber-500" style={{ width: `${Math.round((v.noncompetitive_share || 0) * 100)}%` }} />
                  </div>
                  <span className="text-xs text-gray-600 w-10 text-right">{Math.round((v.noncompetitive_share || 0) * 100)}%</span>
                </div>
              </td>
              <td className="px-3 py-2 text-right">{v.flags || ""}</td>
              <td className="px-3 py-2 text-xs text-gray-500">{v.first?.slice(0, 7)} – {v.last?.slice(0, 7)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AwardsTab({ vendor, setVendor }: { vendor: string; setVendor: (v: string) => void }) {
  const [procurement, setProcurement] = useState("");
  const { data } = useQuery({
    queryKey: ["integrity-awards", vendor, procurement],
    queryFn: () => getIntegrityAwards({ vendor, procurement }),
  });
  const total = useMemo(() => (data || []).reduce((s, a) => s + (a.amount || 0), 0), [data]);
  return (
    <>
      <div className="flex flex-wrap gap-2 mb-3 items-center">
        <input value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="Vendor"
          className="text-sm border rounded-md px-2 py-1.5" />
        <select value={procurement} onChange={(e) => setProcurement(e.target.value)} className="text-sm border rounded-md px-2 py-1.5">
          <option value="">Any procurement</option>
          {["competitive_bid", "rfp", "state_contract", "emergency", "professional_services", "no_bid", "sole_source", "unknown", "n/a"].map((p) => (
            <option key={p} value={p}>{p.replace(/_/g, " ")}</option>
          ))}
        </select>
        <span className="text-xs text-gray-500">{data?.length ?? 0} actions · {money(total)}</span>
      </div>
      <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 uppercase bg-gray-50">
            <tr>
              <th className="text-left px-3 py-2">Date</th>
              <th className="text-left px-3 py-2">Vendor / action</th>
              <th className="text-right px-3 py-2">Amount</th>
              <th className="text-left px-3 py-2">Procurement</th>
              <th className="text-left px-3 py-2">Source</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((a) => (
              <tr key={a.id} className="border-t border-gray-100 align-top">
                <td className="px-3 py-2 whitespace-nowrap text-gray-600">{a.meeting_date || "—"}</td>
                <td className="px-3 py-2">
                  <div className="font-medium text-gray-900">{a.vendor_name || <span className="text-gray-400">—</span>}</div>
                  <div className="text-xs text-gray-500">{a.title}</div>
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">{money(a.amount)}</td>
                <td className="px-3 py-2 text-xs">
                  {a.procurement.replace(/_/g, " ")}
                  {a.bids_received != null && <span className="text-gray-500"> · {a.bids_received} bid{a.bids_received === 1 ? "" : "s"}</span>}
                </td>
                <td className="px-3 py-2 text-xs">
                  <Link href={`/document-library?doc=${a.document_id}`} className="hover:underline">
                    item {a.item_no || "?"}{a.page ? `, p. ${a.page}` : ""}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
