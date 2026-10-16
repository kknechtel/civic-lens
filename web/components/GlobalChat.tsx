"use client";

import { useMunicipality, entityOfKind, type Municipality } from "@/app/contexts/MunicipalityContext";
import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { searchDocuments, resolveCitation, getDocumentViewUrl, getChatSessions, getChatHistory, uploadDocument, type Document } from "@/lib/api";
import { useIsMobile } from "@/lib/hooks";
import EnhancedMessageComponent, { type ChatMessage, type ToolActivity } from "@/components/EnhancedMessageComponent";
import FilePreviewModal from "@/components/FilePreviewModal";
import { useDeckChat, type DeckProposal } from "@/app/contexts/DeckChatContext";
import { createPresentationFromChat } from "@/lib/presentationsApi";
import {
  XMarkIcon, PaperAirplaneIcon, SparklesIcon,
  MinusIcon, DocumentTextIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon,
  MagnifyingGlassIcon, LinkIcon, GlobeAltIcon,
  ArrowDownTrayIcon, ArrowUpTrayIcon, ClockIcon, ArrowPathIcon,
  LightBulbIcon, DocumentChartBarIcon, CpuChipIcon, PlusIcon,
  ClipboardDocumentIcon, CheckIcon, PaperClipIcon,
  EllipsisVerticalIcon, EyeSlashIcon, FunnelIcon,
} from "@heroicons/react/24/outline";

const brandColor = "#385854";
// Non-streaming requests use the relative URL (Amplify proxy → backend).
// Streaming SSE requests bypass Amplify and hit the FastAPI box directly via
// the api.* HTTPS endpoint, because Amplify's SSR Compute has a hard 30s
// response timeout that kills long Claude streams. NEXT_PUBLIC_API_DIRECT_URL
// is set to your API origin (e.g. https://api.example.org) in the deploy env (mirrors bank-processor's
// API_DIRECT pattern).
const API_BASE = "";
const API_DIRECT =
  process.env.NEXT_PUBLIC_API_DIRECT_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "";

// Panel widths — matches bank-processor's RKCAIChatPanel.
const CHAT_WIDTH_DEFAULT = 480;
const CHAT_WIDTH_WIDE = Math.min(900, typeof window !== "undefined" ? Math.max(480, window.innerWidth * 0.6) : 720);
const CHAT_WIDTH_MIN = 360;
const CHAT_WIDTH_KEY = "ah_chat_width";

/** Stable ref to "is the chat panel currently visible to the user".
 *  Read inside the streaming reader so the 'done' handler can decide
 *  whether to flag unread + fire a browser notification. */
function useHiddenRef(isOpen: boolean, isMinimized: boolean, dismissed: boolean) {
  const ref = useRef(false);
  useEffect(() => {
    ref.current = dismissed || !isOpen || isMinimized;
  }, [isOpen, isMinimized, dismissed]);
  return ref;
}

const DISMISSED_KEY = "ah_chat_dismissed";

/** Multi-scope picker — narrows what Claude searches over for this turn.
 *  - `all`: no filter; default
 *  - `entity`: restrict to Borough (id="town") or School (id="school")
 *  - `fiscal_year`: restrict to FY{id} (e.g., "2024")
 *  - `doc_type`: restrict to a category (budget|audit|minutes|resolution|ordinance) */
type ChatScopeType = "all" | "entity" | "fiscal_year" | "doc_type";
interface ChatScope {
  type: ChatScopeType;
  id?: string;
  label: string;
}

/** Scope chips — town/school names come from the municipality profile and
 *  the fiscal-year chips track the current year. */
function scopePresets(m: Municipality): ChatScope[] {
  const school = entityOfKind(m, "school");
  const y = new Date().getFullYear();
  return [
    { type: "all", label: m.short_name ? `All ${m.short_name}` : "All documents" },
    { type: "entity", id: "town", label: `${m.entities[0]?.short || "Town"} only` },
    ...(school ? [{ type: "entity" as const, id: "school", label: `${school.short} only` }] : []),
    ...[y - 1, y - 2, y - 3].map((fy) => ({ type: "fiscal_year" as const, id: String(fy), label: `FY ${fy}` })),
    ...SCOPE_DOC_TYPES,
  ];
}

const SCOPE_DOC_TYPES: ChatScope[] = [
  { type: "doc_type", id: "budget", label: "Budgets" },
  { type: "doc_type", id: "audit", label: "Audits" },
  { type: "doc_type", id: "minutes", label: "Meeting minutes" },
  { type: "doc_type", id: "resolution", label: "Resolutions" },
  { type: "doc_type", id: "ordinance", label: "Ordinances" },
];

/** Build a one-line scope hint appended to the user's query so Claude
 *  constrains its tool calls (search_chunks / search_documents accept
 *  fiscal_year + category filters). Empty string when scope is "all". */
function scopeHint(scope: ChatScope, m: Municipality): string {
  switch (scope.type) {
    case "all": return "";
    case "entity": {
      const school = entityOfKind(m, "school");
      return scope.id === "school"
        ? `[Scope: ${school?.label || "school district"} documents only — pass category='school' to search tools.]`
        : `[Scope: ${m.name || "municipal"} documents only — pass category='town' to search tools.]`;
    }
    case "fiscal_year":
      return `[Scope: FY${scope.id} only — pass fiscal_year='${scope.id}' to search tools.]`;
    case "doc_type":
      return `[Scope: ${scope.id} documents only — pass doc_type='${scope.id}' to list_recent_documents and filter search results accordingly.]`;
    default: return "";
  }
}

export default function GlobalChat() {
  const router = useRouter();
  const isMobile = useIsMobile();
  const [isOpen, setIsOpen] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [unread, setUnread] = useState(0);
  const [convertingDeck, setConvertingDeck] = useState(false);
  // Mobile-only: kebab menu sheet (replaces the row of header buttons that
  // can't fit on a phone-width header).
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  // Fully dismissed = no FAB, no panel. Re-summon via Cmd/Ctrl+/ keyboard
  // shortcut. Persisted across reloads via localStorage so a user who hides
  // the chat doesn't see it pop back on every navigation.
  const [dismissed, setDismissed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(DISMISSED_KEY) === "1";
  });
  const hiddenRef = useHiddenRef(isOpen, isMinimized, dismissed);
  // Deck-mode: when a presentation editor is mounted, the chat sends an
  // additional `presentation_id` flag and the backend exposes deck-aware
  // tools (propose_section). Proposals come back as SSE events that the
  // chat renders as accept-or-reject cards.
  const deckChat = useDeckChat();
  const activeDeck = deckChat.activeDeck;

  // Persist dismissed state. Also wire a Cmd/Ctrl+/ shortcut to summon back.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (dismissed) localStorage.setItem(DISMISSED_KEY, "1");
    else localStorage.removeItem(DISMISSED_KEY);
  }, [dismissed]);

  // Resizable width — persisted, drag handle on the left edge.
  const [chatWidthPx, setChatWidthPx] = useState<number>(() => {
    if (typeof window === "undefined") return CHAT_WIDTH_DEFAULT;
    const stored = localStorage.getItem(CHAT_WIDTH_KEY);
    const n = stored ? parseInt(stored, 10) : NaN;
    return Number.isFinite(n) && n >= CHAT_WIDTH_MIN ? n : CHAT_WIDTH_DEFAULT;
  });
  const [isResizing, setIsResizing] = useState(false);
  const isWide = chatWidthPx >= CHAT_WIDTH_WIDE - 1;

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(CHAT_WIDTH_KEY, String(chatWidthPx));
  }, [chatWidthPx]);

  // Clamp if window resized smaller than the chat
  useEffect(() => {
    const onResize = () => {
      setChatWidthPx(w => Math.min(w, Math.max(CHAT_WIDTH_MIN, window.innerWidth - 200)));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    const startX = e.clientX;
    const startW = chatWidthPx;
    const onMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX;  // drag LEFT = wider
      const next = Math.max(CHAT_WIDTH_MIN, Math.min(window.innerWidth - 200, startW + delta));
      setChatWidthPx(next);
    };
    const onUp = () => {
      setIsResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const toggleMaximize = () => {
    setChatWidthPx(w => (w >= CHAT_WIDTH_WIDE - 1 ? CHAT_WIDTH_DEFAULT : CHAT_WIDTH_WIDE));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        setDismissed(false);
        setIsOpen(true);
        setIsMinimized(false);
      }
    };
    // MobileNav fires this when the user taps More → Show chat. Touch
    // counterpart to the Cmd/Ctrl+/ shortcut above.
    const onShowChat = () => {
      setDismissed(false);
      setIsOpen(true);
      setIsMinimized(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("ah:show-chat", onShowChat);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("ah:show-chat", onShowChat);
    };
  }, []);
  const [sessionId, setSessionId] = useState(() => `s_${Date.now()}`);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [deepThinking, setDeepThinking] = useState(false);
  // reportMode kept in state (still passed to backend) so existing chat sessions
  // don't break, but the toggle UI is gone — presentations replace formal reports.
  const reportMode = false;
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  // Multi-scope picker — narrows what Claude searches over for this turn.
  // The backend already accepts scope_type/scope_id (saved with chat_history)
  // and Claude reads the appended scope hint in the user message to constrain
  // its search_chunks / search_documents tool calls.
  const [chatScope, setChatScope] = useState<ChatScope>({ type: "all", label: "All documents" });
  const municipality = useMunicipality();
  const SCOPE_PRESETS = scopePresets(municipality);
  const expertName = `${municipality.short_name || "Civic"} Expert`;
  const [showScopePicker, setShowScopePicker] = useState(false);
  // Deck-mode slide picker — scoped to the active deck. Reset when the user
  // navigates to a different deck so a stale id doesn't get sent. Stored as
  // a section id or null (= "whole deck").
  const [targetSectionId, setTargetSectionId] = useState<string | null>(null);
  useEffect(() => {
    // Reset slide focus whenever the bound deck changes (different presentation,
    // or no deck at all). Sticky across re-renders within the same deck so the
    // operator's pick survives sending a message.
    setTargetSectionId(null);
  }, [activeDeck?.id]);
  const [splitDoc, setSplitDoc] = useState<{ url: string; filename: string } | null>(null);
  const [showDocPicker, setShowDocPicker] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [docSearch, setDocSearch] = useState("");
  // Upload-from-chat state. While `uploading` is set, the chat shows a
  // "Uploading filename…" pill in place of the normal selectedDoc pill so
  // the user can see progress without losing the input.
  const [uploading, setUploading] = useState<{ filename: string; size: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: docResultsResponse } = useQuery({
    queryKey: ["chat-doc-search", docSearch], queryFn: () => searchDocuments(docSearch), enabled: docSearch.length > 1,
  });
  const docResults = docResultsResponse?.results;
  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"], queryFn: getChatSessions, enabled: showHistory,
  });

  // `block: "end"` is explicit so the sentinel lands at the BOTTOM of the
  // scrollport — without it the default "start" can push the sentinel to
  // the top of the viewport, which on mobile sometimes scrolls the message
  // off-screen mid-stream.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  // iOS soft-keyboard handling: when the input is focused, Safari shrinks
  // the visual viewport but NOT the layout viewport. A fixed panel
  // positioned with `bottom: 4rem` keeps its layout-viewport height — so
  // the keyboard covers the input and the messages area. Listen to the
  // VisualViewport API and lift the panel's bottom by the keyboard height
  // so the input + last messages stay visible. Falls back to a no-op on
  // browsers without VisualViewport (Firefox Android handles this itself).
  const [keyboardOffset, setKeyboardOffset] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    if (!vv) return;
    const onChange = () => {
      const overlap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      setKeyboardOffset(overlap);
    };
    vv.addEventListener("resize", onChange);
    vv.addEventListener("scroll", onChange);
    onChange();
    return () => {
      vv.removeEventListener("resize", onChange);
      vv.removeEventListener("scroll", onChange);
    };
  }, []);

  // When the chat opens (or comes back from minimized), clear the unread count.
  useEffect(() => {
    if (isOpen && !isMinimized) setUnread(0);
  }, [isOpen, isMinimized]);

  // Ask for browser-notification permission lazily — first time the user sends.
  // We don't ask up-front because that's annoying and most browsers will ignore it.
  const ensureNotifyPermission = () => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    if (Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  };

  const fireDoneNotification = (preview: string) => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    if (Notification.permission !== "granted") return;
    const n = new Notification(`${expertName} response ready`, {
      body: preview.slice(0, 200),
      tag: "ah-chat-done",
      icon: "/icon-192.png",
    });
    n.onclick = () => { window.focus(); setIsOpen(true); setIsMinimized(false); n.close(); };
  };

  const handleViewDoc = async (docId: string, filename: string) => {
    try {
      if (!docId && filename) {
        docId = (await resolveCitation(filename))?.id || "";
      }
      if (!docId) {
        console.warn("Could not find doc for citation:", filename);
        return;
      }
      const { url } = await getDocumentViewUrl(docId);
      setSplitDoc({ url, filename });
      if (!isExpanded) setIsExpanded(true);
    } catch (e) {
      console.error("Failed to open doc viewer:", e);
    }
  };

  const handleDownloadChat = () => {
    const text = messages.filter(m => m.role !== "error").map(m => `[${m.role.toUpperCase()}] ${m.content}`).join("\n\n---\n\n");
    const blob = new Blob([text], { type: "text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `ah-chat-${new Date().toISOString().slice(0, 10)}.txt`; a.click();
  };

  const handleDownloadMessage = (content: string) => {
    const blob = new Blob([content], { type: "text/markdown" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `ah-response-${new Date().toISOString().slice(0, 16)}.md`; a.click();
  };

  const handleNewSession = () => {
    setSessionId(`s_${Date.now()}`);
    setMessages([{
      id: "welcome", role: "assistant", timestamp: new Date(),
      content: `New conversation started. Ask me anything about ${municipality.short_name || "your town"}.`,
    }]);
    setShowHistory(false);
  };

  /** Load a previous session's messages and switch to it. */
  const handleLoadSession = async (newSessionId: string) => {
    setShowHistory(false);
    if (newSessionId === sessionId) return;
    try {
      const data = await getChatHistory(newSessionId);
      setSessionId(newSessionId);
      const loaded: ChatMessage[] = (data.messages || []).map((m, i) => ({
        id: `${newSessionId}_${i}`,
        role: (m.role === 'user' || m.role === 'assistant') ? m.role : 'system',
        content: m.content,
        timestamp: m.timestamp ? new Date(m.timestamp) : new Date(),
      }));
      setMessages(loaded.length ? loaded : [{
        id: "empty", role: "assistant", timestamp: new Date(),
        content: "(This conversation has no messages.)",
      }]);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Unknown error';
      setMessages(prev => [...prev, {
        id: `e_${Date.now()}`, role: "error", timestamp: new Date(),
        content: `Couldn't load that conversation: ${detail}`,
      }]);
    }
  };

  const handleCitationClick = (info: { filename: string }) => {
    handleViewDoc("", info.filename);
  };

  /** Apply a deck proposal: dispatch through DeckChatContext, then mark
   *  the proposal as applied/dismissed on the message so the UI updates. */
  const handleApplyProposal = async (messageId: string, idx: number) => {
    const msg = messages.find(m => m.id === messageId);
    const proposal = msg?.proposals?.[idx];
    if (!proposal) return;
    const ok = await deckChat.applyProposal(proposal);
    setMessages(prev => prev.map(m => {
      if (m.id !== messageId) return m;
      const ps = [...(m.proposals || [])];
      ps[idx] = { ...ps[idx], applied: ok ? "applied" : "dismissed" };
      return { ...m, proposals: ps };
    }));
  };

  /** Convert the entire current conversation into a NEW presentation. Claude
   *  structures the messages into 4-8 sections with citations preserved. */
  const handleChatToPresentation = async () => {
    if (convertingDeck) return;
    const transcript = messages
      .filter(m => (m.role === 'user' || m.role === 'assistant') && (m.content || '').trim())
      .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }));
    if (transcript.length === 0) return;
    const firstUser = transcript.find(m => m.role === 'user')?.content || 'Chat presentation';
    const titleHint = firstUser.split(/[.\n]/)[0].slice(0, 80);
    setConvertingDeck(true);
    try {
      const deck = await createPresentationFromChat(transcript, titleHint);
      router.push(`/presentations/${deck.id}`);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Unknown error';
      setMessages(prev => [...prev, {
        id: `e_${Date.now()}`, role: 'error', timestamp: new Date(),
        content: `Couldn't build a presentation from this chat: ${detail}`,
      }]);
    } finally {
      setConvertingDeck(false);
    }
  };

  /** Convert a single assistant message into a NEW one-section presentation
   *  (used when there's no active deck — gives the user a starting point). */
  const handleMessageToNewPresentation = async (msg: ChatMessage) => {
    if (convertingDeck) return;
    const body = (msg.content || '').trim();
    if (!body) return;
    const firstHeading = body.match(/^#{1,3}\s+(.+)$/m)?.[1];
    const firstSentence = body.split(/[.\n]/)[0];
    const titleHint = (firstHeading || firstSentence || 'From chat').trim().slice(0, 80);
    setConvertingDeck(true);
    try {
      const deck = await createPresentationFromChat(
        [{ role: 'assistant', content: body }],
        titleHint,
      );
      router.push(`/presentations/${deck.id}`);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Unknown error';
      setMessages(prev => [...prev, {
        id: `e_${Date.now()}`, role: 'error', timestamp: new Date(),
        content: `Couldn't build a presentation: ${detail}`,
      }]);
    } finally {
      setConvertingDeck(false);
    }
  };

  /** Send an assistant message body to the active deck as a new narrative section. */
  const handleSendMessageToDeck = async (msg: ChatMessage) => {
    if (!activeDeck) {
      setMessages(prev => [...prev, {
        id: `n_${Date.now()}`, role: "system", timestamp: new Date(),
        content: "No active presentation. Open a presentation first, then come back.",
      }]);
      return;
    }
    const body = (msg.content || "").trim();
    if (!body) return;
    // Pull a title from the first heading or first sentence.
    const firstHeading = body.match(/^#{1,3}\s+(.+)$/m)?.[1];
    const firstSentence = body.split(/[.\n]/)[0];
    const title = (firstHeading || firstSentence || "From chat").trim().slice(0, 80);
    try {
      const ok = await deckChat.applyProposal({
        kind: "narrative",
        title,
        body,
        rationale: "Sent from chat",
      });
      if (!ok) throw new Error("applyProposal returned false");
      setMessages(prev => [...prev, {
        id: `n_${Date.now()}`, role: "system", timestamp: new Date(),
        content: `Added to “${activeDeck.title}” as a new section.`,
      }]);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Unknown error';
      setMessages(prev => [...prev, {
        id: `e_${Date.now()}`, role: "error", timestamp: new Date(),
        content: `Couldn't add to deck: ${detail}`,
      }]);
    }
  };

  const handleDismissProposal = (messageId: string, idx: number) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== messageId) return m;
      const ps = [...(m.proposals || [])];
      ps[idx] = { ...ps[idx], applied: "dismissed" };
      return { ...m, proposals: ps };
    }));
  };

  /** Update one message in the list. */
  const patchMessage = (id: string, patch: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== id) return m;
      const p = typeof patch === "function" ? patch(m) : patch;
      return { ...m, ...p };
    }));
  };

  /** Open the system file picker. The actual upload runs in handleFileChosen. */
  const handlePickFileToUpload = () => {
    if (uploading || isStreaming) return;
    setUploadError(null);
    fileInputRef.current?.click();
  };

  /** Upload a file from the user's disk and attach it to the next chat
   *  message. Uses the existing presigned-URL flow (lib/api.uploadDocument)
   *  which goes browser → S3 directly, so it isn't subject to Amplify's 30s
   *  proxy timeout for big PDFs. After success the doc behaves identically
   *  to one picked from the existing library — the chat sends document_id. */
  const handleFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset the input value so the same file can be re-picked after a clear.
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!file) return;

    // Cap at 100MB — anything larger probably belongs in the document library
    // upload flow (with category metadata) rather than ad-hoc chat upload.
    const MAX_BYTES = 100 * 1024 * 1024;
    if (file.size > MAX_BYTES) {
      setUploadError(`File too large (${Math.round(file.size / 1024 / 1024)} MB). Max 100 MB — use the Documents page for larger files.`);
      return;
    }

    setUploading({ filename: file.name, size: file.size });
    setUploadError(null);
    try {
      // "default" project mirrors what the document-library upload modal uses.
      const doc = await uploadDocument(file, "default", { category: "chat-upload" });
      setSelectedDoc({ id: doc.id, filename: doc.filename } as Document);
      setShowDocPicker(false);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : String(err);
      // 409 = duplicate. We don't have the existing doc's id in that response,
      // so surface a clear message and let the user pick the existing one.
      if (/409|duplicate/i.test(detail)) {
        setUploadError(`"${file.name}" is already in the library — use "Scope docs" to attach the existing copy.`);
      } else {
        setUploadError(`Upload failed: ${detail}`);
      }
    } finally {
      setUploading(null);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput(""); setShowDocPicker(false); setShowHistory(false);
    ensureNotifyPermission();

    const userMsg: ChatMessage = {
      id: Date.now().toString(), role: "user", content: text, timestamp: new Date(),
      linkedDocs: selectedDoc ? [{ id: selectedDoc.id, filename: selectedDoc.filename }] : undefined,
    };
    const assistantMsg: ChatMessage = {
      id: (Date.now() + 1).toString(), role: "assistant", content: "", timestamp: new Date(), isStreaming: true,
      toolActivity: [],
    };
    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    try {
      const hint = scopeHint(chatScope, municipality);
      const scopedQuery = hint ? `${hint}\n\n${text}` : text;
      const body: Record<string, unknown> = {
        query: scopedQuery,
        model: "claude",
        session_id: sessionId,
        web_search: webSearchEnabled,
        deep_thinking: deepThinking,
        report_mode: reportMode,
        scope_type: chatScope.type,
        scope_id: chatScope.id,
      };
      if (selectedDoc) body.document_id = selectedDoc.id;
      // Deck-mode hint to the backend so it adds propose_section to the tool surface.
      if (activeDeck) {
        body.presentation_id = activeDeck.id;
        body.presentation_summary = activeDeck.summary;
        if (targetSectionId) body.target_section_id = targetSectionId;
      }

      const token = typeof window !== "undefined" ? localStorage.getItem("ah_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      // Use the direct HTTPS endpoint so Amplify's 30s SSR timeout doesn't
      // kill long streams. If the direct endpoint can't be reached at all
      // (network error, VPN block, DNS, TLS), fall back to the same-origin
      // Amplify proxy — the user gets a working chat (capped at ~30s) instead
      // of a dead one. Falls back to the relative URL when API_DIRECT isn't
      // configured (local dev).
      const directUrl = `${API_DIRECT || API_BASE}/api/chat/stream`;
      const fallbackUrl = `${API_BASE}/api/chat/stream`;
      let response: Response;
      try {
        response = await fetch(directUrl, {
          method: "POST", headers, body: JSON.stringify(body),
        });
      } catch (netErr) {
        // CORS-blocked / DNS / TLS / offline. Try the same-origin proxy.
        if (directUrl !== fallbackUrl) {
          console.warn("Direct chat endpoint unreachable, falling back to proxy:", netErr);
          response = await fetch(fallbackUrl, {
            method: "POST", headers, body: JSON.stringify(body),
          });
        } else {
          throw netErr;
        }
      }
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let full = "";
      let pending = "";  // SSE frames can split across reads

      if (!reader) throw new Error("No response stream");

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });

        // SSE frames separated by blank line
        let sep: number;
        while ((sep = pending.indexOf("\n\n")) !== -1) {
          const frame = pending.slice(0, sep);
          pending = pending.slice(sep + 2);
          if (!frame.trim() || frame.startsWith(":")) continue;  // keepalive

          // Frame may contain `event:` and one or more `data:` lines
          let dataLine = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("data: ")) dataLine += line.slice(6);
            else if (line.startsWith("data:")) dataLine += line.slice(5);
          }
          if (!dataLine) continue;

          let d: any;
          try { d = JSON.parse(dataLine); } catch { continue; }

          switch (d.type) {
            case "session":
              if (d.session_id) setSessionId(d.session_id);
              break;

            case "status":
              // Backend transition marker: "Building context…", "Calling Claude…", etc.
              patchMessage(assistantMsg.id, { statusText: d.text || undefined });
              break;

            case "plan":
              // Claude announced its plan via share_plan. Render as a checklist.
              patchMessage(assistantMsg.id, {
                plan: {
                  steps: Array.isArray(d.steps) ? d.steps : [],
                  rationale: d.rationale || undefined,
                  completed: 0,
                },
                statusText: undefined,
              });
              break;

            case "proposal": {
              // Deck-mode: Claude proposed a section change. Show as an
              // accept/reject card. The proposal can be applied to the active
              // deck via DeckChatContext.applyProposal.
              const proposal = (d.input as DeckProposal) || (d as DeckProposal);
              patchMessage(assistantMsg.id, m => ({
                proposals: [...(m.proposals || []), proposal],
              }));
              break;
            }

            case "continuation":
              // Backend auto-resumed after hitting max_tokens. Tracked for UI.
              patchMessage(assistantMsg.id, m => ({
                continuations: (m.continuations || 0) + 1,
              }));
              break;

            case "delta":
              full += d.content || "";
              patchMessage(assistantMsg.id, { content: full, statusText: undefined });
              break;

            case "thinking":
              patchMessage(assistantMsg.id, {
                thinking: d.status === "done" ? "done" : "started",
              });
              break;

            case "tool_call":
            case "tool_use": {
              const name = d.name || (Array.isArray(d.tools) ? d.tools[0]?.name : "") || "tool";
              const newEntry: ToolActivity = {
                name,
                input: d.input,
                description: d.description,
                status: "started",
              };
              patchMessage(assistantMsg.id, m => ({
                toolActivity: [...(m.toolActivity || []), newEntry],
                // Each non-plan tool call advances one plan step (visual proxy).
                plan: m.plan ? { ...m.plan, completed: Math.min((m.plan.completed || 0) + 1, m.plan.steps.length) } : m.plan,
              }));
              break;
            }

            case "tool_result":
              patchMessage(assistantMsg.id, m => {
                const acts = [...(m.toolActivity || [])];
                for (let i = acts.length - 1; i >= 0; i--) {
                  if (acts[i].name === d.name && acts[i].status === "started") {
                    acts[i] = { ...acts[i], status: "done", summary: d.summary };
                    break;
                  }
                }
                return { toolActivity: acts };
              });
              break;

            case "done":
              patchMessage(assistantMsg.id, m => ({
                isStreaming: false,
                statusText: undefined,
                cost: {
                  input_tokens: d.input_tokens,
                  output_tokens: d.output_tokens,
                  estimated_cost_usd: d.estimated_cost_usd,
                  tool_calls_made: d.tool_calls_made,
                },
                // Mark all plan steps complete on success.
                plan: m.plan ? { ...m.plan, completed: m.plan.steps.length } : m.plan,
              }));
              if (hiddenRef.current) {
                setUnread(u => u + 1);
                fireDoneNotification(full || "Response ready");
              }
              break;

            case "error":
              patchMessage(assistantMsg.id, m => ({
                content: (m.content || "") + (m.content ? "\n\n" : "") + "Error: " + (d.content || "Unknown error"),
                isStreaming: false,
                statusText: undefined,
                role: "error" as const,
              }));
              break;
          }
        }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      patchMessage(assistantMsg.id, { content: `Error: ${msg}`, isStreaming: false, role: "error" as const });
    } finally {
      setIsStreaming(false);
      setSelectedDoc(null);
    }
  };

  // Fully dismissed — no FAB, no panel. Streams continue running in the background;
  // a browser notification still fires when they complete. Press Cmd/Ctrl+/ to summon back.
  if (dismissed) return null;

  // Closed state - FAB button. Streaming pulse on the right; unread count on top-left.
  if (!isOpen) return (
    <button onClick={() => setIsOpen(true)}
      className="fixed bottom-20 md:bottom-6 right-4 md:right-6 z-50 text-white p-3.5 md:p-4 rounded-full shadow-xl hover:shadow-2xl transition-all hover:scale-105 group"
      style={{ backgroundColor: brandColor }}
      aria-label={unread > 0 ? `${unread} new response ready` : `Open ${expertName}`}>
      <SparklesIcon className="w-5 h-5 md:w-6 md:h-6 group-hover:rotate-12 transition-transform" />
      {isStreaming && (
        <span className="absolute top-1 right-1 w-2 h-2 bg-white rounded-full animate-pulse"
              title="Working on a response…" />
      )}
      {unread > 0 && (
        <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1
                         bg-amber-400 text-[#1a2e2c] text-[11px] font-bold
                         rounded-full flex items-center justify-center shadow ring-2 ring-white">
          {unread > 9 ? "9+" : unread}
        </span>
      )}
    </button>
  );

  if (isMinimized) return (
    <div className="fixed bottom-20 md:bottom-6 right-4 md:right-6 z-50 text-white rounded-full shadow-xl flex items-center gap-2 px-4 py-2.5 md:px-5 md:py-3 cursor-pointer hover:shadow-2xl"
      style={{ backgroundColor: brandColor }} onClick={() => setIsMinimized(false)}>
      <SparklesIcon className="w-4 h-4" /><span className="text-sm font-medium">{expertName}</span>
      {isStreaming && <span className="w-2 h-2 bg-white rounded-full animate-pulse" title="Working…" />}
      {unread > 0 && (
        <span className="min-w-[18px] h-[18px] px-1 bg-amber-400 text-[#1a2e2c] text-[10px] font-bold rounded-full flex items-center justify-center">
          {unread > 9 ? "9+" : unread} new
        </span>
      )}
      <button onClick={e => { e.stopPropagation(); setIsOpen(false); setIsMinimized(false); setSplitDoc(null); }} className="ml-1 hover:bg-white/20 rounded p-0.5"><XMarkIcon className="w-4 h-4" /></button>
    </div>
  );

  // Layout matches bank-processor's RKCAIChatPanel on desktop: full-height
  // slide-out anchored to the right edge, draggable left-edge handle, maximize
  // toggles between default width and ~60% of viewport. On mobile the panel
  // takes the full viewport — no width math, no drag handle.
  const totalWidth = splitDoc ? `calc(${chatWidthPx}px + min(420px, 40vw))` : `${chatWidthPx}px`;
  // On mobile, the split-doc viewer is rendered as a separate fullscreen
  // overlay (after this panel) — never as an inline column.
  const showInlineSplit = !!splitDoc && !isMobile;

  return (
    <>
    <div
      className={
        isMobile
          ? "fixed top-0 left-0 right-0 z-40 bg-white flex flex-col"
          : `fixed inset-y-0 right-0 z-50 bg-white shadow-2xl flex flex-row border-l border-gray-200 ${isResizing ? "" : "transition-[width] duration-200"}`
      }
      style={
        isMobile
          // Leave room for MobileNav (h-16 = 4rem) plus iOS safe-area padding.
          // When the soft keyboard is open, `keyboardOffset` raises the
          // panel above it so the input + last messages stay visible.
          ? { bottom: `calc(4rem + env(safe-area-inset-bottom) + ${keyboardOffset}px)` }
          : { width: totalWidth }
      }
    >
      {/* Drag handle on the LEFT edge — drag to resize, double-click to toggle wide.
          Hidden on mobile (touch users can't grab a 1.5px handle). */}
      {!isMobile && (
        <div
          onMouseDown={startResize}
          onDoubleClick={toggleMaximize}
          title="Drag to resize · double-click to toggle wide"
          className="absolute top-0 bottom-0 left-0 w-1.5 -translate-x-1/2 cursor-col-resize z-10 group"
        >
          <div className="h-full w-full transition-colors" style={{ backgroundColor: isResizing ? brandColor : "transparent" }} />
          <div
            className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2 w-1 h-10 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
            style={{ backgroundColor: brandColor }}
          />
        </div>
      )}

      {/* Chat column. `min-h-0` is critical on mobile: without it, a flex-col
          child defaults to `min-height: auto` and refuses to shrink below its
          intrinsic content size, so a long message list pushes the input off
          screen and the messages area never gets a bounded height to scroll
          inside. Same goes for `min-w-0` on the desktop horizontal layout. */}
      <div
        className={`flex flex-col bg-gray-50 min-w-0 min-h-0 flex-1 ${isMobile ? "" : "border-r border-gray-200"}`}
        style={isMobile ? undefined : { width: `${chatWidthPx}px`, minWidth: `${chatWidthPx}px` }}
      >
        {/* Header — color shifts to violet when bound to a presentation so the
            operator sees at a glance that the chat is in revise-the-deck mode. */}
        <div
          className="flex items-center justify-between px-4 py-3 text-white flex-shrink-0"
          style={{ backgroundColor: activeDeck ? '#5b2f8a' : brandColor }}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            {activeDeck
              ? <DocumentChartBarIcon className="w-5 h-5 opacity-90 flex-shrink-0" />
              : <CpuChipIcon className="w-5 h-5 opacity-90 flex-shrink-0" />}
            <div className="min-w-0">
              <div className="font-bold text-sm leading-tight">
                {activeDeck ? 'Presentation mode' : 'AI Analyst'}
              </div>
              <div className="text-[10px] opacity-75 leading-tight truncate">
                {activeDeck
                  ? `Revising · ${activeDeck.title}`
                  : `Connected · Documents${webSearchEnabled ? " + Web" : ""}${deepThinking ? " + Deep" : ""}${reportMode ? " + Report" : ""}`}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {/* Desktop header: full button row. Mobile collapses these into a
                kebab menu below. */}
            {!isMobile && messages.length > 1 && <CopyChatButton messages={messages} />}
            {!isMobile && messages.filter(m => m.role === 'assistant' && (m.content || '').trim()).length > 0 && (
              <button
                onClick={handleChatToPresentation}
                disabled={convertingDeck}
                title="Turn this conversation into a new presentation"
                className="flex items-center gap-1 px-2 py-1 text-xs bg-white/20 rounded hover:bg-white/30 disabled:opacity-50 transition"
              >
                <DocumentChartBarIcon className="w-3.5 h-3.5" />
                <span>{convertingDeck ? "Building…" : "Presentation"}</span>
              </button>
            )}
            {!isMobile && (
              <button
                onClick={handleNewSession}
                title="New chat"
                className="flex items-center gap-1 px-2 py-1 text-xs bg-white/20 rounded hover:bg-white/30 transition"
              >
                <PlusIcon className="w-3.5 h-3.5" />
                <span>New</span>
              </button>
            )}
            {!isMobile && (
              <button onClick={() => setShowHistory(!showHistory)}
                title="Conversation history"
                className={`p-1 rounded transition ${showHistory ? "bg-white/30" : "hover:bg-white/20"}`}
              >
                <ClockIcon className="w-4 h-4 opacity-80" />
              </button>
            )}
            {!isMobile && (
              <button onClick={toggleMaximize} title={isWide ? "Shrink to default width" : "Expand to wide"} className="p-1 hover:bg-white/20 rounded transition">
                {isWide ? <ArrowsPointingInIcon className="w-4 h-4 opacity-80" /> : <ArrowsPointingOutIcon className="w-4 h-4 opacity-80" />}
              </button>
            )}
            {isMobile && (
              <button
                onClick={() => setMobileMenuOpen(true)}
                className="flex items-center justify-center min-w-[40px] min-h-[40px] hover:bg-white/20 rounded transition"
                title="Menu"
                aria-label="Open menu"
              >
                <EllipsisVerticalIcon className="w-5 h-5" />
              </button>
            )}
            <button
              onClick={() => { setIsOpen(false); setSplitDoc(null); }}
              onContextMenu={isMobile ? undefined : (e) => { e.preventDefault(); setDismissed(true); setIsOpen(false); setSplitDoc(null); }}
              className={`hover:bg-white/20 rounded transition ${isMobile ? "flex items-center justify-center min-w-[40px] min-h-[40px]" : "p-1"}`}
              title={isMobile ? "Close" : "Close (right-click to fully dismiss; Ctrl/Cmd+/ to summon)"}
              aria-label="Close chat"
            >
              <XMarkIcon className={isMobile ? "w-5 h-5" : "w-4 h-4"} />
            </button>
          </div>
        </div>

        {/* Presentation-mode strip — only rendered when bound to a deck. Lets
            the operator pick a target slide (so propose_section edits THAT
            section) and fire one-tap revision shortcuts that pre-fill the
            input. The chips fall back to "first section" when nothing is
            picked, so "Rewrite for tone" still does something useful. */}
        {activeDeck && (
          <div className="px-3 md:px-4 py-2 border-b border-violet-200 bg-violet-50 flex flex-wrap items-center gap-2 text-[11px] flex-shrink-0">
            <span className="font-semibold text-violet-800">Focus slide:</span>
            <select
              value={targetSectionId || ''}
              onChange={(e) => setTargetSectionId(e.target.value || null)}
              className="flex-1 min-w-[140px] max-w-[280px] border border-violet-300 rounded px-2 py-1 bg-white text-violet-900"
              title="Scope the AI to a specific section — proposals will default to editing THIS section"
            >
              <option value="">Whole deck</option>
              {activeDeck.sections.map(s => (
                <option key={s.id} value={s.id}>
                  {s.title?.slice(0, 50) || s.kind} {s.title && s.title.length > 50 ? '…' : ''}
                </option>
              ))}
            </select>
            {/* Quick-action chips — pre-fill the input so the user can hit Send.
                When a slide is picked, they target it; otherwise they target the
                first slide. */}
            <div className="flex flex-wrap gap-1 w-full">
              {[
                { label: 'Rewrite for tone', tmpl: 'Rewrite this section in a tighter, more readable tone. Keep all citations.' },
                { label: 'Add a chart', tmpl: 'Propose an inline_chart that summarizes the key numbers in this section.' },
                { label: 'Tighten', tmpl: 'Cut this section to ~50% length while keeping every citation and key fact.' },
                { label: 'Add citations', tmpl: 'Search the corpus and add [source: filename.pdf] citations for every unsourced claim in this section.' },
                { label: 'New section', tmpl: 'Propose a new section that logically follows the current one. Use search_chunks to ground it.' },
              ].map(chip => (
                <button
                  key={chip.label}
                  type="button"
                  onClick={() => setInput(chip.tmpl)}
                  className="px-2 py-0.5 rounded border border-violet-300 bg-white text-violet-700 hover:bg-violet-100"
                >
                  {chip.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Controls bar — Deep / Report toggles + scope filters (matches bank-processor).
            flex-wrap so on a narrow phone width an active scope chip (e.g.
            "School (HHRSD) only") wraps onto a second row instead of pushing
            the doc-scope button off-screen. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 md:px-4 py-2 border-b border-gray-200 bg-white flex-shrink-0 text-[11px]">
          <label
            className={`flex items-center gap-1.5 cursor-pointer ${deepThinking ? "font-semibold" : "text-gray-500"}`}
            style={deepThinking ? { color: brandColor } : undefined}
          >
            <input type="checkbox" checked={deepThinking} onChange={e => setDeepThinking(e.target.checked)}
              className="cursor-pointer" style={{ accentColor: brandColor, width: 13, height: 13 }} />
            <LightBulbIcon className="w-3 h-3" />
            Deep
          </label>
          <label
            className={`flex items-center gap-1.5 cursor-pointer ${webSearchEnabled ? "font-semibold" : "text-gray-500"}`}
            style={webSearchEnabled ? { color: brandColor } : undefined}
          >
            <input type="checkbox" checked={webSearchEnabled} onChange={e => setWebSearchEnabled(e.target.checked)}
              className="cursor-pointer" style={{ accentColor: brandColor, width: 13, height: 13 }} />
            <GlobeAltIcon className="w-3 h-3" />
            Web
          </label>
          <div className="flex-1" />
          <div className="relative">
            <button
              onClick={() => setShowScopePicker(v => !v)}
              className={`flex items-center gap-1 px-2 py-0.5 text-[11px] rounded border ${
                chatScope.type !== "all"
                  ? "bg-emerald-50 border-emerald-200 font-semibold"
                  : "bg-white border-gray-300 text-gray-600 hover:bg-gray-100"
              }`}
              style={chatScope.type !== "all" ? { color: brandColor } : undefined}
              title="Narrow the chat to an entity / fiscal year / document type"
            >
              <FunnelIcon className="w-3 h-3" />
              {chatScope.type === "all" ? "Scope" : chatScope.label}
            </button>
            {showScopePicker && !isMobile && (
              <div className="absolute right-0 top-full mt-1 w-60 bg-white border border-gray-200 rounded shadow-lg z-20 py-1 max-h-72 overflow-y-auto">
                {SCOPE_PRESETS.map(s => {
                  const active = s.type === chatScope.type && s.id === chatScope.id;
                  return (
                    <button
                      key={`${s.type}:${s.id || ""}`}
                      onClick={() => { setChatScope(s); setShowScopePicker(false); }}
                      className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 ${
                        active ? "font-semibold" : "text-gray-700"
                      }`}
                      style={active ? { color: brandColor } : undefined}
                    >
                      {s.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <button
            onClick={() => setShowDocPicker(!showDocPicker)}
            className={`flex items-center gap-1 px-2 py-0.5 text-[11px] rounded border ${
              selectedDoc
                ? "bg-emerald-50 border-emerald-200 font-semibold"
                : "bg-white border-gray-300 text-gray-600 hover:bg-gray-100"
            }`}
            style={selectedDoc ? { color: brandColor } : undefined}
            title="Restrict the chat to a specific document"
          >
            <DocumentTextIcon className="w-3 h-3" />
            {selectedDoc ? "1 doc" : "Scope docs"}
          </button>
        </div>

        {/* History panel */}
        {showHistory && (
          <div className="border-b border-gray-200 bg-white px-3 py-2 max-h-48 overflow-y-auto flex-shrink-0">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-gray-500">Chat History</span>
              <button onClick={handleNewSession} className="text-xs hover:opacity-80" style={{ color: brandColor }}>+ New Chat</button>
            </div>
            {sessions?.map((s: any) => (
              <button key={s.session_id} onClick={() => handleLoadSession(s.session_id)}
                className={`w-full text-left px-2 py-1.5 text-xs rounded hover:bg-gray-100 ${s.session_id === sessionId ? "text-gray-900" : "text-gray-600"}`}
                style={s.session_id === sessionId ? { backgroundColor: `${brandColor}10` } : {}}>
                <p className="truncate font-medium">{s.last_query || "Conversation"}</p>
                <p className="text-gray-400">{s.message_count} msgs &middot; {s.last_activity ? new Date(s.last_activity).toLocaleDateString() : ""}</p>
              </button>
            ))}
            {!sessions?.length && <p className="text-xs text-gray-400 py-2">No previous conversations</p>}
          </div>
        )}

        {/* Hidden file input — wired to the upload buttons below. */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg,.tif,.tiff"
          className="hidden"
          onChange={handleFileChosen}
        />

        {/* Status pill: uploading-in-progress, upload error, or selectedDoc.
            Only one of these shows at a time. */}
        {uploading ? (
          <div className="px-3 py-1.5 border-b border-gray-200 flex items-center gap-2 text-xs flex-shrink-0" style={{ backgroundColor: `${brandColor}10` }}>
            <div
              className="w-3 h-3 border-2 rounded-full animate-spin flex-shrink-0"
              style={{ borderColor: `${brandColor}40`, borderTopColor: brandColor }}
            />
            <span className="truncate flex-1" style={{ color: brandColor }}>
              Uploading {uploading.filename} ({Math.round(uploading.size / 1024)} KB)…
            </span>
          </div>
        ) : uploadError ? (
          <div className="px-3 py-1.5 border-b border-red-200 flex items-center gap-2 text-xs flex-shrink-0 bg-red-50">
            <XMarkIcon className="w-3 h-3 text-red-500 flex-shrink-0" />
            <span className="truncate flex-1 text-red-700">{uploadError}</span>
            <button onClick={() => setUploadError(null)} className="text-red-400 hover:text-red-600"><XMarkIcon className="w-3 h-3" /></button>
          </div>
        ) : selectedDoc ? (
          <div className="px-3 py-1.5 border-b border-gray-200 flex items-center gap-2 text-xs flex-shrink-0" style={{ backgroundColor: `${brandColor}10` }}>
            <LinkIcon className="w-3 h-3" style={{ color: brandColor }} />
            <span className="truncate flex-1" style={{ color: brandColor }}>{selectedDoc.filename}</span>
            <button onClick={() => setSelectedDoc(null)} className="text-gray-400 hover:text-gray-600"><XMarkIcon className="w-3 h-3" /></button>
          </div>
        ) : null}

        {/* Messages. `overscroll-contain` stops the chat's pull-to-refresh
            and rubber-band from bleeding into the underlying page scroll
            on iOS — without it, scrolling past the top/bottom of the
            messages list scrolls the route behind the chat panel and the
            user can't reach the messages they want. `touch-pan-y` keeps
            vertical-pan gestures owned by this scrollport. */}
        <div className="flex-1 overflow-y-auto overscroll-contain touch-pan-y px-4 py-3 min-h-0">
          {messages.map(msg => (
            <EnhancedMessageComponent
              key={msg.id}
              message={msg}
              brandColor={brandColor}
              onCitationClick={handleCitationClick}
              onDocClick={handleViewDoc}
              onDownload={handleDownloadMessage}
              onApplyProposal={activeDeck ? handleApplyProposal : undefined}
              onDismissProposal={activeDeck ? handleDismissProposal : undefined}
              onSendMessageToDeck={activeDeck ? handleSendMessageToDeck : undefined}
              activeDeckTitle={activeDeck?.title}
              onMessageToNewPresentation={!activeDeck && !convertingDeck ? handleMessageToNewPresentation : undefined}
            />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {showDocPicker && (
          <div className="border-t border-gray-200 bg-white px-3 py-2 max-h-56 overflow-y-auto flex-shrink-0">
            {/* Upload a new file from disk — sits at the top so it's the
                first option a user sees when they want to attach something
                that isn't already in the library. */}
            <button
              onClick={handlePickFileToUpload}
              disabled={!!uploading || isStreaming}
              className="flex items-center gap-2 w-full px-2 py-2 mb-2 text-xs rounded border border-dashed border-gray-300 hover:bg-gray-50 disabled:opacity-50"
              style={{ color: brandColor }}
            >
              <ArrowUpTrayIcon className="w-3.5 h-3.5" />
              <span className="flex-1 text-left">Upload a new file from your device</span>
              <span className="text-[10px] text-gray-400">PDF, DOCX, XLSX, images, …</span>
            </button>
            <div className="relative mb-2">
              <MagnifyingGlassIcon className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input type="text" value={docSearch} onChange={e => setDocSearch(e.target.value)} placeholder="…or search the existing library" className="w-full pl-8 pr-2 py-1.5 text-xs border border-gray-300 rounded-lg" autoFocus />
            </div>
            {docResults?.slice(0, 6).map((sr: any) => (
              <button key={sr.id} onClick={() => { setSelectedDoc({ id: sr.id, filename: sr.filename } as Document); setShowDocPicker(false); setDocSearch(""); }}
                className="flex items-center gap-2 w-full px-2 py-1.5 text-xs text-gray-700 hover:bg-gray-50 rounded">
                <DocumentTextIcon className="w-3.5 h-3.5 text-gray-400" /><span className="truncate flex-1 text-left">{sr.filename}</span>
              </button>
            ))}
          </div>
        )}

        {/* Input — toggles live in the top controls bar; this row is just
            attach + textfield + send. On mobile, only the paperclip is
            visible (which opens a picker that also offers upload-from-device). */}
        <div className="p-3 border-t border-gray-200 bg-white flex-shrink-0">
          <div className="flex gap-1.5 items-center">
            <button
              onClick={() => setShowDocPicker(!showDocPicker)}
              className={`rounded-lg border transition-colors ${isMobile ? "min-w-[44px] min-h-[44px] flex items-center justify-center" : "p-2"} ${
                showDocPicker || selectedDoc
                  ? "border-gray-400 text-gray-700 bg-gray-100"
                  : "border-gray-300 text-gray-400 hover:text-gray-600"
              }`}
              title="Attach a document (search library or upload)"
            >
              <PaperClipIcon className={isMobile ? "w-5 h-5" : "w-4 h-4"} />
            </button>
            {!isMobile && (
              <button
                onClick={handlePickFileToUpload}
                disabled={!!uploading || isStreaming}
                className={`p-2 rounded-lg border transition-colors ${
                  uploading
                    ? "border-gray-400 text-gray-700 bg-gray-100"
                    : "border-gray-300 text-gray-400 hover:text-gray-600 disabled:opacity-50"
                }`}
                title="Upload a file from your device"
              >
                {uploading ? (
                  <div
                    className="w-4 h-4 border-2 rounded-full animate-spin"
                    style={{ borderColor: `${brandColor}40`, borderTopColor: brandColor }}
                  />
                ) : (
                  <ArrowUpTrayIcon className="w-4 h-4" />
                )}
              </button>
            )}
            <input
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleSend()}
              placeholder={
                selectedDoc ? `Ask about ${selectedDoc.filename}...`
                : deepThinking ? "Ask a deep analytical question..."
                : webSearchEnabled ? "Ask anything (+ web search)..."
                : `Ask about ${municipality.short_name || "local"} documents...`
              }
              className={`flex-1 rounded-xl border border-gray-300 px-3 ${isMobile ? "py-2.5 text-base" : "py-2 text-sm"} focus:ring-2 focus:border-transparent`}
              style={{ "--tw-ring-color": brandColor } as React.CSSProperties}
              disabled={isStreaming}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || isStreaming}
              className={`text-white rounded-xl hover:opacity-90 disabled:opacity-50 shadow-lg ${isMobile ? "min-w-[44px] min-h-[44px] flex items-center justify-center" : "px-3 py-2"}`}
              style={{ backgroundColor: brandColor }}
              aria-label="Send"
            >
              <PaperAirplaneIcon className={isMobile ? "w-5 h-5" : "w-4 h-4"} />
            </button>
          </div>
        </div>
      </div>

      {/* Inline split document viewer (desktop only — mobile renders this as
          a separate fullscreen overlay below). */}
      {showInlineSplit && splitDoc && (
        <div className="flex-1 border-l border-gray-200 flex flex-col bg-white min-w-0">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
            <span className="text-xs font-medium text-gray-600 truncate flex-1">{splitDoc.filename}</span>
            <button onClick={() => setSplitDoc(null)} className="p-1 text-gray-400 hover:text-gray-600 rounded"><XMarkIcon className="w-4 h-4" /></button>
          </div>
          <div className="flex-1 overflow-hidden">
            <iframe src={splitDoc.url} className="w-full h-full border-0" title={splitDoc.filename} />
          </div>
        </div>
      )}
    </div>

    {/* Mobile-only scope picker bottom sheet. On a phone the inline dropdown
        would either open upward into the header or off the edge of the
        viewport, so we promote it to a sheet matching the kebab menu. */}
    {isMobile && showScopePicker && (
      <div
        className="fixed inset-0 z-[55] bg-black/40"
        onClick={() => setShowScopePicker(false)}
      >
        <div
          className="absolute bottom-0 left-0 right-0 bg-white rounded-t-2xl shadow-xl max-h-[70vh] overflow-y-auto"
          style={{ paddingBottom: 'max(env(safe-area-inset-bottom), 0.5rem)' }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex justify-center pt-2 pb-1">
            <div className="w-10 h-1 rounded-full bg-gray-300" />
          </div>
          <div className="px-3 pb-2">
            <div className="px-2 pb-2 text-[11px] uppercase tracking-wide text-gray-500 font-semibold">Scope this chat</div>
            {SCOPE_PRESETS.map(s => {
              const active = s.type === chatScope.type && s.id === chatScope.id;
              return (
                <button
                  key={`${s.type}:${s.id || ""}`}
                  onClick={() => { setChatScope(s); setShowScopePicker(false); }}
                  className={`flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50 ${
                    active ? "bg-emerald-50" : ""
                  }`}
                >
                  <FunnelIcon className="w-5 h-5 text-gray-500" />
                  <span className={`text-sm ${active ? "font-semibold" : "text-gray-800"}`}
                    style={active ? { color: brandColor } : undefined}>
                    {s.label}
                  </span>
                  {active && <CheckIcon className="w-4 h-4 ml-auto" style={{ color: brandColor }} />}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    )}

    {/* Mobile-only kebab menu sheet. Renders as a bottom sheet over the chat. */}
    {isMobile && mobileMenuOpen && (
      <div
        className="fixed inset-0 z-[55] bg-black/40"
        onClick={() => setMobileMenuOpen(false)}
      >
        <div
          className="absolute bottom-0 left-0 right-0 bg-white rounded-t-2xl shadow-xl"
          style={{ paddingBottom: 'max(env(safe-area-inset-bottom), 0.5rem)' }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex justify-center pt-2 pb-1">
            <div className="w-10 h-1 rounded-full bg-gray-300" />
          </div>
          <div className="px-2 pb-2">
            <button
              onClick={() => { handleNewSession(); setMobileMenuOpen(false); }}
              className="flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50"
            >
              <PlusIcon className="w-5 h-5 text-gray-600" />
              <span className="text-sm font-medium text-gray-800">New chat</span>
            </button>
            <button
              onClick={() => { setShowHistory(true); setMobileMenuOpen(false); }}
              className="flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50"
            >
              <ClockIcon className="w-5 h-5 text-gray-600" />
              <span className="text-sm font-medium text-gray-800">Conversation history</span>
            </button>
            {messages.length > 1 && (
              <button
                onClick={() => {
                  const text = messages
                    .filter(m => m.content)
                    .map(m => `[${m.role}] ${m.content}`)
                    .join("\n\n");
                  navigator.clipboard?.writeText(text).catch(() => {});
                  setMobileMenuOpen(false);
                }}
                className="flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50"
              >
                <ClipboardDocumentIcon className="w-5 h-5 text-gray-600" />
                <span className="text-sm font-medium text-gray-800">Copy conversation</span>
              </button>
            )}
            {messages.filter(m => m.role === 'assistant' && (m.content || '').trim()).length > 0 && (
              <button
                onClick={() => { handleChatToPresentation(); setMobileMenuOpen(false); }}
                disabled={convertingDeck}
                className="flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                <DocumentChartBarIcon className="w-5 h-5 text-gray-600" />
                <span className="text-sm font-medium text-gray-800">
                  {convertingDeck ? "Building presentation…" : "Convert to presentation"}
                </span>
              </button>
            )}
            <div className="border-t border-gray-100 my-1" />
            <button
              onClick={() => {
                setDismissed(true);
                setIsOpen(false);
                setSplitDoc(null);
                setMobileMenuOpen(false);
              }}
              className="flex items-center gap-3 w-full px-3 py-3 text-left rounded-lg hover:bg-gray-50"
            >
              <EyeSlashIcon className="w-5 h-5 text-gray-600" />
              <div>
                <div className="text-sm font-medium text-gray-800">Hide chat</div>
                <div className="text-[11px] text-gray-500">Re-open from More menu</div>
              </div>
            </button>
          </div>
        </div>
      </div>
    )}

    {/* Mobile-only document popup. FilePreviewModal handles fullscreen layout,
        PDF rendering, zoom, swipe page-nav, etc. — same modal used elsewhere
        in the app for consistency. Closing returns to the chat with state
        preserved. */}
    {isMobile && splitDoc && (
      <FilePreviewModal
        isOpen
        url={splitDoc.url}
        filename={splitDoc.filename}
        onClose={() => setSplitDoc(null)}
      />
    )}
    </>
  );
}


/** Header button: copy the entire chat transcript to clipboard. */
function CopyChatButton({ messages }: { messages: ChatMessage[] }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const text = messages
      .filter(m => m.role !== "error")
      .map(m => {
        const role = m.role === "user" ? "USER" : "ASSISTANT";
        return `[${role}]\n${m.content}`;
      })
      .join("\n\n---\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };
  return (
    <button
      onClick={handleCopy}
      title={copied ? "Copied" : "Copy entire chat"}
      className="flex items-center gap-1 px-2 py-1 text-xs bg-white/20 rounded hover:bg-white/30 transition"
    >
      {copied ? <CheckIcon className="w-3.5 h-3.5" /> : <ClipboardDocumentIcon className="w-3.5 h-3.5" />}
      <span>{copied ? "Copied" : "Copy Chat"}</span>
    </button>
  );
}
