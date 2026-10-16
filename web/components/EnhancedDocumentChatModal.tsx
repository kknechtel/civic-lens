"use client";

/**
 * EnhancedDocumentChatModal — document-scoped chat modal with full SSE
 * event handling. Mirrors GlobalChat's parsing (delta, tool_use,
 * tool_result, plan, proposal, done, thinking, error) but constrains the
 * conversation to a single document via the chat.py `document_id` param.
 *
 * Replaces the original 212-line DocumentChatModal which only handled
 * `delta` + `done`. Uses EnhancedMessageComponent for rendering, so chips,
 * tool activity, plans, citations, and the per-message OPRA / deck /
 * presentation buttons all work uniformly across surfaces.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { XMarkIcon, PaperAirplaneIcon, SparklesIcon } from "@heroicons/react/24/outline";

import { type Document } from "@/lib/api";
import { createPresentationFromChat } from "@/lib/presentationsApi";
import EnhancedMessageComponent, {
    type ChatMessage,
    type ChatPlan,
    type ChatCost,
    type ToolActivity,
} from "./EnhancedMessageComponent";

interface Props {
    document: Document | null;
    isOpen: boolean;
    onClose: () => void;
    brandColor?: string;
}

const API_BASE = "";

const SUGGESTED_QUESTIONS = [
    "What are the key takeaways?",
    "Summarize the main financial figures.",
    "What categories or accounts are referenced?",
    "Are there any anomalies or notable variances?",
];

function newId(prefix: string): string {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export default function EnhancedDocumentChatModal({
    document: doc, isOpen, onClose, brandColor = "#385854",
}: Props) {
    const router = useRouter();
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");
    const [streaming, setStreaming] = useState(false);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [convertingDeck, setConvertingDeck] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const abortRef = useRef<AbortController | null>(null);

    // Auto-scroll to the latest message.
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    // Reset whenever the modal opens against a new document.
    useEffect(() => {
        if (isOpen && doc) {
            setMessages([
                {
                    id: newId("intro"),
                    role: "assistant",
                    timestamp: new Date(),
                    content: `Ready to help you with **${doc.filename}**. Ask about specific figures, dates, or what the document is about.`,
                },
            ]);
            setSessionId(null);
        }
    }, [isOpen, doc?.id]);

    // Abort any in-flight stream when the modal closes.
    useEffect(() => {
        if (!isOpen && abortRef.current) {
            abortRef.current.abort();
            abortRef.current = null;
        }
    }, [isOpen]);

    const updateLastAssistant = useCallback((mut: (m: ChatMessage) => ChatMessage) => {
        setMessages(prev => {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
                if (prev[i].role === "assistant") {
                    const next = prev.slice();
                    next[i] = mut(prev[i]);
                    return next;
                }
            }
            return prev;
        });
    }, []);

    const send = useCallback(async (text: string) => {
        const query = text.trim();
        if (!query || streaming || !doc) return;
        setInput("");

        const userMsg: ChatMessage = {
            id: newId("u"), role: "user", content: query, timestamp: new Date(),
        };
        const assistantMsg: ChatMessage = {
            id: newId("a"), role: "assistant", content: "", timestamp: new Date(),
            isStreaming: true, toolActivity: [], statusText: "Connecting…",
        };
        setMessages(prev => [...prev, userMsg, assistantMsg]);
        setStreaming(true);

        const controller = new AbortController();
        abortRef.current = controller;

        try {
            const token = typeof window !== "undefined" ? localStorage.getItem("ah_token") : null;
            const headers: Record<string, string> = { "Content-Type": "application/json" };
            if (token) headers["Authorization"] = `Bearer ${token}`;

            const res = await fetch(`${API_BASE}/api/chat/stream`, {
                method: "POST",
                headers,
                signal: controller.signal,
                body: JSON.stringify({
                    query,
                    session_id: sessionId,
                    document_id: doc.id,
                    model: "claude",
                }),
            });
            if (!res.body) throw new Error("No response stream");

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let pending = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                pending += decoder.decode(value, { stream: true });

                let sep = pending.indexOf("\n\n");
                while (sep !== -1) {
                    const frame = pending.slice(0, sep);
                    pending = pending.slice(sep + 2);
                    sep = pending.indexOf("\n\n");
                    if (!frame.trim() || frame.startsWith(":")) continue;
                    let dataLine = "";
                    for (const line of frame.split("\n")) {
                        if (line.startsWith("data: ")) dataLine += line.slice(6);
                        else if (line.startsWith("data:")) dataLine += line.slice(5);
                    }
                    if (!dataLine) continue;
                    let evt: { type: string; [k: string]: unknown };
                    try { evt = JSON.parse(dataLine); } catch { continue; }

                    handleEvent(evt);
                }
            }

            updateLastAssistant(m => ({ ...m, isStreaming: false, statusText: undefined }));
        } catch (err: unknown) {
            const aborted = err instanceof DOMException && err.name === "AbortError";
            if (aborted) {
                updateLastAssistant(m => ({ ...m, isStreaming: false, statusText: undefined }));
            } else {
                const detail = err instanceof Error ? err.message : String(err);
                updateLastAssistant(m => ({
                    ...m, isStreaming: false, statusText: undefined,
                    content: `${m.content ? m.content + "\n\n" : ""}Error: ${detail}`,
                }));
            }
        } finally {
            setStreaming(false);
            if (abortRef.current === controller) abortRef.current = null;
        }

        function handleEvent(evt: { type: string; [k: string]: unknown }) {
            switch (evt.type) {
                case "session": {
                    if (typeof evt.session_id === "string") setSessionId(evt.session_id);
                    break;
                }
                case "status": {
                    const text = typeof evt.text === "string" ? evt.text : undefined;
                    updateLastAssistant(m => ({ ...m, statusText: text }));
                    break;
                }
                case "thinking": {
                    const status = evt.status as "started" | "done" | undefined;
                    updateLastAssistant(m => ({ ...m, thinking: status }));
                    break;
                }
                case "plan": {
                    const plan = evt as unknown as ChatPlan;
                    updateLastAssistant(m => ({ ...m, plan }));
                    break;
                }
                case "delta": {
                    const chunk = typeof evt.content === "string" ? evt.content : "";
                    if (!chunk) return;
                    updateLastAssistant(m => ({ ...m, content: m.content + chunk }));
                    break;
                }
                case "tool_use":
                case "tool_call": {
                    const name = typeof evt.name === "string" ? evt.name : "tool";
                    const description = typeof evt.description === "string" ? evt.description : undefined;
                    updateLastAssistant(m => {
                        const next: ToolActivity[] = [...(m.toolActivity || [])];
                        next.push({ name, status: "started", description });
                        return { ...m, toolActivity: next };
                    });
                    break;
                }
                case "tool_result": {
                    const name = typeof evt.name === "string" ? evt.name : "tool";
                    const summary = typeof evt.summary === "string" ? evt.summary : undefined;
                    updateLastAssistant(m => {
                        const next: ToolActivity[] = [...(m.toolActivity || [])];
                        // Mark the most recent matching call as done.
                        for (let i = next.length - 1; i >= 0; i -= 1) {
                            if (next[i].name === name && next[i].status !== "done") {
                                next[i] = { ...next[i], status: "done", summary };
                                break;
                            }
                        }
                        return { ...m, toolActivity: next };
                    });
                    break;
                }
                case "continuation": {
                    const count = typeof evt.count === "number" ? evt.count : undefined;
                    updateLastAssistant(m => ({ ...m, continuations: count }));
                    break;
                }
                case "done": {
                    const cost: ChatCost = {
                        input_tokens: typeof evt.input_tokens === "number" ? evt.input_tokens : undefined,
                        output_tokens: typeof evt.output_tokens === "number" ? evt.output_tokens : undefined,
                        estimated_cost_usd: typeof evt.estimated_cost_usd === "number" ? evt.estimated_cost_usd : undefined,
                        tool_calls_made: typeof evt.tool_calls_made === "number" ? evt.tool_calls_made : undefined,
                    };
                    updateLastAssistant(m => ({
                        ...m,
                        isStreaming: false,
                        statusText: undefined,
                        cost,
                    }));
                    break;
                }
                case "error": {
                    const detail = typeof evt.content === "string" ? evt.content : "Stream failed";
                    updateLastAssistant(m => ({
                        ...m, isStreaming: false, statusText: undefined,
                        content: `${m.content ? m.content + "\n\n" : ""}Error: ${detail}`,
                    }));
                    break;
                }
                default:
                    break;
            }
        }
    }, [sessionId, streaming, doc, updateLastAssistant]);

    const handleMessageToOpra = (msg: ChatMessage) => {
        const body = (msg.content || "").trim();
        if (!body || !doc) return;
        const seed: Record<string, unknown> = {
            additional_context: `From ${doc.filename}:\n\n${body}`,
        };
        if (body.length <= 400) seed.specific_records = body;
        try { window.sessionStorage.setItem("ah-opra-seed", JSON.stringify(seed)); } catch { /* ignore */ }
        router.push("/opra");
    };

    const handleMessageToPresentation = async (msg: ChatMessage) => {
        if (convertingDeck) return;
        const body = (msg.content || "").trim();
        if (!body) return;
        const firstHeading = body.match(/^#{1,3}\s+(.+)$/m)?.[1];
        const firstSentence = body.split(/[.\n]/)[0];
        const titleHint = (firstHeading || firstSentence || (doc?.filename ?? "From chat")).trim().slice(0, 80);
        setConvertingDeck(true);
        try {
            const deck = await createPresentationFromChat(
                [{ role: "assistant", content: body }],
                titleHint,
            );
            router.push(`/presentations/${deck.id}`);
        } catch (err: unknown) {
            const detail = err instanceof Error ? err.message : "Unknown error";
            setMessages(prev => [...prev, {
                id: newId("e"), role: "error", timestamp: new Date(),
                content: `Couldn't build a presentation: ${detail}`,
            }]);
        } finally {
            setConvertingDeck(false);
        }
    };

    if (!isOpen || !doc) return null;

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl h-[85vh] flex flex-col">
                <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
                    <div className="flex items-center gap-2">
                        <SparklesIcon className="w-5 h-5" style={{ color: brandColor }} />
                        <h2 className="font-semibold text-gray-900">Chat with document</h2>
                    </div>
                    <div className="flex items-center gap-3">
                        <span className="text-xs text-gray-500 truncate max-w-[280px]" title={doc.filename}>
                            {doc.filename}
                        </span>
                        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 p-1 rounded hover:bg-gray-100">
                            <XMarkIcon className="w-5 h-5" />
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50/50">
                    {messages.map(msg => (
                        <EnhancedMessageComponent
                            key={msg.id}
                            message={msg}
                            brandColor={brandColor}
                            onMessageToOpra={handleMessageToOpra}
                            onMessageToNewPresentation={msg.role === "assistant" ? handleMessageToPresentation : undefined}
                        />
                    ))}
                    <div ref={messagesEndRef} />
                </div>

                {messages.length <= 1 && (
                    <div className="px-4 py-2 border-t border-gray-100 bg-white flex flex-wrap gap-1.5">
                        {SUGGESTED_QUESTIONS.map(q => (
                            <button
                                key={q}
                                onClick={() => send(q)}
                                disabled={streaming}
                                className="text-[11px] px-2.5 py-1 rounded-full border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                            >
                                {q}
                            </button>
                        ))}
                    </div>
                )}

                <div className="px-4 py-3 border-t border-gray-200">
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) send(input); }}
                            placeholder={`Ask about ${doc.filename}...`}
                            className="flex-1 rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:border-gray-400"
                            disabled={streaming}
                        />
                        <button
                            onClick={() => send(input)}
                            disabled={!input.trim() || streaming}
                            className="px-3 rounded-xl text-white disabled:opacity-50"
                            style={{ backgroundColor: brandColor }}
                            title="Send"
                        >
                            <PaperAirplaneIcon className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
