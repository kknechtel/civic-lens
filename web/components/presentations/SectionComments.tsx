'use client';

/**
 * SectionComments — review comments thread anchored to a single deck
 * section. Editor-only: this component never mounts in the public
 * viewer at /p/{slug} (use PublicSectionComments for that read-only path).
 *
 * Lifecycle:
 *  - Parent passes `presentationId`, `sectionId`, and the full deck-wide
 *    comments list. We filter to this section client-side.
 *  - When the user clicks the icon-button, the parent toggles `open`.
 *  - Add / reply / resolve / unresolve / delete all hit the REST
 *    endpoints and call `onChange()` so the parent can refresh its
 *    cached list + summaries.
 */
import { MessageSquare, Check, Trash2, X, CornerDownRight, Send } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  addComment, patchComment, deleteComment,
  type CommentAnchor, type CommentRecord,
} from '@/lib/presentationsApi';

interface SectionCommentsProps {
  presentationId: string;
  sectionId: string;
  open: boolean;
  onClose: () => void;
  /** Parent-supplied list of every comment in the deck. We filter to this section. */
  allComments: CommentRecord[];
  /** Authenticated user email — used to gate Edit/Delete affordances. */
  currentUserEmail: string | null;
  /** Called after any mutation so the parent can refetch the deck's comments. */
  onChange: () => void;
  /** Optional one-shot anchor: when the parent captured a text selection
   *  at the moment the user opened the comment panel, pass it here. The
   *  next posted comment includes this anchor; afterwards the panel
   *  reverts to plain section-level comments. */
  pendingAnchor?: CommentAnchor | null;
  /** Notify parent the anchor has been consumed (cleared on submit/cancel). */
  onAnchorConsumed?: () => void;
}

export default function SectionComments({
  presentationId,
  sectionId,
  open,
  onClose,
  allComments,
  currentUserEmail,
  onChange,
  pendingAnchor,
  onAnchorConsumed,
}: SectionCommentsProps) {
  const [draft, setDraft] = useState('');
  const [replyParentId, setReplyParentId] = useState<string | null>(null);
  const [replyDraft, setReplyDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const sectionComments = useMemo(
    () => allComments.filter(c => c.section_id === sectionId),
    [allComments, sectionId],
  );

  const [topLevel, repliesByParent] = useMemo(() => {
    const top: CommentRecord[] = [];
    const replies = new Map<string, CommentRecord[]>();
    for (const c of sectionComments) {
      if (c.parent_comment_id == null) {
        top.push(c);
      } else {
        const arr = replies.get(c.parent_comment_id) || [];
        arr.push(c);
        replies.set(c.parent_comment_id, arr);
      }
    }
    return [top, replies] as const;
  }, [sectionComments]);

  const post = useCallback(async (body: string, parent_comment_id: string | null, anchor: CommentAnchor | null = null) => {
    setBusy(true);
    try {
      await addComment(presentationId, { section_id: sectionId, body, parent_comment_id, anchor });
      onChange();
    } catch (e) {
      alert(`Failed to post comment: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [presentationId, sectionId, onChange]);

  const patch = useCallback(async (commentId: string, body: { body?: string; resolved?: boolean }) => {
    setBusy(true);
    try {
      await patchComment(presentationId, commentId, body);
      onChange();
    } catch (e) {
      alert(`Failed to update comment: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [presentationId, onChange]);

  const remove = useCallback(async (commentId: string) => {
    if (!confirm('Delete this comment? Replies under it will be removed too.')) return;
    setBusy(true);
    try {
      await deleteComment(presentationId, commentId);
      onChange();
    } catch (e) {
      alert(`Failed to delete comment: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [presentationId, onChange]);

  const handleSubmitTop = useCallback(async () => {
    const body = draft.trim();
    if (!body) return;
    // Consume pendingAnchor on this submit only; subsequent comments
    // revert to section-level until the parent passes a new selection.
    await post(body, null, pendingAnchor ?? null);
    setDraft('');
    if (pendingAnchor && onAnchorConsumed) onAnchorConsumed();
  }, [draft, post, pendingAnchor, onAnchorConsumed]);

  const handleSubmitReply = useCallback(async () => {
    if (replyParentId == null) return;
    const body = replyDraft.trim();
    if (!body) return;
    await post(body, replyParentId);
    setReplyParentId(null);
    setReplyDraft('');
  }, [replyParentId, replyDraft, post]);

  useEffect(() => {
    if (!open) {
      setReplyParentId(null);
      setReplyDraft('');
    }
  }, [open]);

  if (!open) return null;

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
          <MessageSquare className="w-4 h-4 text-amber-700" />
          <span>Comments ({sectionComments.length})</span>
        </div>
        <button
          onClick={onClose}
          className="p-1 text-gray-400 hover:text-gray-700 rounded"
          title="Close comments"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="space-y-3">
        {topLevel.length === 0 && (
          <div className="text-xs text-gray-500 italic">No comments yet on this section.</div>
        )}
        {topLevel.map((c) => (
          <CommentNode
            key={c.id}
            comment={c}
            replies={repliesByParent.get(c.id) || []}
            currentUserEmail={currentUserEmail}
            onReply={() => { setReplyParentId(c.id); setReplyDraft(''); }}
            onResolve={(next) => patch(c.id, { resolved: next })}
            onDelete={() => remove(c.id)}
            replyOpen={replyParentId === c.id}
            replyDraft={replyDraft}
            setReplyDraft={setReplyDraft}
            onSubmitReply={handleSubmitReply}
            onCancelReply={() => { setReplyParentId(null); setReplyDraft(''); }}
            busy={busy}
            onDeleteReply={(replyId) => remove(replyId)}
          />
        ))}
      </div>

      <div className="mt-3 border-t border-amber-200 pt-3">
        {pendingAnchor?.quote && (
          <div className="mb-2 flex items-start gap-2 text-xs">
            <div className="flex-1 pl-2 border-l-2 border-amber-400 text-gray-700">
              <div className="text-[10px] uppercase tracking-wider text-amber-700 font-semibold mb-0.5">Commenting on</div>
              <div className="italic">&ldquo;{pendingAnchor.quote.slice(0, 200)}{pendingAnchor.quote.length > 200 ? '…' : ''}&rdquo;</div>
            </div>
            <button
              onClick={() => onAnchorConsumed && onAnchorConsumed()}
              className="text-gray-400 hover:text-gray-700"
              title="Clear selection"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
        <div className="flex items-start gap-2">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder={pendingAnchor?.quote ? 'Add a note about this passage...' : 'Add a comment for the team...'}
            className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-amber-300/60 focus:border-amber-400 min-h-[60px] resize-y"
            onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                void handleSubmitTop();
              }
            }}
          />
          <button
            onClick={handleSubmitTop}
            disabled={busy || !draft.trim()}
            className="inline-flex items-center gap-1 rounded bg-amber-600 px-3 py-1.5 text-xs text-white hover:bg-amber-700 disabled:opacity-40"
            title="Post comment (⌘/Ctrl+Enter)"
          >
            <Send className="w-3.5 h-3.5" /> Post
          </button>
        </div>
      </div>
    </div>
  );
}

interface CommentNodeProps {
  comment: CommentRecord;
  replies: CommentRecord[];
  currentUserEmail: string | null;
  onReply: () => void;
  onResolve: (next: boolean) => void;
  onDelete: () => void;
  replyOpen: boolean;
  replyDraft: string;
  setReplyDraft: (v: string) => void;
  onSubmitReply: () => void;
  onCancelReply: () => void;
  onDeleteReply: (replyId: string) => void;
  busy: boolean;
}

function CommentNode({
  comment, replies, currentUserEmail,
  onReply, onResolve, onDelete,
  replyOpen, replyDraft, setReplyDraft, onSubmitReply, onCancelReply,
  onDeleteReply, busy,
}: CommentNodeProps) {
  const isAuthor = !!currentUserEmail && comment.author_email === currentUserEmail;
  return (
    <div className={`rounded border p-2.5 text-sm ${
      comment.resolved ? 'border-gray-200 bg-gray-50 opacity-70' : 'border-amber-200 bg-white'
    }`}>
      <CommentHeader comment={comment} />
      {comment.anchor?.quote && (
        <div className="mt-1 mb-1 pl-2 border-l-2 border-amber-300 text-[11px] italic text-gray-600">
          &ldquo;{comment.anchor.quote.slice(0, 200)}{comment.anchor.quote.length > 200 ? '…' : ''}&rdquo;
        </div>
      )}
      <div className={`mt-1 whitespace-pre-wrap text-gray-800 ${comment.resolved ? 'line-through' : ''}`}>
        {comment.body}
      </div>
      <div className="mt-1.5 flex items-center gap-3 text-[11px]">
        <button
          onClick={() => onResolve(!comment.resolved)}
          disabled={busy}
          className={`inline-flex items-center gap-1 ${
            comment.resolved ? 'text-gray-500 hover:text-gray-700' : 'text-emerald-700 hover:text-emerald-800'
          }`}
          title={comment.resolved ? 'Reopen this comment' : 'Mark as resolved'}
        >
          <Check className="w-3 h-3" />
          {comment.resolved ? 'Reopen' : 'Resolve'}
        </button>
        {!comment.resolved && (
          <button
            onClick={onReply}
            disabled={busy}
            className="inline-flex items-center gap-1 text-gray-600 hover:text-gray-900"
          >
            <CornerDownRight className="w-3 h-3" /> Reply
          </button>
        )}
        {isAuthor && (
          <button
            onClick={onDelete}
            disabled={busy}
            className="inline-flex items-center gap-1 text-gray-500 hover:text-red-600 ml-auto"
            title="Delete comment"
          >
            <Trash2 className="w-3 h-3" /> Delete
          </button>
        )}
      </div>
      {replies.length > 0 && (
        <div className="mt-2 ml-3 space-y-2 border-l-2 border-amber-200/60 pl-2.5">
          {replies.map(r => (
            <div key={r.id} className="rounded border border-gray-200 bg-white p-2 text-xs">
              <CommentHeader comment={r} />
              <div className="mt-1 whitespace-pre-wrap text-gray-800">{r.body}</div>
              {!!currentUserEmail && r.author_email === currentUserEmail && (
                <button
                  onClick={() => onDeleteReply(r.id)}
                  disabled={busy}
                  className="mt-1 inline-flex items-center gap-1 text-[10px] text-gray-500 hover:text-red-600"
                >
                  <Trash2 className="w-3 h-3" /> Delete reply
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {replyOpen && (
        <div className="mt-2 ml-3 flex items-start gap-2 border-l-2 border-amber-300 pl-2.5">
          <textarea
            value={replyDraft}
            onChange={e => setReplyDraft(e.target.value)}
            placeholder={`Reply to ${comment.author_name || 'comment'}...`}
            className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs bg-white min-h-[40px] resize-y focus:outline-none focus:ring-1 focus:ring-amber-300/60"
            autoFocus
            onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); onSubmitReply(); }
              if (e.key === 'Escape') { e.preventDefault(); onCancelReply(); }
            }}
          />
          <div className="flex flex-col gap-1">
            <button
              onClick={onSubmitReply}
              disabled={busy || !replyDraft.trim()}
              className="rounded bg-amber-600 px-2 py-1 text-[10px] text-white hover:bg-amber-700 disabled:opacity-40"
            >
              Reply
            </button>
            <button
              onClick={onCancelReply}
              className="rounded border border-gray-300 px-2 py-0.5 text-[10px] text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function CommentHeader({ comment }: { comment: CommentRecord }) {
  const author = comment.author_name || comment.author_email || 'Unknown';
  const when = formatWhen(comment.created_at);
  return (
    <div className="flex items-center gap-2 text-[11px] text-gray-500">
      <span className="font-medium text-gray-700">{author}</span>
      <span>·</span>
      <span title={comment.created_at}>{when}</span>
      {comment.resolved && (
        <span className="ml-auto inline-flex items-center gap-1 text-emerald-700 text-[10px]">
          <Check className="w-3 h-3" /> Resolved
        </span>
      )}
    </div>
  );
}

function formatWhen(isoTs: string): string {
  if (!isoTs) return '';
  const dt = new Date(isoTs);
  const diffMs = Date.now() - dt.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return dt.toLocaleDateString();
}
