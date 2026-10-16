"use client";

/**
 * DeckChatContext — wires the global chat (FAB) to whichever presentation
 * editor is currently mounted, so AI proposals from the global chat can
 * land directly in the active deck.
 *
 * Mount this provider once near the root (Providers.tsx). The presentation
 * editor calls `bindActiveDeck(...)` when it mounts, providing the deck id
 * and a callback the chat can invoke to apply a proposal. GlobalChat reads
 * `activeDeck` to know whether to send the deck-mode flag.
 *
 * Adapted from bank-processor's DeckChatContext (which also bridges
 * proposals from RKCAIChatPanel to PresentationEditor).
 */
import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

export type SectionKind = "narrative" | "table" | "attachment" | "react_component";

/**
 * Discriminator for the richer propose_* tool family ported from
 * bank-processor's deck_chat_service. When absent (legacy `propose_section`
 * tool call), the editor falls back to the create-or-replace-section path
 * driven by `kind` + `section_id`.
 */
export type ProposalType =
  | "section_edit"
  | "new_section"
  | "inline_chart"
  | "diagram"
  | "react_component"
  | "section_data_edit"
  | "section_data_patch";

export type ChartType = "bar" | "line" | "pie" | "stacked_bar" | "area";

export interface DataPatchArrayItem {
  key: unknown;
  set?: Record<string, unknown>;
  unset?: string[];
}
export interface DataPatchArrayPatch {
  path: string;
  key_field: string;
  items: DataPatchArrayItem[];
}
export interface DataPatchAppend {
  path: string;
  items: unknown[];
}
export interface DataPatchRemove {
  path: string;
  key_field: string;
  keys: unknown[];
}

export interface DeckProposal {
  /** Discriminator for the richer propose_* tools. */
  proposal_type?: ProposalType;
  /** If present, REPLACE this existing section. If absent, APPEND a new one. */
  section_id?: string;
  kind?: SectionKind;
  title?: string;
  body?: string;
  headers?: string[];
  rows?: string[][];
  caption?: string;
  tsx?: string;
  data?: unknown;
  rationale?: string;

  // section_edit fields
  new_markdown?: string;

  // new_section fields
  heading?: string;
  markdown?: string;
  after_section_id?: string;

  // inline_chart fields
  chart_type?: ChartType;
  x?: string;
  y?: string[];

  // diagram fields
  source?: string;
  theme?: "default" | "dark" | "forest" | "neutral";

  // react_component fields
  name?: string;
  deps_used?: string[];

  // section_data_edit fields
  new_data?: unknown;

  // section_data_patch fields
  array_patches?: DataPatchArrayPatch[];
  scalar_set?: Record<string, unknown>;
  scalar_unset?: string[];
  appends?: DataPatchAppend[];
  removes?: DataPatchRemove[];
}

export interface ActiveDeck {
  id: string;
  title: string;
  /** Compact textual summary the chat backend uses as system context. */
  summary: string;
  /** Sections, in deck order — surface in the chat as a section picker so
   *  the operator can scope a query/proposal to a specific section. */
  sections: { id: string; title: string; kind: SectionKind }[];
  /** Editor-supplied callback to apply a proposal. Returns true if applied. */
  applyProposal: (p: DeckProposal) => Promise<boolean> | boolean;
}

interface DeckChatContextValue {
  activeDeck: ActiveDeck | null;
  bindActiveDeck: (deck: ActiveDeck | null) => void;
  /** Called by GlobalChat when a deck-aware proposal SSE event arrives. */
  applyProposal: (p: DeckProposal) => Promise<boolean>;
}

const DeckChatContext = createContext<DeckChatContextValue | undefined>(undefined);


export function DeckChatProvider({ children }: { children: React.ReactNode }) {
  const [activeDeck, setActiveDeck] = useState<ActiveDeck | null>(null);
  // Keep the latest activeDeck in a ref so applyProposal sees the up-to-date
  // value when called from inside an async stream reader (avoids stale closures).
  const ref = useRef<ActiveDeck | null>(null);

  const bindActiveDeck = useCallback((deck: ActiveDeck | null) => {
    ref.current = deck;
    setActiveDeck(deck);
  }, []);

  const applyProposal = useCallback(async (p: DeckProposal): Promise<boolean> => {
    const cur = ref.current;
    if (!cur) {
      console.warn("DeckChatContext: applyProposal called with no active deck");
      return false;
    }
    try {
      const res = await cur.applyProposal(p);
      return Boolean(res);
    } catch (err) {
      console.error("DeckChatContext: applyProposal failed", err);
      // Re-throw so callers can surface the real reason (e.g. 422 / 500)
      // instead of all errors collapsing to a generic "couldn't add".
      throw err;
    }
  }, []);

  const value = useMemo<DeckChatContextValue>(
    () => ({ activeDeck, bindActiveDeck, applyProposal }),
    [activeDeck, bindActiveDeck, applyProposal],
  );

  return <DeckChatContext.Provider value={value}>{children}</DeckChatContext.Provider>;
}


export function useDeckChat(): DeckChatContextValue {
  const ctx = useContext(DeckChatContext);
  if (!ctx) {
    // Soft-fail when the provider isn't mounted (e.g. tests). Return a no-op
    // surface so callers don't have to null-check everywhere.
    return {
      activeDeck: null,
      bindActiveDeck: () => {},
      applyProposal: async () => false,
    };
  }
  return ctx;
}
