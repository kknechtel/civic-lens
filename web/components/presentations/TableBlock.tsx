'use client';

import React, { useEffect, useState } from 'react';
import { Plus, Minus, Save, X } from 'lucide-react';
import type { DeckSection } from '@/lib/presentationsApi';

interface Props {
  section: DeckSection;
  editable: boolean;
  onSave: (patch: Partial<DeckSection>) => void;
  brandColor?: string;
}

export default function TableBlock({ section, editable, onSave, brandColor = '#385854' }: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(section.title || '');
  const [caption, setCaption] = useState(section.caption || '');
  const [headers, setHeaders] = useState<string[]>(section.headers || ['']);
  const [rows, setRows] = useState<string[][]>(section.rows || [['']]);

  useEffect(() => {
    setTitle(section.title || '');
    setCaption(section.caption || '');
    setHeaders(section.headers || ['']);
    setRows(section.rows || [['']]);
  }, [section.id]);

  const addCol = () => {
    setHeaders([...headers, '']);
    setRows(rows.map((r) => [...r, '']));
  };
  const removeCol = (idx: number) => {
    if (headers.length <= 1) return;
    setHeaders(headers.filter((_, i) => i !== idx));
    setRows(rows.map((r) => r.filter((_, i) => i !== idx)));
  };
  const addRow = () => setRows([...rows, headers.map(() => '')]);
  const removeRow = (idx: number) => setRows(rows.filter((_, i) => i !== idx));

  if (!editable || !editing) {
    return (
      <div className="space-y-2">
        {section.title && <h2 className="text-xl font-semibold text-gray-900">{section.title}</h2>}
        {section.headers && section.headers.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  {section.headers.map((h, i) => (
                    <th key={i} className="text-left px-3 py-2 font-semibold text-gray-700">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(section.rows || []).map((r, ri) => (
                  <tr key={ri} className="border-b border-gray-100">
                    {r.map((cell, ci) => (
                      <td key={ci} className="px-3 py-2 align-top">{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400 italic">(no data)</p>
        )}
        {section.caption && <p className="text-xs text-gray-500 italic">{section.caption}</p>}
        {editable && (
          <button onClick={() => setEditing(true)} className="text-xs text-gray-500 hover:text-gray-800 underline">Edit</button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Table title"
        className="w-full text-xl font-semibold border-b border-gray-200 focus:outline-none focus:border-gray-400 pb-1"
      />
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs border-collapse">
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i} className="px-1">
                  <div className="flex items-center gap-1">
                    <input
                      value={h}
                      onChange={(e) => setHeaders(headers.map((x, ix) => ix === i ? e.target.value : x))}
                      placeholder={`Col ${i + 1}`}
                      className="w-full text-xs font-semibold border border-gray-200 rounded px-2 py-1"
                    />
                    <button onClick={() => removeCol(i)} className="text-gray-400 hover:text-red-500"><X className="w-3 h-3" /></button>
                  </div>
                </th>
              ))}
              <th className="px-1">
                <button onClick={addCol} className="p-1 text-gray-500 hover:text-gray-800" title="Add column">
                  <Plus className="w-3 h-3" />
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci} className="px-1 py-0.5">
                    <input
                      value={cell}
                      onChange={(e) => setRows(rows.map((r, rIdx) => rIdx === ri ? r.map((c, cIdx) => cIdx === ci ? e.target.value : c) : r))}
                      className="w-full border border-gray-200 rounded px-2 py-1"
                    />
                  </td>
                ))}
                <td className="px-1">
                  <button onClick={() => removeRow(ri)} className="text-gray-400 hover:text-red-500" title="Remove row">
                    <Minus className="w-3 h-3" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button onClick={addRow} className="text-xs flex items-center gap-1 text-gray-600 hover:text-gray-900">
        <Plus className="w-3 h-3" /> Add row
      </button>
      <input
        value={caption}
        onChange={(e) => setCaption(e.target.value)}
        placeholder="Caption (optional)"
        className="w-full text-xs italic border-b border-gray-200 pb-1 focus:outline-none focus:border-gray-400"
      />
      <div className="flex gap-2 text-xs">
        <button
          onClick={() => { onSave({ title, caption, headers, rows }); setEditing(false); }}
          className="px-3 py-1.5 rounded text-white flex items-center gap-1"
          style={{ backgroundColor: brandColor }}
        ><Save className="w-3 h-3" /> Save</button>
        <button onClick={() => setEditing(false)}
          className="px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
      </div>
    </div>
  );
}
