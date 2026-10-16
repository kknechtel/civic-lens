"use client";

import { useState, useRef, useEffect } from "react";
import { type Document } from "@/lib/api";
import {
  PaperAirplaneIcon,
  SparklesIcon,
  BuildingOfficeIcon,
  AcademicCapIcon,
} from "@heroicons/react/24/outline";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
}

interface Props {
  documents: Document[];
}

const API_BASE = "";

const SUGGESTED_QUESTIONS = [
  "Compare town budget trends from 2020 to 2025",
  "What are the major expenditure categories for the school district?",
  "How has the town's fund balance changed over the last 5 years?",
  "Summarize the most recent audit findings",
  "What is the total debt for the town vs school district?",
  "Are there any concerning financial trends?",
];

export default function FinancialChatPanel({ documents }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "I can help analyze this municipality's financial documents. Ask me about budgets, audits, trends, or comparisons between the town and school district.\n\nI have access to financial documents going back to 2004.",
    },
  ]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [scope, setScope] = useState<"all" | "town" | "school">("all");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async (query?: string) => {
    const text = query || input.trim();
    if (!text || isStreaming) return;
    setInput("");

    // Filter documents by scope
    const scopedDocs = documents.filter((d) => {
      if (scope === "all") return true;
      return d.category === scope;
    });

    // Pick the most relevant document to send context for
    // Prefer audits and financial statements as they have the most data
    const prioritized = [...scopedDocs].sort((a, b) => {
      const typeOrder: Record<string, number> = {
        audit: 0,
        financial_statement: 1,
        budget: 2,
      };
      const aOrder = typeOrder[a.doc_type || ""] ?? 9;
      const bOrder = typeOrder[b.doc_type || ""] ?? 9;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return (b.fiscal_year || "").localeCompare(a.fiscal_year || "");
    });

    const contextDoc = prioritized[0];

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: "user",
      content: text,
    };
    const assistantMsg: ChatMessage = {
      id: (Date.now() + 1).toString(),
      role: "assistant",
      content: "",
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    try {
      const body: any = {
        query: text,
        model: "claude",
      };

      // Send document context if available
      if (contextDoc) {
        body.document_id = contextDoc.id;
      }

      const token = typeof window !== "undefined" ? localStorage.getItem("ah_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let fullContent = "";

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split("\n");

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.type === "delta") {
                  fullContent += data.content;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id
                        ? { ...m, content: fullContent, isStreaming: true }
                        : m
                    )
                  );
                } else if (data.type === "done") {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id ? { ...m, isStreaming: false } : m
                    )
                  );
                } else if (data.type === "error") {
                  fullContent += `\n\nError: ${data.content}`;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id
                        ? { ...m, content: fullContent, isStreaming: false }
                        : m
                    )
                  );
                }
              } catch {}
            }
          }
        }
      }
    } catch (e: any) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: `Error: ${e.message}`, isStreaming: false }
            : m
        )
      );
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b bg-gray-50">
        <div className="flex items-center gap-2 mb-2">
          <SparklesIcon className="w-5 h-5 text-purple-500" />
          <h3 className="font-semibold text-gray-900 text-sm">Financial AI Chat</h3>
        </div>
        {/* Scope selector */}
        <div className="flex gap-1">
          {[
            { key: "all" as const, label: "All", icon: null },
            { key: "town" as const, label: "Town", icon: BuildingOfficeIcon },
            { key: "school" as const, label: "School", icon: AcademicCapIcon },
          ].map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setScope(key)}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-full transition-colors ${
                scope === key
                  ? "bg-purple-100 text-purple-700 font-medium"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              {Icon && <Icon className="w-3 h-3" />}
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[90%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-primary-600 text-white"
                  : "bg-gray-100 text-gray-800"
              }`}
            >
              {msg.content}
              {msg.isStreaming && (
                <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-gray-400 animate-pulse rounded" />
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />

        {/* Suggested questions (only show if few messages) */}
        {messages.length <= 2 && (
          <div className="space-y-1.5 pt-2">
            <p className="text-xs text-gray-400">Suggested questions:</p>
            {SUGGESTED_QUESTIONS.map((q, i) => (
              <button
                key={i}
                onClick={() => handleSend(q)}
                className="block w-full text-left text-xs text-gray-600 bg-gray-50 hover:bg-gray-100 rounded-lg px-3 py-2 transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-3 border-t">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
            placeholder="Ask about financials..."
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500"
            disabled={isStreaming}
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isStreaming}
            className="px-3 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50"
          >
            <PaperAirplaneIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
