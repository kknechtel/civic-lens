'use client';

/**
 * MarkdownWithCharts — markdown renderer that also expands inline
 * ```chart\n{spec}\n``` fenced blocks into live Recharts visualizations.
 *
 * Replaces the older Chart.js + dangerouslySetInnerHTML approach (which
 * had race conditions with React re-renders that left charts blank
 * after streaming completed). Each segment is its own React tree, so
 * charts re-render naturally on prop change — no canvas re-attach race,
 * no fallback needed.
 *
 * Supports two spec formats so existing chats/decks keep working:
 *   1. Recharts (preferred): `{ type, title?, data: [...], xKey, yKey | yKeys, colors? }`
 *   2. Chart.js (legacy):     `{ type, data: { labels, datasets: [{ label, data }] } }`
 * Chart.js specs are normalized to Recharts shape on the fly.
 */
import React, { useMemo } from 'react';
import dynamic from 'next/dynamic';
import {
    BarChart, Bar, LineChart, Line, AreaChart, Area, PieChart, Pie, Cell,
    XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import type { FactCheckResultItem } from './MarkdownRenderer';

// Lazy: MarkdownRenderer pulls marked + highlight.js + DOMPurify (heavy).
// Loading dynamically keeps the deck shell light.
const MarkdownRenderer = dynamic(() => import('./MarkdownRenderer'), { ssr: false });

const AH_PALETTE = [
    '#385854', '#6A9B95', '#B2D4D0', '#CCA43B',
    '#8A6E3E', '#2F2F2F', '#D26A4F', '#4F7AD2',
];

interface ChartSpec {
    type: 'bar' | 'line' | 'pie' | 'area' | 'stacked_bar';
    title?: string;
    data: Record<string, unknown>[];
    xKey: string;
    yKey?: string;
    yKeys?: string[];
    colors?: string[];
}

interface ChartJsSpec {
    type?: string;
    data?: {
        labels?: (string | number)[];
        datasets?: Array<{
            label?: string;
            data?: number[];
            backgroundColor?: string | string[];
            borderColor?: string | string[];
        }>;
    };
    options?: { plugins?: { title?: { text?: string } } };
}

const CHART_FENCE = /```chart\s*\n([\s\S]*?)\n```/g;

interface Segment {
    kind: 'md' | 'chart' | 'chart_error';
    text?: string;
    spec?: ChartSpec;
    error?: string;
}

/** Convert a Chart.js spec `{ data: { labels, datasets } }` into the
 *  Recharts shape `{ data: [{x, label1, label2}], xKey, yKeys }`. */
function normalizeChartJsSpec(raw: ChartJsSpec): ChartSpec | null {
    const labels = raw?.data?.labels;
    const datasets = raw?.data?.datasets;
    if (!Array.isArray(labels) || !Array.isArray(datasets) || datasets.length === 0) return null;

    const xKey = '__x';
    const yKeys = datasets.map((d, i) => (d.label || `Series ${i + 1}`));
    const colors = datasets
        .map(d => (Array.isArray(d.backgroundColor) ? d.backgroundColor[0] : d.backgroundColor) ||
                  (Array.isArray(d.borderColor) ? d.borderColor[0] : d.borderColor))
        .filter(Boolean) as string[];

    const data = labels.map((lbl, i) => {
        const row: Record<string, unknown> = { [xKey]: lbl };
        datasets.forEach((d, j) => {
            row[yKeys[j]] = d.data?.[i] ?? null;
        });
        return row;
    });

    const title = raw?.options?.plugins?.title?.text;
    const allowed: ChartSpec['type'][] = ['bar', 'line', 'pie', 'area', 'stacked_bar'];
    const t = (raw.type || 'bar') as ChartSpec['type'];
    return {
        type: allowed.includes(t) ? t : 'bar',
        title: title || undefined,
        data,
        xKey,
        yKeys,
        colors: colors.length ? colors : undefined,
    };
}

/** Parse a fenced chart spec — accepts either Recharts or Chart.js shape. */
function parseChartSpec(json: string): { spec?: ChartSpec; error?: string } {
    try {
        const raw = JSON.parse(json);
        if (raw && Array.isArray(raw.data) && typeof raw.xKey === 'string') {
            return { spec: raw as ChartSpec };
        }
        if (raw && raw.data && Array.isArray(raw.data.labels) && Array.isArray(raw.data.datasets)) {
            const normalized = normalizeChartJsSpec(raw as ChartJsSpec);
            if (normalized) return { spec: normalized };
            return { error: 'chart spec has empty labels/datasets' };
        }
        return { error: 'chart spec missing data[] or xKey' };
    } catch (e) {
        return { error: (e as Error).message };
    }
}

function splitOnChartFences(content: string): Segment[] {
    const out: Segment[] = [];
    let lastIdx = 0;
    CHART_FENCE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = CHART_FENCE.exec(content)) !== null) {
        if (m.index > lastIdx) {
            out.push({ kind: 'md', text: content.slice(lastIdx, m.index) });
        }
        const json = (m[1] || '').trim();
        const { spec, error } = parseChartSpec(json);
        if (spec) out.push({ kind: 'chart', spec });
        else out.push({ kind: 'chart_error', error });
        lastIdx = m.index + m[0].length;
    }
    if (lastIdx < content.length) {
        out.push({ kind: 'md', text: content.slice(lastIdx) });
    }
    if (out.length === 0) {
        out.push({ kind: 'md', text: content });
    }
    return out;
}

function ChartFromSpec({ spec }: { spec: ChartSpec }) {
    const colors = spec.colors && spec.colors.length ? spec.colors : AH_PALETTE;
    const yKeys = spec.yKeys && spec.yKeys.length
        ? spec.yKeys
        : (spec.yKey ? [spec.yKey] : []);

    if (!spec.data.length || (spec.type !== 'pie' && yKeys.length === 0)) {
        return (
            <div className="text-xs italic text-gray-500 p-3 border border-gray-200 rounded bg-gray-50 my-3">
                Chart unavailable — spec missing data or y-keys.
            </div>
        );
    }

    return (
        <div className="my-3 bg-white rounded-lg border border-gray-200 overflow-hidden">
            {spec.title && (
                <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 text-xs font-semibold text-gray-700">
                    {spec.title}
                </div>
            )}
            <div className="p-2" style={{ height: 280 }}>
                <ResponsiveContainer width="100%" height="100%">
                    {spec.type === 'line' ? (
                        <LineChart data={spec.data} margin={{ left: 0, right: 8, top: 8, bottom: 8 }}>
                            <CartesianGrid stroke="#f1f5f4" strokeDasharray="2 4" />
                            <XAxis dataKey={spec.xKey} tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} />
                            <Tooltip />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            {yKeys.map((k, i) => (
                                <Line key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} strokeWidth={2} dot={{ r: 3 }} />
                            ))}
                        </LineChart>
                    ) : spec.type === 'area' ? (
                        <AreaChart data={spec.data} margin={{ left: 0, right: 8, top: 8, bottom: 8 }}>
                            <CartesianGrid stroke="#f1f5f4" strokeDasharray="2 4" />
                            <XAxis dataKey={spec.xKey} tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} />
                            <Tooltip />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            {yKeys.map((k, i) => (
                                <Area key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} fill={colors[i % colors.length]} fillOpacity={0.35} />
                            ))}
                        </AreaChart>
                    ) : spec.type === 'pie' ? (
                        <PieChart>
                            <Tooltip />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <Pie
                                data={spec.data}
                                dataKey={yKeys[0] || 'value'}
                                nameKey={spec.xKey}
                                cx="50%"
                                cy="50%"
                                outerRadius={100}
                                label
                            >
                                {spec.data.map((_, i) => (
                                    <Cell key={i} fill={colors[i % colors.length]} />
                                ))}
                            </Pie>
                        </PieChart>
                    ) : (
                        <BarChart data={spec.data} margin={{ left: 0, right: 8, top: 8, bottom: 8 }}>
                            <CartesianGrid stroke="#f1f5f4" strokeDasharray="2 4" />
                            <XAxis dataKey={spec.xKey} tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} />
                            <Tooltip />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            {yKeys.map((k, i) => (
                                <Bar
                                    key={k}
                                    dataKey={k}
                                    fill={colors[i % colors.length]}
                                    stackId={spec.type === 'stacked_bar' ? 'stack' : undefined}
                                    radius={[4, 4, 0, 0]}
                                />
                            ))}
                        </BarChart>
                    )}
                </ResponsiveContainer>
            </div>
        </div>
    );
}

interface Props {
    content: string;
    /** Forwarded to the inner MarkdownRenderer for AH-style citations. */
    onCitationClick?: (info: { filename: string }) => void;
    /** Forwarded — when set, citation pills get verdict badges. */
    factCheckResults?: FactCheckResultItem[];
    brandColor?: string;
}

export default function MarkdownWithCharts({ content, onCitationClick, factCheckResults, brandColor }: Props) {
    const segments = useMemo(() => splitOnChartFences(content || ''), [content]);

    return (
        <>
            {segments.map((seg, i) => {
                if (seg.kind === 'chart' && seg.spec) {
                    return <ChartFromSpec key={i} spec={seg.spec} />;
                }
                if (seg.kind === 'chart_error') {
                    return (
                        <div key={i} className="my-2 text-xs italic text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                            Chart spec failed to parse — {seg.error}
                        </div>
                    );
                }
                return seg.text
                    ? <MarkdownRenderer
                        key={i}
                        content={seg.text}
                        onCitationClick={onCitationClick}
                        factCheckResults={factCheckResults}
                        brandColor={brandColor}
                    />
                    : null;
            })}
        </>
    );
}
