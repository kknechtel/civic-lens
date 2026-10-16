'use client';

import React, { useEffect, useRef } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import { Table } from '@tiptap/extension-table';
import { TableRow } from '@tiptap/extension-table-row';
import { TableCell } from '@tiptap/extension-table-cell';
import { TableHeader } from '@tiptap/extension-table-header';
import Image from '@tiptap/extension-image';
import { Markdown } from 'tiptap-markdown';
import {
  Bold as BoldIcon, Italic as ItalicIcon, List as ListIcon, ListOrdered,
  Quote, Code as CodeIcon, Link as LinkIcon, Table as TableIcon,
  Heading2, Heading3, Minus, Undo2, Redo2, ImageIcon,
} from 'lucide-react';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';
import type { DeckSection } from '@/lib/presentationsApi';

interface Props {
  section: DeckSection;
  editable: boolean;
  onSave: (patch: Partial<DeckSection>) => void;
  onCitationClick?: (info: { filename: string }) => void;
  brandColor?: string;
}

/**
 * Rich-text narrative section editor — Tiptap with a markdown bridge so
 * the underlying section.body stays plain markdown (round-trips cleanly
 * to/from PPTX/DOCX export and AI proposals). Toolbar mirrors the
 * bank-processor's deck editor: H2/H3, bold, italic, code, lists,
 * quote, link, table, image, HR, undo/redo.
 *
 * Read-only mode renders via MarkdownWithCharts so charts (Recharts)
 * and citation pills behave identically here, in the chat panel, and
 * in the AskAIPanel proposal preview.
 */
export default function NarrativeBlock({
  section, editable, onSave, onCitationClick, brandColor = '#385854',
}: Props) {
  const body = section.body || '';

  // Read-only render — no editor instance, no toolbar. Title is owned by
  // the wrapping PresentationViewer (which renders an outer <h2> for every
  // section kind); rendering it here too caused a duplicate header in the
  // public/viewer mode, mirroring the duplicate the editor had.
  if (!editable) {
    return (
      <div className="space-y-2">
        {body ? (
          <MarkdownWithCharts
            content={body}
            onCitationClick={onCitationClick}
            brandColor={brandColor}
          />
        ) : (
          <p className="text-sm text-gray-400 italic">(empty)</p>
        )}
      </div>
    );
  }

  return (
    <EditorWrapper
      section={section}
      onSave={onSave}
      brandColor={brandColor}
    />
  );
}


/**
 * Tiptap's markdown bridge mangles ```chart fences (the JSON body breaks
 * round-tripping). Stash chart fences out-of-band before handing the text
 * to Tiptap, show the user an opaque marker line instead, and splice the
 * original fence back in on save. This guarantees chart blocks survive
 * any number of edits without being touched by the markdown serializer.
 */
const CHART_FENCE_RE = /```chart\s*\n[\s\S]*?\n```/g;
function stashCharts(md: string): { stripped: string; charts: string[] } {
  const charts: string[] = [];
  const stripped = md.replace(CHART_FENCE_RE, (match) => {
    const idx = charts.length;
    charts.push(match);
    // Visible marker the user sees in Tiptap. Plain text — no markdown
    // syntax — so tiptap-markdown round-trips it untouched.
    return `📊 [Chart ${idx + 1} — preserved, edit via raw markdown]`;
  });
  return { stripped, charts };
}
function restoreCharts(md: string, charts: string[]): string {
  return md.replace(/📊 \[Chart (\d+) — preserved[^\]]*\]/g, (_m, n) => {
    const idx = parseInt(n, 10) - 1;
    return charts[idx] ?? _m;
  });
}

/** Inner component so we don't init Tiptap on read-only sections. */
function EditorWrapper({
  section, onSave, brandColor,
}: {
  section: DeckSection;
  onSave: (patch: Partial<DeckSection>) => void;
  brandColor: string;
}) {
  // Title is owned by the outer PresentationEditor's AutoGrowTextarea.
  // Rendering a second title input here was duplicating the header on
  // every narrative section; we only manage the body.
  const [dirty, setDirty] = React.useState(false);
  // Holds the original ```chart fences for the body currently loaded into
  // the editor. Updated whenever section.body changes externally.
  const chartsRef = useRef<string[]>([]);

  const initial = React.useMemo(() => stashCharts(section.body || ''), []); // eslint-disable-line react-hooks/exhaustive-deps
  // Seed the ref synchronously so the very first save has the stash too.
  if (chartsRef.current.length === 0 && initial.charts.length > 0) {
    chartsRef.current = initial.charts;
  }

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        codeBlock: { HTMLAttributes: { class: 'rounded bg-gray-100 p-2 font-mono text-xs' } },
      }),
      Link.configure({
        openOnClick: false,
        HTMLAttributes: { class: 'underline', style: `color: ${brandColor}` },
      }),
      Placeholder.configure({
        placeholder: 'Write the section body in markdown. Use [source: filename.pdf] to cite documents.',
      }),
      Table.configure({ resizable: false }),
      TableRow,
      TableCell,
      TableHeader,
      Image,
      Markdown.configure({ html: false, breaks: true, transformPastedText: true }),
    ],
    content: initial.stripped,
    editorProps: {
      attributes: {
        class: 'prose prose-sm max-w-none focus:outline-none min-h-[120px] py-2',
      },
    },
    onUpdate: () => setDirty(true),
  });

  // If the section's body changes externally (e.g. AI proposal applied),
  // re-stash the chart fences and reload the editor with the stripped text.
  useEffect(() => {
    if (!editor) return;
    const next = stashCharts(section.body || '');
    const current = (editor.storage.markdown.getMarkdown?.() ?? editor.getText()) as string;
    if (current.trim() === next.stripped.trim()) return;
    chartsRef.current = next.charts;
    editor.commands.setContent(next.stripped, false);
    setDirty(false);
  }, [editor, section.id, section.body]);

  const handleSave = () => {
    if (!editor) return;
    const md = (editor.storage.markdown.getMarkdown?.() ?? '') as string;
    // Splice the original chart fences back before persisting.
    const restored = restoreCharts(md, chartsRef.current);
    onSave({ body: restored });
    setDirty(false);
  };

  if (!editor) return null;

  return (
    <div className="space-y-2">
      <Toolbar editor={editor} brandColor={brandColor} />

      <div className="border border-gray-200 rounded-md focus-within:border-gray-400 transition-colors">
        <EditorContent editor={editor} className="px-3" />
      </div>

      <div className="flex items-center gap-2 text-xs">
        <button
          onClick={handleSave}
          disabled={!dirty}
          className="px-3 py-1.5 rounded text-white disabled:opacity-40"
          style={{ backgroundColor: brandColor }}
        >
          {dirty ? 'Save changes' : 'Saved'}
        </button>
        {dirty && (
          <button
            onClick={() => {
              const next = stashCharts(section.body || '');
              chartsRef.current = next.charts;
              editor.commands.setContent(next.stripped, false);
              setDirty(false);
            }}
            className="px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50 text-gray-700"
          >
            Discard
          </button>
        )}
      </div>
    </div>
  );
}


/** Toolbar mirrors the bank-processor's deck editor — same set of buttons,
 *  same ordering: H2 / H3 / B / I / code / lists / quote / link / table / image / HR / undo / redo. */
function Toolbar({ editor, brandColor }: { editor: ReturnType<typeof useEditor>; brandColor: string }) {
  if (!editor) return null;

  const Btn = ({
    onClick, active, disabled, title, children,
  }: {
    onClick: () => void; active?: boolean; disabled?: boolean; title: string;
    children: React.ReactNode;
  }) => (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent ${
        active ? 'bg-gray-100' : ''
      }`}
      style={active ? { color: brandColor } : {}}
    >
      {children}
    </button>
  );

  const insertLink = () => {
    const previousUrl = editor.getAttributes('link').href;
    const url = window.prompt('Link URL', previousUrl || 'https://');
    if (url === null) return;
    if (url === '') {
      editor.chain().focus().extendMarkRange('link').unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
  };

  const insertTable = () => {
    editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
  };

  const insertImage = () => {
    const url = window.prompt('Image URL');
    if (!url) return;
    editor.chain().focus().setImage({ src: url }).run();
  };

  return (
    <div className="flex items-center gap-0.5 px-1 py-1 border border-gray-200 rounded-md bg-gray-50/60">
      <Btn
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        active={editor.isActive('heading', { level: 2 })}
        title="Heading 2"
      >
        <Heading2 className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        active={editor.isActive('heading', { level: 3 })}
        title="Heading 3"
      >
        <Heading3 className="w-4 h-4" />
      </Btn>

      <span className="w-px h-4 bg-gray-300 mx-1" />

      <Btn
        onClick={() => editor.chain().focus().toggleBold().run()}
        active={editor.isActive('bold')}
        title="Bold (Cmd/Ctrl+B)"
      >
        <BoldIcon className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().toggleItalic().run()}
        active={editor.isActive('italic')}
        title="Italic (Cmd/Ctrl+I)"
      >
        <ItalicIcon className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().toggleCode().run()}
        active={editor.isActive('code')}
        title="Inline code"
      >
        <CodeIcon className="w-4 h-4" />
      </Btn>

      <span className="w-px h-4 bg-gray-300 mx-1" />

      <Btn
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        active={editor.isActive('bulletList')}
        title="Bulleted list"
      >
        <ListIcon className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        active={editor.isActive('orderedList')}
        title="Numbered list"
      >
        <ListOrdered className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        active={editor.isActive('blockquote')}
        title="Blockquote"
      >
        <Quote className="w-4 h-4" />
      </Btn>

      <span className="w-px h-4 bg-gray-300 mx-1" />

      <Btn onClick={insertLink} active={editor.isActive('link')} title="Add link">
        <LinkIcon className="w-4 h-4" />
      </Btn>
      <Btn onClick={insertTable} title="Insert 3x3 table">
        <TableIcon className="w-4 h-4" />
      </Btn>
      <Btn onClick={insertImage} title="Insert image by URL">
        <ImageIcon className="w-4 h-4" />
      </Btn>
      <Btn onClick={() => editor.chain().focus().setHorizontalRule().run()} title="Horizontal rule">
        <Minus className="w-4 h-4" />
      </Btn>

      <span className="ml-auto" />

      <Btn
        onClick={() => editor.chain().focus().undo().run()}
        disabled={!editor.can().undo()}
        title="Undo"
      >
        <Undo2 className="w-4 h-4" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().redo().run()}
        disabled={!editor.can().redo()}
        title="Redo"
      >
        <Redo2 className="w-4 h-4" />
      </Btn>
    </div>
  );
}
