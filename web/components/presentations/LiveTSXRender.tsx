'use client';

/**
 * LiveTSXRender — sandboxed live preview of AI-generated TSX.
 *
 * `react-live` runs the TSX inside a curated scope with no `window`,
 * `fetch`, `document`, `eval`, `localStorage`, etc. Any reference to
 * a forbidden identifier surfaces as a clean ReferenceError in
 * <LiveError /> rather than executing.
 *
 * The TSX can only enter the system through the authenticated editor
 * (Claude proposes it, the editor's user accepts), so the trust
 * boundary is the editor — not the public viewer. Public readers see
 * the rendered output but never an editor or the source.
 *
 * The `react-live` + `@babel/standalone` bundle is heavy (~600KB), so
 * callers should `dynamic(() => import('./LiveTSXRender'), { ssr: false })`.
 */
import React from 'react';
import { LiveProvider, LivePreview, LiveError, LiveEditor, LiveContext } from 'react-live';
import {
  TrendingUp, TrendingDown, AlertTriangle, CheckCircle2, XCircle,
  Calendar, DollarSign, Users, FileText, Globe, Sparkles,
  ChevronRight, ArrowRight, BarChart2,
} from 'lucide-react';
import {
  ResponsiveContainer, LineChart, BarChart, PieChart, AreaChart, RadarChart,
  Line, Bar, Pie, Area, Radar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, Cell, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from 'recharts';
import {
  KPICard, Callout, Stat, Section, Timeline, RiskMatrix, ComparisonRow,
  PartiesGrid, Cite, AH_BRAND,
} from './AHPrimitives';

interface Props {
  /** TSX source. May define `function Foo() {}` at top level — we auto-`render(<Foo />)`. */
  code: string;
  showEditor?: boolean;
  onChange?: (next: string) => void;
  framed?: boolean;
  /** Public viewer mode — no source toggle, no editor, no error noise. */
  readOnly?: boolean;
  onCompileError?: (error: string | null) => void;
  /** Structured data exposed to TSX as the `data` identifier. */
  data?: unknown;
}

function ErrorWatcher({ onError }: { onError: (error: string | null) => void }) {
  const ctx = React.useContext(LiveContext) as { error?: string | null } | null;
  React.useEffect(() => {
    const err = ctx?.error;
    onError(typeof err === 'string' && err.length > 0 ? err : null);
  }, [ctx, onError]);
  return null;
}

const Recharts = {
  ResponsiveContainer, LineChart, BarChart, PieChart, AreaChart, RadarChart,
  Line, Bar, Pie, Area, Radar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, Cell, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
};

const CURATED_SCOPE = {
  React,
  useState: React.useState, useMemo: React.useMemo,
  useCallback: React.useCallback, useEffect: React.useEffect,
  // Recharts (both bare names and namespace — defensive against model output style).
  Recharts,
  ResponsiveContainer, LineChart, BarChart, PieChart, AreaChart, RadarChart,
  Line, Bar, Pie, Area, Radar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, Cell, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  // Lucide icons.
  TrendingUp, TrendingDown, AlertTriangle, CheckCircle2, XCircle,
  Calendar, DollarSign, Users, FileText, Globe, Sparkles,
  ChevronRight, ArrowRight, BarChart2,
  // AH-branded primitives — preferred over raw divs so output matches
  // the rest of the deck. Keep this list in sync with the system prompt
  // that lists the components Claude is allowed to emit.
  KPICard, Callout, Stat, Section, Timeline, RiskMatrix, ComparisonRow,
  PartiesGrid,
  // Inline citation chip for use INSIDE JSX (string literals inside a
  // component don't get the markdown `[source: ...]` rewrite).
  Cite,
  // Brand color constant for inline styling.
  BRAND: AH_BRAND,
};

/**
 * Strip syntactic patterns Claude sometimes emits that break Babel's
 * preset-react in `noInline` mode. Curated scope is global, so
 * imports/exports never make sense.
 */
function sanitizeTsx(code: string): string {
  if (!code) return code;
  let out = code;
  out = out.replace(/^```(?:tsx?|jsx?|typescript|javascript)?\s*\n/, '');
  out = out.replace(/\n```\s*$/, '');
  // Drop import statements (curated scope is global).
  out = out.replace(/^\s*import\s+[\s\S]+?from\s+['"][^'"]+['"]\s*;?[ \t]*$/gm, '');
  out = out.replace(/^\s*import\s+['"][^'"]+['"]\s*;?[ \t]*$/gm, '');
  out = out.replace(/^[ \t]*['"]use client['"];?[ \t]*$/gm, '');
  // Hooks redeclarations conflict with react-live's noInline parameter list.
  out = out.replace(
    /^\s*const\s*\{\s*(?:useState|useMemo|useCallback|useEffect)\b[^}]*\}\s*=\s*React\s*;?[ \t]*$/gm, '');
  out = out.replace(
    /^\s*const\s+(useState|useMemo|useCallback|useEffect)\s*=\s*React\.\1\s*;?[ \t]*$/gm, '');
  out = out.replace(/^\s*export\s+default\s+/gm, '');
  out = out.replace(/^\s*export\s+/gm, '');
  out = out.replace(/^\s*interface\s+\w+\s*(?:extends\s+[\w<>,\s]+)?\{[^}]*\}\s*/gm, '');
  out = out.replace(/^\s*type\s+\w+\s*=\s*[^;\n]+;?\s*$/gm, '');
  out = out.replace(/(\([^()]*?)\s*:\s*[A-Z]\w*(?:<[^>]*>)?\s*(\))/g, '$1$2');
  out = out.replace(/(\{[^}]*\})\s*,\s*([\w])/g, (_m, g1, g2) => `${g1} ${g2}`);
  // Insert missing comma between consecutive object-literal properties on one line.
  out = out.replace(/(\})(\s+)([A-Za-z_$][\w$]*)(\s*:\s*[\{\[\"\'\d\-<])/g, '$1,$2$3$4');
  return out.trim();
}

function wrapForLive(code: string): string {
  const sanitized = sanitizeTsx(code);
  if (/render\s*\(/.test(sanitized)) return sanitized;
  const fnMatch = sanitized.match(/function\s+([A-Z][A-Za-z0-9_]*)\s*\(/);
  if (fnMatch) return `${sanitized}\n\nrender(<${fnMatch[1]} />);`;
  const trailing = sanitized.replace(/\s+$/, '');
  if (trailing.endsWith('/>') || trailing.endsWith('>')) return `render(${trailing});`;
  return sanitized;
}

export default function LiveTSXRender({
  code, showEditor, onChange, framed = true, readOnly = false, onCompileError, data,
}: Props) {
  const cleanCode = React.useMemo(() => wrapForLive(code), [code]);
  const [hasError, setHasError] = React.useState(false);
  const handleErr = React.useCallback((err: string | null) => {
    setHasError(!!err);
    onCompileError?.(err);
  }, [onCompileError]);
  const scope = React.useMemo(
    () => ({ ...CURATED_SCOPE, data: data ?? null }),
    [data],
  );

  return (
    <LiveProvider code={cleanCode} scope={scope} noInline enableTypeScript>
      <ErrorWatcher onError={handleErr} />
      <div className={framed ? 'rounded-lg border border-gray-200 bg-white p-3' : ''}>
        {/* Click delegate — re-route bare `<a href="ah://cite/...">` and any
            stray external links to the same side-panel events the markdown
            renderer uses. The model occasionally emits raw <a> tags inside
            its TSX instead of invoking <Cite>; without this they'd open in
            a new tab (or do nothing for the ah:// scheme). */}
        <div
          className="ah-live-preview"
          onClickCapture={(e) => {
            const a = (e.target as HTMLElement | null)?.closest('a');
            if (!a) return;
            const href = a.getAttribute('href') || '';
            const m = href.match(/^ah:\/\/cite\/(.+)$/);
            if (m) {
              e.preventDefault();
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent('ah:open-citation', {
                detail: { kind: 'doc', filename: decodeURIComponent(m[1]) },
              }));
              return;
            }
            // External http(s) — route to WebReferencePreview when mounted,
            // otherwise let the browser open the link normally.
            if (/^https?:\/\//i.test(href) && (window as { __ahWebRefPanelMounted?: boolean }).__ahWebRefPanelMounted) {
              e.preventDefault();
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent('ah:open-web-reference', {
                detail: { url: href, title: a.textContent?.trim() || href },
              }));
            }
          }}
        >
          <LivePreview />
        </div>
        {!readOnly && (
          <LiveError className="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 whitespace-pre-wrap font-mono" />
        )}
        {readOnly ? null : (showEditor || hasError) ? (
          <div className="mt-3 border-t border-gray-100 pt-3">
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
              {hasError ? 'TSX source (auto-opened — compile error above)' : 'TSX source (editable)'}
            </div>
            <div className="max-h-64 overflow-auto border border-gray-200 rounded text-xs">
              <LiveEditor onChange={onChange} />
            </div>
          </div>
        ) : (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] text-gray-500 hover:text-gray-800">View / edit source</summary>
            <div className="mt-2 max-h-64 overflow-auto border border-gray-200 rounded text-xs">
              <LiveEditor onChange={onChange} />
            </div>
          </details>
        )}
      </div>
    </LiveProvider>
  );
}
