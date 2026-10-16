'use client';

/**
 * Public-side review comments rendered alongside each section in the
 * /p/{slug} viewer when the editor has enabled disclosure.show_public_comments.
 *
 * Read-only by default: shows the existing thread for this section,
 * including inline-range anchors. No add/resolve/delete affordances.
 *
 * Editor-authored comments (author_email != null on the server) and public
 * comments (is_public=true) are visually distinguished via a small badge.
 */

import { MessageSquare, CornerDownRight, User2, Globe } from 'lucide-react';

export interface PublicComment {
  id: string;
  section_id: string;
  parent_comment_id: string | null;
  author_name: string | null;
  body: string;
  resolved: boolean;
  created_at: string | null;
  is_public: boolean;
  anchor?: { quote?: string; prefix?: string; suffix?: string } | null;
}

interface Props {
  sectionId: string;
  allComments: PublicComment[];
}

export default function PublicSectionComments({ sectionId, allComments }: Props) {
  const sectionComments = allComments.filter(c => c.section_id === sectionId);
  if (sectionComments.length === 0) return null;

  // Split into top-level + replies-by-parent for a one-level thread render.
  const topLevel = sectionComments.filter(c => c.parent_comment_id == null);
  const repliesByParent = new Map<string, PublicComment[]>();
  for (const c of sectionComments) {
    if (c.parent_comment_id != null) {
      const arr = repliesByParent.get(c.parent_comment_id) || [];
      arr.push(c);
      repliesByParent.set(c.parent_comment_id, arr);
    }
  }

  return (
    <div className="ah-public-comments mt-6 pl-3 border-l-2 border-amber-200">
      <div className="text-xs font-semibold text-amber-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
        <MessageSquare className="w-3.5 h-3.5" />
        Review notes <span className="font-normal text-amber-700/70">({sectionComments.length})</span>
      </div>

      {topLevel.map(c => (
        <div key={c.id} className="mb-3">
          <CommentBubble c={c} />
          {repliesByParent.get(c.id)?.map(reply => (
            <div key={reply.id} className="ml-5 mt-1.5 flex items-start gap-1.5">
              <CornerDownRight className="w-3 h-3 text-gray-400 mt-1 shrink-0" />
              <CommentBubble c={reply} compact />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CommentBubble({ c, compact }: { c: PublicComment; compact?: boolean }) {
  return (
    <div className={`rounded-md ${compact ? 'p-2' : 'p-2.5'} ${c.is_public ? 'bg-amber-50 border border-amber-100' : 'bg-gray-50 border border-gray-200'}`}>
      <div className="flex items-center gap-1.5 text-[11px] text-gray-600 mb-1">
        {c.is_public ? <Globe className="w-3 h-3 text-amber-600" /> : <User2 className="w-3 h-3 text-gray-500" />}
        <span className="font-medium text-gray-800">{c.author_name || (c.is_public ? 'External reviewer' : 'Team')}</span>
        {c.is_public && <span className="text-[10px] text-amber-700 bg-amber-100 px-1 rounded">Reviewer</span>}
        {c.resolved && <span className="text-[10px] text-emerald-700 bg-emerald-100 px-1 rounded">Resolved</span>}
        {c.created_at && (
          <span className="text-gray-400 ml-auto">{new Date(c.created_at).toLocaleDateString()}</span>
        )}
      </div>
      {c.anchor?.quote && (
        <div className="text-[11px] text-gray-600 italic mb-1 pl-2 border-l-2 border-amber-300">
          &ldquo;{c.anchor.quote}&rdquo;
        </div>
      )}
      <div className="text-sm text-gray-800 whitespace-pre-wrap break-words">{c.body}</div>
    </div>
  );
}
