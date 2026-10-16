'use client';

import React from 'react';
import { Paperclip, Eye } from 'lucide-react';
import type { DeckSection, DeckAttachment } from '@/lib/presentationsApi';

interface Props {
  section: DeckSection;
  attachments: DeckAttachment[];
  onPreview?: (att: DeckAttachment) => void;
  brandColor?: string;
}

export default function AttachmentBlock({ section, attachments, onPreview, brandColor = '#385854' }: Props) {
  const att = attachments.find((a) => a.id === section.attachment_id);
  if (!att) {
    return <div className="text-sm text-gray-400 italic">Attachment not found ({section.attachment_id})</div>;
  }
  return (
    <div className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg bg-gray-50">
      <Paperclip className="w-5 h-5 flex-shrink-0" style={{ color: brandColor }} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{att.filename}</p>
        {section.caption && <p className="text-xs text-gray-500 truncate">{section.caption}</p>}
      </div>
      {onPreview && (
        <button
          onClick={() => onPreview(att)}
          className="px-2 py-1 text-xs rounded border border-gray-300 hover:bg-white flex items-center gap-1"
        >
          <Eye className="w-3 h-3" /> Preview
        </button>
      )}
    </div>
  );
}
