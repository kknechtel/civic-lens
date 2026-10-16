"use client";

import { useState, useRef, useEffect } from "react";
import { type Document } from "@/lib/api";
import {
  XMarkIcon,
  PaperAirplaneIcon,
  SparklesIcon,
} from "@heroicons/react/24/outline";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
}

interface Props {
  document: Document | null;
  isOpen: boolean;
  onClose: () => void;
}

const API_BASE = "";

export default function DocumentChatModal({ document: doc, isOpen, onClose }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Reset when doc changes
  useEffect(() => {
    if (doc) {
      setMessages([
        {
          id: "system",
          role: "assistant",
          content: `I'm ready to help you analyze **${doc.filename}**. Ask me anything about this document.`,
        },
      ]);
    }
  }, [doc?.id]);

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    const query = input.trim();
    setInput("");

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: "user",
      content: query,
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
      const token = typeof window !== "undefined" ? localStorage.getItem("ah_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          query,
          document_id: doc?.id,
          model: "claude",
        }),
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
                      m.id === assistantMsg.id
                        ? { ...m, isStreaming: false }
                        : m
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

  if (!isOpen || !doc) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 border-b">
          <div className="flex items-center gap-2">
            <SparklesIcon className="w-5 h-5 text-purple-500" />
            <h2 className="font-semibold text-gray-900">Chat with Document</h2>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400 truncate max-w-[200px]">{doc.filename}</span>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <XMarkIcon className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-primary-600 text-white"
                    : "bg-gray-100 text-gray-800"
                }`}
              >
                {msg.content}
                {msg.isStreaming && (
                  <span className="inline-block w-2 h-4 ml-1 bg-gray-400 animate-pulse rounded" />
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="px-4 py-3 border-t">
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
              placeholder="Ask about this document..."
              className="flex-1 rounded-xl border border-gray-300 px-4 py-2.5 text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              disabled={isStreaming}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || isStreaming}
              className="px-4 py-2.5 bg-primary-600 text-white rounded-xl hover:bg-primary-700 disabled:opacity-50"
            >
              <PaperAirplaneIcon className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
