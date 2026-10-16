'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
    UserIcon,
    CpuChipIcon,
    ClipboardIcon,
    CheckIcon,
    ExclamationCircleIcon,
    InformationCircleIcon,
    ArrowDownTrayIcon,
    LightBulbIcon,
    WrenchScrewdriverIcon,
    PlusIcon,
    DocumentTextIcon,
} from '@heroicons/react/24/outline';
import MarkdownWithCharts from './MarkdownWithCharts';
import TypingIndicator from './TypingIndicator';
import { useAuth } from '@/app/contexts/AuthContext';

export type ToolActivity = {
    name: string;
    /** Human-readable line built from the tool's input args, e.g. `Searching: "X"`. */
    description?: string;
    input?: Record<string, unknown>;
    summary?: string;
    status: 'started' | 'done' | 'error';
};

export interface ChatPlan {
    steps: string[];
    rationale?: string;
    /** Number of plan steps already executed. We tick these off as tool_call events arrive. */
    completed?: number;
}

export interface ChatCost {
    input_tokens?: number;
    output_tokens?: number;
    estimated_cost_usd?: number;
    tool_calls_made?: number;
}

import type { DeckProposal } from "@/app/contexts/DeckChatContext";

/** Deck-mode proposal as displayed in chat. Extends the canonical
 *  DeckProposal with a local UI status flag set when the user clicks
 *  Apply/Dismiss in the message bubble. */
export type DeckProposalLite = DeckProposal & {
    applied?: "pending" | "applied" | "dismissed";
};

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant' | 'error' | 'system';
    content: string;
    timestamp: Date;
    isStreaming?: boolean;
    linkedDocs?: { id: string; filename: string }[];
    /** Extended thinking status: 'started' while Claude is reasoning, 'done' once text begins. */
    thinking?: 'started' | 'done';
    /** Tool calls Claude made on this turn (search_chunks, read_document, etc.). */
    toolActivity?: ToolActivity[];
    /** Latest progress line emitted by the backend (e.g. "Calling Claude…"). */
    statusText?: string;
    /** Plan announced via the share_plan tool — rendered as a checklist. */
    plan?: ChatPlan;
    /** Token + cost summary emitted with the `done` event. */
    cost?: ChatCost;
    /** Number of seamless continuations after a max_tokens cut. */
    continuations?: number;
    /** Deck-mode proposals from Claude (when chat is bound to an active presentation). */
    proposals?: DeckProposalLite[];
}

interface EnhancedMessageComponentProps {
    message: ChatMessage;
    brandColor: string;
    onCitationClick?: (info: { filename: string }) => void;
    onDocClick?: (docId: string, filename: string) => void;
    onDownload?: (content: string) => void;
    /** When in deck-mode, called with the proposal index when the user clicks Apply. */
    onApplyProposal?: (messageId: string, proposalIndex: number) => void;
    /** Called when the user clicks Dismiss on a proposal. */
    onDismissProposal?: (messageId: string, proposalIndex: number) => void;
    /** When in deck-mode, called when the user clicks "Send to deck" on a finished message. */
    onSendMessageToDeck?: (message: ChatMessage) => void;
    /** Title of the active deck, used to label the Send-to-Deck button. */
    activeDeckTitle?: string;
    /** When NOT in deck-mode, called to spin up a brand new presentation from this single message. */
    onMessageToNewPresentation?: (message: ChatMessage) => void;
    /** Called to seed an OPRA records request from this single message
     *  (navigates to /opra with additional_context pre-filled). */
    onMessageToOpra?: (message: ChatMessage) => void;
}

const TOOL_LABEL: Record<string, string> = {
    search_chunks: 'Searching passages',
    search_documents: 'Searching documents',
    read_document: 'Reading document',
    get_financial_summary: 'Loading financials',
    list_recent_documents: 'Listing recent documents',
    web_search: 'Searching the web',
};

function ToolActivityList({ items, brandColor }: { items: ToolActivity[]; brandColor: string }) {
    if (!items.length) return null;
    return (
        <div className="mt-2 space-y-1">
            {items.map((t, i) => (
                <div key={i} className="flex items-start gap-2 text-[11px] text-gray-500">
                    <WrenchScrewdriverIcon className="w-3 h-3 flex-shrink-0 mt-0.5" style={{ color: brandColor }} />
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="font-medium">{t.description || TOOL_LABEL[t.name] || t.name}</span>
                            {t.status === 'started' && (
                                <span className="flex gap-0.5">
                                    <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor }} />
                                    <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor, animationDelay: '120ms' }} />
                                    <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor, animationDelay: '240ms' }} />
                                </span>
                            )}
                        </div>
                        {t.summary && t.status !== 'started' && (
                            <div className="text-gray-400 truncate">{t.summary}</div>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
}

function StatusIndicator({ text, brandColor }: { text: string; brandColor: string }) {
    return (
        <div className="flex items-center gap-2 text-[11px] text-gray-500">
            <span className="flex gap-0.5">
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor }} />
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor, animationDelay: '120ms' }} />
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ backgroundColor: brandColor, animationDelay: '240ms' }} />
            </span>
            <span className="italic">{text}</span>
        </div>
    );
}

/** Tiny live-updating mm:ss elapsed-time chip. Re-renders every second. */
function ElapsedTicker({ since }: { since: Date }) {
    const [now, setNow] = useState(Date.now());
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);
    const sec = Math.max(0, Math.floor((now - since.getTime()) / 1000));
    if (sec < 3) return null; // hide for the first couple seconds — looks frantic otherwise
    const mm = Math.floor(sec / 60);
    const ss = sec % 60;
    return (
        <span className="text-[10px] text-gray-400 font-mono tabular-nums">
            {mm > 0 ? `${mm}m ${ss}s` : `${ss}s`}
        </span>
    );
}

function ThinkingIndicator({ status, brandColor }: { status: 'started' | 'done'; brandColor: string }) {
    if (status === 'done') return null;
    return (
        <div className="flex items-center gap-2 text-[11px] text-gray-500 mb-2">
            <LightBulbIcon className="w-3.5 h-3.5 animate-pulse" style={{ color: brandColor }} />
            <span className="italic">Thinking deeply…</span>
        </div>
    );
}

function PlanChecklist({ plan, brandColor }: { plan: ChatPlan; brandColor: string }) {
    if (!plan.steps?.length) return null;
    const completed = plan.completed ?? 0;
    return (
        <div className="mb-2 rounded-md border border-gray-200 bg-gray-50/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide font-semibold text-gray-500 mb-1.5">
                Plan
            </div>
            <ol className="space-y-1">
                {plan.steps.map((step, i) => {
                    const done = i < completed;
                    const active = i === completed;
                    return (
                        <li key={i} className="flex items-start gap-2 text-[12px]">
                            <span className={`mt-0.5 inline-flex w-4 h-4 items-center justify-center rounded-full text-[9px] font-bold flex-shrink-0
                                ${done ? 'text-white' : active ? 'border-2' : 'border border-gray-300 text-gray-400'}`}
                                style={done ? { backgroundColor: brandColor }
                                    : active ? { borderColor: brandColor, color: brandColor }
                                    : {}}
                            >
                                {done ? '✓' : i + 1}
                            </span>
                            <span className={done ? 'text-gray-500 line-through decoration-gray-300' : active ? 'text-gray-900' : 'text-gray-700'}>
                                {step}
                            </span>
                        </li>
                    );
                })}
            </ol>
            {plan.rationale && (
                <div className="text-[10px] italic text-gray-500 mt-2 pt-1.5 border-t border-gray-200">
                    {plan.rationale}
                </div>
            )}
        </div>
    );
}

function CostFooter({ cost }: { cost: ChatCost }) {
    const tok = (cost.input_tokens ?? 0) + (cost.output_tokens ?? 0);
    if (!tok && !cost.estimated_cost_usd) return null;
    const tokStr = tok >= 1000 ? `${(tok / 1000).toFixed(1)}K` : String(tok);
    const usd = cost.estimated_cost_usd ?? 0;
    return (
        <div className="text-[10px] text-gray-400 mt-1 flex items-center gap-2 flex-wrap">
            {cost.tool_calls_made != null && cost.tool_calls_made > 0 && (
                <span>{cost.tool_calls_made} tool {cost.tool_calls_made === 1 ? 'call' : 'calls'}</span>
            )}
            {tok > 0 && <span>{tokStr} tokens</span>}
            {usd > 0 && <span>${usd.toFixed(4)}</span>}
        </div>
    );
}

function ProposalCard({
    p, idx, brandColor, onApply, onDismiss,
}: {
    p: DeckProposalLite; idx: number; brandColor: string;
    onApply?: () => void; onDismiss?: () => void;
}) {
    const isApplied = p.applied === "applied";
    const isDismissed = p.applied === "dismissed";
    return (
        <div
            className={`mt-2 rounded-lg border p-2.5 ${
                isApplied ? "bg-emerald-50 border-emerald-300"
                    : isDismissed ? "bg-gray-50 border-gray-200 opacity-60"
                    : "bg-amber-50 border-amber-300"
            }`}
        >
            <div className="flex items-center justify-between gap-2 mb-1">
                <span className={`text-[10px] font-semibold uppercase tracking-wide ${
                    isApplied ? "text-emerald-700"
                        : isDismissed ? "text-gray-500"
                        : "text-amber-700"
                }`}>
                    {isApplied ? "Applied" : isDismissed ? "Dismissed" : "Proposal"}
                    {" — "}{proposalLabel(p)}
                </span>
                {!isApplied && !isDismissed && onApply && (
                    <div className="flex gap-1">
                        <button
                            onClick={onApply}
                            className="text-[11px] px-2 py-0.5 rounded text-white"
                            style={{ backgroundColor: brandColor }}
                        >
                            Apply
                        </button>
                        {onDismiss && (
                            <button
                                onClick={onDismiss}
                                className="text-[11px] px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-100 text-gray-600"
                            >
                                Dismiss
                            </button>
                        )}
                    </div>
                )}
            </div>
            <ProposalBody p={p} />
        </div>
    );
}

function proposalLabel(p: DeckProposalLite): string {
    switch (p.proposal_type) {
        case "section_edit": return "edit section";
        case "new_section": return "new section";
        case "inline_chart": return `${p.chart_type || "bar"} chart`;
        case "diagram": return "mermaid diagram";
        case "react_component": return p.section_id ? "replace component" : "new component";
        case "section_data_edit": return "replace component data";
        case "section_data_patch": return "patch component data";
        default: return `${p.kind || "section"}${p.section_id ? " (rewrite)" : " (new)"}`;
    }
}

function ProposalBody({ p }: { p: DeckProposalLite }) {
    const heading = p.heading || p.name || p.title;
    const proseBody =
        p.proposal_type === "section_edit" ? p.new_markdown
        : p.proposal_type === "new_section" ? p.markdown
        : p.proposal_type === undefined && p.kind === "narrative" ? p.body
        : undefined;
    const codeBody =
        p.proposal_type === "diagram" ? p.source
        : p.proposal_type === "react_component" ? p.tsx
        : p.proposal_type === "section_data_edit" ? safeJson(p.new_data)
        : p.proposal_type === undefined && p.kind === "react_component" ? p.tsx
        : undefined;
    const meta =
        p.proposal_type === "inline_chart"
            ? `${p.headers?.length ?? 0} cols × ${p.rows?.length ?? 0} rows`
            : p.proposal_type === "section_data_patch"
                ? summarizePatchOps(p)
                : p.proposal_type === undefined && p.kind === "table" && p.headers
                    ? `${p.headers.length} cols × ${(p.rows || []).length} rows`
                    : undefined;

    return (
        <>
            {heading && <p className="text-xs font-semibold text-gray-800">{heading}</p>}
            {p.rationale && <p className="text-[11px] text-gray-600 italic mt-0.5">{p.rationale}</p>}
            {proseBody && (
                <pre className="text-[11px] text-gray-700 whitespace-pre-wrap mt-1 max-h-32 overflow-y-auto">
                    {proseBody.slice(0, 600)}{proseBody.length > 600 ? "…" : ""}
                </pre>
            )}
            {codeBody && (
                <pre className="text-[11px] text-gray-700 whitespace-pre-wrap font-mono mt-1 max-h-32 overflow-y-auto">
                    {codeBody.slice(0, 600)}{codeBody.length > 600 ? "…" : ""}
                </pre>
            )}
            {meta && <div className="text-[11px] text-gray-700 mt-1">{meta}</div>}
        </>
    );
}

function safeJson(v: unknown): string {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function summarizePatchOps(p: DeckProposalLite): string {
    const parts: string[] = [];
    const ap = (p.array_patches || []).reduce<number>(
        (n, x) => n + (Array.isArray((x as { items?: unknown[] }).items) ? (x as { items: unknown[] }).items.length : 0),
        0,
    );
    if (ap) parts.push(`${ap} row patch${ap === 1 ? "" : "es"}`);
    if (p.scalar_set && Object.keys(p.scalar_set).length) parts.push(`${Object.keys(p.scalar_set).length} field set`);
    if (p.scalar_unset?.length) parts.push(`${p.scalar_unset.length} field unset`);
    const apx = (p.appends || []).reduce<number>(
        (n, x) => n + (Array.isArray((x as { items?: unknown[] }).items) ? (x as { items: unknown[] }).items.length : 0),
        0,
    );
    if (apx) parts.push(`${apx} append${apx === 1 ? "" : "s"}`);
    const rmx = (p.removes || []).reduce<number>(
        (n, x) => n + (Array.isArray((x as { keys?: unknown[] }).keys) ? (x as { keys: unknown[] }).keys.length : 0),
        0,
    );
    if (rmx) parts.push(`${rmx} remove${rmx === 1 ? "" : "s"}`);
    return parts.join(", ") || "no-op";
}

const EnhancedMessageComponent: React.FC<EnhancedMessageComponentProps> = ({
    message,
    brandColor,
    onCitationClick,
    onDocClick,
    onDownload,
    onApplyProposal,
    onDismissProposal,
    onSendMessageToDeck,
    activeDeckTitle,
    onMessageToNewPresentation,
    onMessageToOpra,
}) => {
    const [copied, setCopied] = useState(false);
    const [isVisible, setIsVisible] = useState(false);
    const messageRef = useRef<HTMLDivElement>(null);
    const { user } = useAuth();
    const isAdmin = !!user?.is_admin;

    useEffect(() => {
        const timer = setTimeout(() => setIsVisible(true), 50);
        return () => clearTimeout(timer);
    }, []);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(message.content);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {}
    };

    const formatTimestamp = (date: Date) => {
        const diff = Date.now() - date.getTime();
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(diff / 3600000);
        if (minutes < 1) return 'Just now';
        if (minutes < 60) return `${minutes}m ago`;
        if (hours < 24) return `${hours}h ago`;
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    };

    // While we wait for the first content delta we may still have status text,
    // tool activity, plan, or thinking already populated — render those inside the bubble
    // rather than the generic typing indicator.
    const hasEarlyActivity = (
        (message.toolActivity && message.toolActivity.length > 0) ||
        message.thinking === 'started' ||
        Boolean(message.statusText) ||
        Boolean(message.plan?.steps?.length)
    );

    if (message.isStreaming && !message.content && message.role === 'assistant' && !hasEarlyActivity) {
        return (
            <div className="flex justify-start mb-4">
                <TypingIndicator brandColor={brandColor} message="Analyzing documents..." isVisible={isVisible} />
            </div>
        );
    }

    if (message.role === 'system') {
        return (
            <div className={`flex justify-center mb-3 transition-all duration-300 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
                <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 rounded-lg text-xs text-gray-500 border border-gray-200">
                    <InformationCircleIcon className="w-3.5 h-3.5" />
                    <span>{message.content}</span>
                </div>
            </div>
        );
    }

    if (message.role === 'error') {
        return (
            <div className={`flex justify-center mb-3 transition-all duration-300 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
                <div className="flex items-center gap-2 px-3 py-2 bg-red-50 rounded-lg text-xs text-red-700 border border-red-200">
                    <ExclamationCircleIcon className="w-3.5 h-3.5" />
                    <span>{message.content}</span>
                </div>
            </div>
        );
    }

    const isUser = message.role === 'user';

    return (
        <div
            ref={messageRef}
            className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4 transition-all duration-300 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}
        >
            <div className={`flex items-end gap-2.5 ${isUser ? 'flex-row-reverse' : ''} max-w-[85%]`}>
                <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0 shadow"
                    style={{ backgroundColor: brandColor }}
                >
                    {isUser ? <UserIcon className="w-3.5 h-3.5" /> : <CpuChipIcon className="w-3.5 h-3.5" />}
                </div>

                <div className={`group relative rounded-2xl px-4 py-3 text-sm shadow-lg ${
                    isUser
                        ? 'text-white rounded-br-md'
                        : 'bg-white text-gray-800 border border-gray-200 rounded-bl-md'
                }`} style={isUser ? { backgroundColor: brandColor } : {}}>

                    {!isUser && message.thinking && <ThinkingIndicator status={message.thinking} brandColor={brandColor} />}

                    {!isUser && message.plan && <PlanChecklist plan={message.plan} brandColor={brandColor} />}

                    {!isUser && message.statusText && message.isStreaming && !message.content && (
                        <StatusIndicator text={message.statusText} brandColor={brandColor} />
                    )}

                    {!isUser && message.toolActivity && message.toolActivity.length > 0 && (
                        <ToolActivityList items={message.toolActivity} brandColor={brandColor} />
                    )}

                    {!isUser && message.isStreaming && !message.content && (
                        <div className="mt-1.5">
                            <ElapsedTicker since={message.timestamp} />
                        </div>
                    )}

                    {isUser ? (
                        <span className="whitespace-pre-wrap">{message.content}</span>
                    ) : message.content ? (
                        <div className={message.toolActivity?.length || message.thinking ? 'mt-2' : ''}>
                            <MarkdownWithCharts
                                content={message.content}
                                onCitationClick={onCitationClick}
                                brandColor={brandColor}
                            />
                        </div>
                    ) : null}

                    {message.isStreaming && message.content && (
                        <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-100">
                            <div className="flex gap-1">
                                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                            </div>
                            <span className="text-xs text-gray-400">Generating...</span>
                        </div>
                    )}

                    {!isUser && !message.isStreaming && message.content && (
                        <div className="absolute top-2 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-all">
                            <button onClick={handleCopy} className="p-1 hover:bg-gray-100 rounded" title="Copy">
                                {copied ? <CheckIcon className="w-3 h-3" style={{ color: brandColor }} /> : <ClipboardIcon className="w-3 h-3 text-gray-400" />}
                            </button>
                            {onDownload && (
                                <button onClick={() => onDownload(message.content)} className="p-1 hover:bg-gray-100 rounded" title="Download">
                                    <ArrowDownTrayIcon className="w-3 h-3 text-gray-400" />
                                </button>
                            )}
                        </div>
                    )}

                    {!isUser && !message.isStreaming && message.content && (onSendMessageToDeck || onMessageToNewPresentation || onMessageToOpra) && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {onSendMessageToDeck && (
                                <button
                                    onClick={() => onSendMessageToDeck(message)}
                                    className="text-[11px] px-2.5 py-1 rounded-full border hover:opacity-90 inline-flex items-center gap-1.5"
                                    style={{ borderColor: `${brandColor}50`, color: brandColor, backgroundColor: `${brandColor}08` }}
                                    title={activeDeckTitle ? `Add this answer as a new section in ${activeDeckTitle}` : 'Add to active deck'}
                                >
                                    <PlusIcon className="w-3 h-3" />
                                    Send to deck{activeDeckTitle ? ` (${activeDeckTitle})` : ''}
                                </button>
                            )}
                            {!onSendMessageToDeck && onMessageToNewPresentation && (
                                <button
                                    onClick={() => onMessageToNewPresentation(message)}
                                    className="text-[11px] px-2.5 py-1 rounded-full border hover:opacity-90 inline-flex items-center gap-1.5"
                                    style={{ borderColor: `${brandColor}50`, color: brandColor, backgroundColor: `${brandColor}08` }}
                                    title="Make a new presentation from this answer"
                                >
                                    <PlusIcon className="w-3 h-3" />
                                    New presentation
                                </button>
                            )}
                            {onMessageToOpra && (
                                <button
                                    onClick={() => onMessageToOpra(message)}
                                    className="text-[11px] px-2.5 py-1 rounded-full border hover:opacity-90 inline-flex items-center gap-1.5 border-amber-300 text-amber-800 bg-amber-50/60"
                                    title="Use this answer to seed an OPRA records request"
                                >
                                    <DocumentTextIcon className="w-3 h-3" />
                                    Make OPRA request
                                </button>
                            )}
                        </div>
                    )}

                    {!isUser && message.proposals && message.proposals.length > 0 && (
                        <div>
                            {message.proposals.map((p, i) => (
                                <ProposalCard
                                    key={i}
                                    p={p}
                                    idx={i}
                                    brandColor={brandColor}
                                    onApply={onApplyProposal ? () => onApplyProposal(message.id, i) : undefined}
                                    onDismiss={onDismissProposal ? () => onDismissProposal(message.id, i) : undefined}
                                />
                            ))}
                        </div>
                    )}

                    {!isUser && !message.isStreaming && message.cost && isAdmin && <CostFooter cost={message.cost} />}

                    {!message.isStreaming && (
                        <div className="text-[10px] text-gray-400 mt-1.5">{formatTimestamp(message.timestamp)}</div>
                    )}
                </div>
            </div>

            {message.linkedDocs && message.linkedDocs.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1 ml-9">
                    {message.linkedDocs.map(ld => (
                        <button key={ld.id} onClick={() => onDocClick?.(ld.id, ld.filename)}
                            className="flex items-center gap-1 px-2 py-1 text-[11px] rounded-lg hover:opacity-80 border"
                            style={{ backgroundColor: `${brandColor}10`, color: brandColor, borderColor: `${brandColor}30` }}>
                            <span className="truncate max-w-[180px]">{ld.filename}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
};

export default EnhancedMessageComponent;
