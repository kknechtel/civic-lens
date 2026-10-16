'use client';

/**
 * AH-branded primitives Claude can use inside `propose_react_component`
 * TSX. Hand-written + trusted; Claude only invokes them, never defines
 * new ones. Each component is also matched by a Tier-2 PPTX rasterizer
 * in `api/services/document_builder.py` so PPTX/DOCX exports look right
 * even without headless Chromium.
 *
 * Visual language:
 *   - White surfaces, AH teal #385854 accents
 *   - Bold sans for titles, system sans for body
 *   - Thin teal underlines on headings (matches the deck section style)
 */
import React from 'react';
import {
  TrendingUp, TrendingDown, AlertCircle, AlertTriangle, CheckCircle2, XCircle,
} from 'lucide-react';

const BRAND = '#385854';

// ── KPICard ─────────────────────────────────────────────────────────────
// Single stat block. Wrap several in a flex row for a stat strip.

interface KPICardProps {
  label: string;
  value: string | number;
  delta?: string;
  trend?: 'up' | 'down' | 'flat';
  caption?: string;
  /** legacy alias — older AI prompts emit `sub` instead of `caption`. */
  sub?: string;
}

export function KPICard({ label, value, delta, trend, caption, sub }: KPICardProps) {
  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : null;
  const trendColor = trend === 'up' ? 'text-emerald-700' : trend === 'down' ? 'text-red-700' : 'text-gray-500';
  const helper = caption || sub;
  return (
    <div className="flex-1 min-w-[140px] rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-[11px] uppercase tracking-wide text-gray-500 font-medium mb-1">{label}</div>
      <div className="text-2xl font-semibold text-gray-900">{value}</div>
      {(delta || trend) && (
        <div className={`mt-1.5 flex items-center gap-1 text-xs ${trendColor}`}>
          {TrendIcon && <TrendIcon className="w-3.5 h-3.5" />}
          {delta && <span className="font-medium">{delta}</span>}
        </div>
      )}
      {helper && <div className="mt-1 text-[11px] text-gray-500">{helper}</div>}
    </div>
  );
}

// ── Callout ─────────────────────────────────────────────────────────────
// Branded info / warning / risk / note box.

interface CalloutProps {
  kind?: 'info' | 'insight' | 'warn' | 'warning' | 'success' | 'risk' | 'note';
  title?: string;
  children: React.ReactNode;
}

export function Callout({ kind = 'info', title, children }: CalloutProps) {
  // Tolerate both AH ('info'/'warn'/'success') and bank-processor
  // ('insight'/'warning'/'risk'/'note') vocabularies — same component
  // is fed by both AI prompts during the migration.
  const k = kind === 'warn' ? 'warning'
    : kind === 'success' ? 'insight'
    : kind === 'info' ? 'note'
    : kind;
  const styles: Record<string, { bg: string; border: string; text: string; Icon: React.ComponentType<{ className?: string }> }> = {
    insight: { bg: 'bg-teal-50',    border: 'border-teal-200',    text: 'text-teal-900',    Icon: CheckCircle2 },
    warning: { bg: 'bg-amber-50',   border: 'border-amber-200',   text: 'text-amber-900',   Icon: AlertTriangle },
    risk:    { bg: 'bg-red-50',     border: 'border-red-200',     text: 'text-red-900',     Icon: XCircle },
    note:    { bg: 'bg-gray-50',    border: 'border-gray-200',    text: 'text-gray-800',    Icon: AlertCircle },
  };
  const s = styles[k] || styles.note;
  return (
    <div className={`rounded-lg border ${s.border} ${s.bg} ${s.text} p-3 my-2`}>
      <div className="flex items-start gap-2">
        <s.Icon className="w-4 h-4 mt-0.5 shrink-0" />
        <div className="text-sm">
          {title && <div className="font-semibold mb-0.5">{title}</div>}
          <div className="leading-relaxed">{children}</div>
        </div>
      </div>
    </div>
  );
}

// ── Stat ────────────────────────────────────────────────────────────────
// Tiny inline label/value pair — for grid-of-numbers layouts.

export function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-sm font-semibold text-gray-900">{value}</div>
    </div>
  );
}

// ── Section ─────────────────────────────────────────────────────────────
// Lightweight wrapper with a teal-accented heading for grouping content.

export function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      {title && <h3 className="text-base font-semibold" style={{ color: BRAND }}>{title}</h3>}
      <div>{children}</div>
    </section>
  );
}

// ── Timeline ────────────────────────────────────────────────────────────
// Horizontal row of dated events (budget cycles, school-year milestones,
// council meetings, RFP windows).

interface TimelineEvent {
  date: string;
  label: string;
  detail?: string;
}

export function Timeline({ events = [] }: { events: TimelineEvent[] }) {
  // Defensive: tolerate null / missing fields.
  const safe = (Array.isArray(events) ? events : []).map((e: TimelineEvent & { title?: string }) => ({
    date: String(e?.date || ''),
    label: String(e?.label || e?.title || ''),
    detail: e?.detail ? String(e.detail) : undefined,
  })).filter(e => e.date || e.label);

  if (!safe.length) {
    return (
      <div className="my-3 p-4 border border-dashed border-gray-300 rounded text-xs text-gray-500 italic text-center">
        No timeline events supplied
      </div>
    );
  }
  return (
    <div className="my-4">
      <div className="flex items-stretch gap-0">
        {safe.map((e, i) => (
          <React.Fragment key={i}>
            <div className="flex-1 min-w-0 text-center px-2">
              <div className="text-xs uppercase tracking-wide font-semibold" style={{ color: BRAND }}>
                {e.date || ' '}
              </div>
              <div className="w-3 h-3 rounded-full mx-auto my-2" style={{ backgroundColor: BRAND }} />
              <div className="text-sm font-medium text-gray-900 leading-snug">
                {e.label || e.date}
              </div>
              {e.detail && <div className="text-xs text-gray-600 mt-1 leading-snug">{e.detail}</div>}
            </div>
            {i < safe.length - 1 && (
              <div className="self-center h-px w-6 shrink-0 mt-[-3rem]" style={{ backgroundColor: `${BRAND}80` }} />
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ── RiskMatrix ──────────────────────────────────────────────────────────
// 3×3 likelihood × impact grid colored by severity.

interface RiskCell {
  likelihood: 'low' | 'med' | 'high';
  impact: 'low' | 'med' | 'high';
  label: string;
}

export function RiskMatrix({ risks = [] }: { risks: RiskCell[] }) {
  const norm = (v: unknown): 'low' | 'med' | 'high' => {
    const s = String(v || '').trim().toLowerCase();
    if (s === 'high' || s === 'h') return 'high';
    if (s === 'low' || s === 'l') return 'low';
    return 'med';
  };
  const safe: RiskCell[] = (Array.isArray(risks) ? risks : []).map((r: Partial<RiskCell>) => ({
    likelihood: norm(r?.likelihood),
    impact: norm(r?.impact),
    label: String(r?.label || '(unlabeled)'),
  }));
  if (!safe.length) {
    return (
      <div className="my-3 p-4 border border-dashed border-gray-300 rounded text-xs text-gray-500 italic text-center">
        No risks supplied to RiskMatrix
      </div>
    );
  }

  const cellAt = (l: string, i: string) => safe.filter(r => r.likelihood === l && r.impact === i);
  const zone = (l: string, i: string): string => {
    const rank = (v: string) => v === 'low' ? 1 : v === 'med' ? 2 : 3;
    const score = rank(l) + rank(i);
    if (score >= 5) return 'bg-red-100 text-red-900 border-red-300';
    if (score >= 4) return 'bg-amber-100 text-amber-900 border-amber-300';
    return 'bg-emerald-50 text-emerald-900 border-emerald-300';
  };
  const impacts: Array<'high' | 'med' | 'low'> = ['high', 'med', 'low'];
  const liks: Array<'low' | 'med' | 'high'> = ['low', 'med', 'high'];
  return (
    <div className="my-4">
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: 'minmax(80px, auto) repeat(3, minmax(140px, 1fr))' }}
      >
        <div />
        {liks.map(l => (
          <div key={l} className="text-xs font-semibold uppercase tracking-wide text-gray-600 text-center pb-1.5 border-b border-gray-300">
            {l} likelihood
          </div>
        ))}
        {impacts.map(i => (
          <React.Fragment key={i}>
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-600 self-center pr-2 text-right border-r border-gray-300">
              {i} impact
            </div>
            {liks.map(l => {
              const items = cellAt(l, i);
              return (
                <div key={l} className={`min-h-[80px] border rounded-md p-2 text-xs leading-snug space-y-1 ${zone(l, i)}`}>
                  {items.length > 0
                    ? items.map((r, k) => <div key={k} className="font-medium">• {r.label}</div>)
                    : <div className="text-gray-400 italic text-[10px]">—</div>}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ── ComparisonRow ───────────────────────────────────────────────────────
// Two-column compare (e.g., FY2023 vs FY2024, pre/post-consolidation,
// borough vs district).

interface ComparisonRowProps {
  leftLabel: string;
  leftValue: string | number;
  rightLabel: string;
  rightValue: string | number;
  delta?: string;
}

export function ComparisonRow({ leftLabel, leftValue, rightLabel, rightValue, delta }: ComparisonRowProps) {
  return (
    <div className="grid grid-cols-2 gap-3 my-2">
      <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
        <div className="text-[11px] uppercase tracking-wide text-gray-500">{leftLabel}</div>
        <div className="text-xl font-bold text-gray-900 mt-1">{leftValue}</div>
      </div>
      <div className="border rounded-lg p-3" style={{ borderColor: `${BRAND}33`, backgroundColor: `${BRAND}0c` }}>
        <div className="text-[11px] uppercase tracking-wide" style={{ color: BRAND }}>{rightLabel}</div>
        <div className="text-xl font-bold mt-1" style={{ color: '#1f3735' }}>{rightValue}</div>
        {delta && <div className="text-xs mt-1" style={{ color: BRAND }}>{delta}</div>}
      </div>
    </div>
  );
}

// ── Cite ────────────────────────────────────────────────────────────────
// Inline clickable citation chip for use INSIDE React component JSX.
// Markdown narrative bodies handle `[source: filename]` automatically,
// but that rewrite doesn't run on string literals inside a React
// component — embedding `[source: x]` as text would render literally.
// Use this primitive in JSX instead. Clicking dispatches the same
// `ah:open-citation` window event the markdown pills emit, so the
// side-panel CitationPreview opens whether the component is hosted in
// the editor or the public viewer.

interface CiteProps {
  filename: string;
  label?: string;
}

export function Cite({ filename, label }: CiteProps) {
  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent('ah:open-citation', {
      detail: { kind: 'doc', filename },
    }));
  };
  return (
    <a
      href={`ah://cite/${encodeURIComponent(filename)}`}
      onClick={onClick}
      className="inline-flex items-center px-1.5 py-0.5 mx-0.5 text-[11px] font-medium rounded border hover:opacity-90 cursor-pointer no-underline"
      style={{ backgroundColor: `${BRAND}10`, color: BRAND, borderColor: `${BRAND}30` }}
    >
      📄 {label || filename}
    </a>
  );
}

// ── PartiesGrid ─────────────────────────────────────────────────────────
// Labeled cards for stakeholders (Borough / School Board / DOE / etc.).

interface Party {
  role: string;
  members: string[];
}

export function PartiesGrid({ parties = [] }: { parties: Party[] }) {
  if (!parties.length) return null;
  return (
    <div
      className="grid gap-4 my-4"
      style={{
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        maxWidth: '100%',
      }}
    >
      {parties.map((p, i) => (
        <div key={i} className="border border-gray-200 rounded-lg p-4 bg-white shadow-sm flex flex-col">
          <div className="font-semibold border-b-2 pb-1.5 mb-2.5 text-base" style={{ color: BRAND, borderColor: `${BRAND}80` }}>
            {p.role}
          </div>
          {p.members && p.members.length > 0 ? (
            <ul className="space-y-1 text-sm text-gray-800 list-disc list-outside ml-4">
              {p.members.map((m, k) => <li key={k} className="leading-snug">{m}</li>)}
            </ul>
          ) : (
            <div className="text-sm text-gray-400 italic">No members listed</div>
          )}
        </div>
      ))}
    </div>
  );
}

export const AH_BRAND = BRAND;
