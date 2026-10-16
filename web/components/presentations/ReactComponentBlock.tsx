'use client';

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import { Save, Code as CodeIcon } from 'lucide-react';
import type { DeckSection } from '@/lib/presentationsApi';

// react-live + babel/standalone is ~600KB — load on demand only.
const LiveTSXRender = dynamic(() => import('./LiveTSXRender'), {
  ssr: false,
  loading: () => <div className="text-xs text-gray-400 italic p-3">Loading sandbox…</div>,
});

interface Props {
  section: DeckSection & { tsx?: string; data?: unknown };
  editable: boolean;
  onSave: (patch: Partial<DeckSection> & { tsx?: string; data?: unknown }) => void;
  brandColor?: string;
}

export default function ReactComponentBlock({ section, editable, onSave, brandColor = '#385854' }: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(section.title || '');
  const [tsx, setTsx] = useState(section.tsx || '');

  const tsxValue = section.tsx || '';

  if (!editable || !editing) {
    return (
      <div className="space-y-2">
        {section.title && <h2 className="text-xl font-semibold text-gray-900">{section.title}</h2>}
        {tsxValue ? (
          <LiveTSXRender code={tsxValue} readOnly={!editable} data={section.data} framed={false} />
        ) : (
          <p className="text-sm text-gray-400 italic">(no component)</p>
        )}
        {editable && (
          <button onClick={() => { setEditing(true); setTitle(section.title || ''); setTsx(tsxValue); }}
            className="text-xs text-gray-500 hover:text-gray-800 underline flex items-center gap-1">
            <CodeIcon className="w-3 h-3" /> Edit TSX
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Section title"
        className="w-full text-xl font-semibold border-b border-gray-200 focus:outline-none focus:border-gray-400 pb-1"
      />
      <textarea
        value={tsx}
        onChange={(e) => setTsx(e.target.value)}
        placeholder="function MyChart() { return <div>...</div>; }"
        rows={Math.max(10, tsx.split('\n').length + 1)}
        className="w-full text-xs font-mono border border-gray-200 rounded p-3 focus:outline-none focus:border-gray-400"
      />
      <div className="text-[10px] text-gray-500">
        Available scope: React + hooks · Recharts (BarChart, LineChart, PieChart, …) · lucide icons (TrendingUp, …) ·
        AH primitives (KPICard, Callout, Stat, Section) · BRAND color constant. No window/fetch/document.
      </div>
      <div className="border border-gray-200 rounded p-2 bg-gray-50">
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Live preview</div>
        {tsx ? <LiveTSXRender code={tsx} framed={false} /> : <div className="text-xs text-gray-400 italic">empty</div>}
      </div>
      <div className="flex gap-2 text-xs">
        <button
          onClick={() => { onSave({ title, tsx }); setEditing(false); }}
          className="px-3 py-1.5 rounded text-white flex items-center gap-1"
          style={{ backgroundColor: brandColor }}
        ><Save className="w-3 h-3" /> Save</button>
        <button onClick={() => setEditing(false)}
          className="px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
      </div>
    </div>
  );
}
