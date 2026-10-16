"""
Deck-aware AI chat. Streams Claude responses that can either:
  1. Answer free-form questions about the deck (no proposal), or
  2. Emit a `propose_*` tool call — a structured patch the editor applies.

The frontend AskAIPanel (and GlobalChat in deck mode) renders proposal events
as accept/reject cards. Each proposal SSE payload carries a `proposal_type`
discriminator so the editor can dispatch to the right apply handler.

Tools exposed to Claude
-----------------------
Read tools (deterministic, executed server-side):
  - search_chunks                  RAG over indexed AH documents
  - read_deck                      Return the live deck sections (compact JSON)
  - read_react_component_source    Fetch full TSX for one react_component section

Propose tools (each emits a `proposal` SSE event for the editor):
  - propose_section_edit           Replace body of an existing section
  - propose_new_section            Insert a new narrative section
  - propose_inline_chart           Append a ```chart fenced block to a section
  - propose_diagram                Append a ```mermaid fenced block to a section
  - propose_react_component        Add or replace a react_component section
  - propose_section_data_edit      Replace `data` on a react_component section
  - propose_section_data_patch     Surgical patches to `data` on react_component

Legacy:
  - propose_section                Catch-all (narrative/table/react_component).
                                   Kept for back-compat with older AskAIPanel
                                   clients; new clients use propose_section_edit
                                   / propose_new_section instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

import anthropic
from sqlalchemy.orm import Session

from config import ANTHROPIC_API_KEY
from database import SessionLocal
from services.rag_search import search_chunks

log = logging.getLogger(__name__)



def _system_prompt() -> str:
    from municipality import get_municipality
    muni = get_municipality()
    # str.replace, not .format — the prompt contains literal JSON braces.
    return SYSTEM_PROMPT.replace("{place}", f"{muni.short_name}, {muni.state}")


SYSTEM_PROMPT = """You are an expert analyst helping the user build a presentation about {place} municipal affairs.

You can:
  1. Answer questions about the deck or the {place} document corpus.
  2. Propose section content via the propose_* tools.

GROUND RULES
- ALWAYS call read_deck first when asked to revise/shorten/expand/cite an existing section.
- Always call search_chunks to ground claims in indexed AH documents before drafting.
- NEVER mutate the deck directly. Use a propose_* tool and let the user Apply or Discard:
    propose_section_edit       — replace an existing narrative section's body
    propose_new_section        — insert a brand new narrative section
    propose_inline_chart       — bar/line/pie/area chart from a small table
    propose_diagram            — Mermaid flowchart / org / sequence / swim-lane
    propose_react_component    — emit / replace a TSX component section
    propose_section_data_edit  — patch a react_component's `data` (cheap, can't break TSX)
    propose_section_data_patch — surgical patches to a react_component's `data` when it is large

Use `[source: filename.pdf]` citations in narrative bodies for every fact.

## Section kinds

- `narrative`: Markdown prose. Include `[source: filename.pdf]` citations for every fact.
- `table`: Comparison table. Provide `headers` and `rows`.
- `react_component`: A React component for custom visualizations (charts, KPI tiles, layouts).
                     Provide `tsx`. See the react_component scope rules below.

## react_component scope

The TSX runs in a sandboxed `react-live` environment. You have these globals — DO NOT write `import` statements:

- React + hooks: `useState`, `useMemo`, `useCallback`, `useEffect`
- Recharts: `ResponsiveContainer`, `LineChart`, `BarChart`, `PieChart`, `AreaChart`, `Line`, `Bar`, `Pie`, `Area`, `XAxis`, `YAxis`, `CartesianGrid`, `Tooltip`, `Legend`, `Cell`
- Lucide icons: `TrendingUp`, `TrendingDown`, `AlertTriangle`, `CheckCircle2`, `Calendar`, `DollarSign`, `Users`, `FileText`, `Globe`
- AH primitives: `KPICard({label, value, delta?, trend?, sub?})`, `Callout({kind, title?, children})`, `Stat({label, value})`, `Section({title?, children})`
- `BRAND` constant: "#385854" (AH brand color, use for chart series)

NO `window`, `fetch`, `document`, `localStorage`. Any reference throws a clean ReferenceError.

Numeric formatting rule (mandatory): any formatted number/currency string must sit inside an element with
both `whitespace-nowrap` AND `tabular-nums` classes — without `whitespace-nowrap` the value wraps at the comma
when the container narrows.

## react_component data separation

If a react_component section has a `data` payload, the TSX should read from it as the `data` identifier rather
than re-inlining arrays of rows. Smaller TSX = far fewer parse errors and the user can edit the data without
regenerating code.

For data-only updates (changing values, status, adding/removing rows), use:
  - propose_section_data_edit  — replace the full `data` object
  - propose_section_data_patch — surgical patches when `data` is large (>20KB)

For layout / column / new computed field changes, call read_react_component_source(section_id) FIRST to fetch
the full TSX, then propose_react_component with the SAME `name` (the editor offers a Replace affordance when
names match — the existing `data` is preserved).
"""


def _tool_defs() -> list[dict]:
    """Tool definitions exposed to Claude. Order matters for discoverability —
    read tools above propose tools so the model reaches for them first."""
    return [
        # ── Read tools (server-side) ─────────────────────────────────────────
        {
            "name": "search_chunks",
            "description": "Search passages across the indexed municipal document corpus.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "read_deck",
            "description": (
                "Return the live sections of the deck (id, kind, title, body excerpt, "
                "headers/rows for tables, deps_used + truncated tsx for react_component "
                "sections, data summary). Call this first when asked to revise / shorten / "
                "expand / cite an existing section so you operate on the actual content."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_react_component_source",
            "description": (
                "Fetch the FULL untruncated TSX source of one react_component section. "
                "read_deck only includes the first ~800 chars of TSX (enough for data-only "
                "edits); call this when you need to modify LAYOUT, COLUMNS, RENDER LOGIC, "
                "or APPEND UI. Returns {section_id, name, tsx, deps_used, has_data, data_size_bytes}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Target react_component section id."},
                },
                "required": ["section_id"],
            },
        },

        # ── Propose tools (emit `proposal` SSE event) ────────────────────────
        {
            "name": "propose_section_edit",
            "description": (
                "Replace the body of an existing section. The user sees the proposed markdown "
                "and decides whether to apply. Use when revising / expanding / shortening / "
                "rewriting an existing narrative section."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Target section id from read_deck."},
                    "new_markdown": {"type": "string", "description": "Full replacement body in markdown."},
                    "rationale": {"type": "string"},
                },
                "required": ["section_id", "new_markdown"],
            },
        },
        {
            "name": "propose_new_section",
            "description": (
                "Insert a new narrative section. Use when the user asks for a missing topic, "
                "or when material doesn't fit any existing section."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "markdown": {"type": "string"},
                    "after_section_id": {
                        "type": "string",
                        "description": "If set, the new section is inserted right after this id; otherwise it lands at the end.",
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["heading", "markdown"],
            },
        },
        {
            "name": "propose_inline_chart",
            "description": (
                "Append a chart block inline at the end of an existing section's body. The "
                "chart renders both in the web viewer (Recharts) and in the PPTX export. "
                "Provide `headers` and `rows` for the data — chart_type controls visualization."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "stacked_bar", "area"]},
                    "title": {"type": "string"},
                    "x": {"type": "string", "description": "Column name for x axis."},
                    "y": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names for y values.",
                    },
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                    "rationale": {"type": "string"},
                },
                "required": ["section_id", "chart_type", "headers", "rows"],
            },
        },
        {
            "name": "propose_diagram",
            "description": (
                "Append a Mermaid diagram (flowchart, org chart, sequence, swim lane, etc.) "
                "inline at the end of a section's body. You write the Mermaid source; the "
                "viewer renders to interactive SVG and the PPTX export rasterizes. Use for "
                "ANY structural / process / relationship visualization — Mermaid is far "
                "more readable than a wall of prose for these."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "source": {
                        "type": "string",
                        "description": "Full Mermaid source (e.g. 'flowchart TD\\n A[Start] --> B[End]'). No code fences.",
                    },
                    "theme": {"type": "string", "enum": ["default", "dark", "forest", "neutral"]},
                    "rationale": {"type": "string"},
                },
                "required": ["section_id", "source"],
            },
        },
        {
            "name": "propose_react_component",
            "description": (
                "Emit a self-contained TSX React component as a new section, OR replace an "
                "existing react_component section's TSX. To replace: pass `section_id` (and "
                "use the same `name` as the existing component). The user sees a live preview "
                "before deciding. See the system prompt for the curated scope and rules."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {
                        "type": "string",
                        "description": "Target section id to replace; omit for a brand-new section.",
                    },
                    "name": {
                        "type": "string",
                        "description": "PascalCase component name (e.g. 'BudgetSnapshot'). Used as the section heading.",
                    },
                    "tsx": {
                        "type": "string",
                        "description": "Full TSX source. Must define a default-exported function component. NO import statements.",
                    },
                    "deps_used": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Identifiers from the curated allow-list this component references.",
                    },
                    "data": {
                        "description": "Optional structured data payload exposed to the TSX as the `data` identifier.",
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["name", "tsx"],
            },
        },
        {
            "name": "propose_section_data_edit",
            "description": (
                "Replace the `data` payload of a react_component section WITHOUT touching "
                "its TSX. Use when the user asks to fix / add / remove / update rows in a "
                "data-driven component. The TSX reads from `data.*` so a pure-data edit "
                "takes effect on the next render — no need to regenerate the component.\n\n"
                "STRICT: pass the COMPLETE new data object as `new_data`. Read the current "
                "data from read_deck (it's serialized into the section body) and send back "
                "the whole thing with your edits applied. Dropping a key the component "
                "reads will produce a runtime error in the user's preview."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Target react_component section id."},
                    "new_data": {
                        "description": "The COMPLETE replacement object that should become `content.data`.",
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["section_id", "new_data"],
            },
        },
        {
            "name": "propose_section_data_patch",
            "description": (
                "Patch a react_component's `data` WITHOUT re-emitting the full payload. Use "
                "when the data is large (>20KB) and you're only changing a few rows. "
                "Operations apply in order: array_patches → scalar_set → scalar_unset → "
                "appends → removes. At least one operation must be non-empty."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Target react_component section id."},
                    "array_patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Field name on data, e.g. 'loans'"},
                                "key_field": {"type": "string"},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "key": {"description": "Value to match element[key_field] against"},
                                            "set": {"type": "object"},
                                            "unset": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["key"],
                                    },
                                },
                            },
                            "required": ["path", "key_field", "items"],
                        },
                    },
                    "scalar_set": {"type": "object"},
                    "scalar_unset": {"type": "array", "items": {"type": "string"}},
                    "appends": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "items": {"type": "array"},
                            },
                            "required": ["path", "items"],
                        },
                    },
                    "removes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "key_field": {"type": "string"},
                                "keys": {"type": "array"},
                            },
                            "required": ["path", "key_field", "keys"],
                        },
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["section_id"],
            },
        },

        # ── Legacy catch-all ─────────────────────────────────────────────────
        {
            "name": "propose_section",
            "description": (
                "Legacy: propose a section by kind (narrative/table/react_component). Kept "
                "for back-compat. Prefer propose_section_edit / propose_new_section / "
                "propose_react_component for new code."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["narrative", "table", "react_component"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "caption": {"type": "string"},
                    "tsx": {"type": "string"},
                    "data": {},
                    "rationale": {"type": "string"},
                },
                "required": ["kind", "title"],
            },
        },
    ]


# ─── Tool executors (server-side reads) ─────────────────────────────────────

def _exec_search_chunks(db: Session, args: dict) -> dict:
    # Same ranking pipeline as /api/search and the main chat.
    from routes.search import rank_chunks
    top_k = min(int(args.get("top_k", 8)), 20)
    rows = rank_chunks(db, args["query"], pool_size=max(top_k * 3, 30))[0][:top_k]
    return {
        "count": len(rows),
        "chunks": [
            {
                "filename": r["filename"],
                "title": r.get("title"),
                "doc_date": r.get("doc_date"),
                "municipality": r.get("municipality"),
                "fiscal_year": r.get("fiscal_year"),
                "doc_type": r.get("doc_type"),
                "content": (r.get("content") or "")[:1500],
            }
            for r in rows
        ],
    }


def _exec_read_deck(db: Session, presentation_id: Optional[str]) -> dict:
    """Return a compact JSON view of the live deck sections. The model uses
    this to find target section ids and verify what's actually in the deck."""
    if not presentation_id:
        return {"error": "no_active_deck"}
    from models.presentation import Presentation
    p = db.query(Presentation).filter(Presentation.id == presentation_id).first()
    if not p:
        return {"error": "deck_not_found"}
    sections_out = []
    for s in (p.sections or []):
        kind = s.get("kind") or "narrative"
        item: dict = {
            "id": s.get("id"),
            "kind": kind,
            "title": s.get("title") or "(untitled)",
        }
        if kind == "narrative":
            body = s.get("body") or ""
            item["body"] = body if len(body) <= 2000 else (body[:1900] + "\n…[truncated]")
            item["body_len"] = len(body)
        elif kind == "table":
            item["headers"] = s.get("headers") or []
            item["row_count"] = len(s.get("rows") or [])
            # Include first few rows for context (not all — could be huge)
            item["rows_preview"] = (s.get("rows") or [])[:5]
        elif kind == "react_component":
            tsx = s.get("tsx") or ""
            item["tsx_excerpt"] = tsx[:800]
            item["tsx_len"] = len(tsx)
            data = s.get("data")
            if data is not None:
                try:
                    data_str = json.dumps(data)
                    item["has_data"] = True
                    item["data_size_bytes"] = len(data_str)
                    item["data_preview"] = data_str[:1500] if len(data_str) > 1500 else data_str
                except Exception:
                    item["has_data"] = True
                    item["data_size_bytes"] = -1
        elif kind == "attachment":
            item["attachment_id"] = s.get("attachment_id")
            item["caption"] = s.get("caption")
        sections_out.append(item)
    return {
        "deck_id": str(p.id),
        "title": p.title,
        "section_count": len(sections_out),
        "sections": sections_out,
    }


def _exec_read_react_component_source(db: Session, presentation_id: Optional[str], args: dict) -> dict:
    """Return the full TSX of a single react_component section."""
    if not presentation_id:
        return {"error": "no_active_deck"}
    section_id = args.get("section_id")
    if not section_id:
        return {"error": "missing_section_id"}
    from models.presentation import Presentation
    p = db.query(Presentation).filter(Presentation.id == presentation_id).first()
    if not p:
        return {"error": "deck_not_found"}
    target = None
    for s in (p.sections or []):
        if s.get("id") == section_id:
            target = s
            break
    if not target:
        return {"error": "section_not_found", "section_id": section_id}
    if (target.get("kind") or "") != "react_component":
        return {"error": "not_a_react_component", "section_id": section_id, "kind": target.get("kind")}
    tsx = target.get("tsx") or ""
    data = target.get("data")
    return {
        "section_id": section_id,
        "name": target.get("title") or "Component",
        "tsx": tsx,
        "deps_used": target.get("deps_used") or [],
        "has_data": data is not None,
        "data_size_bytes": len(json.dumps(data)) if data is not None else 0,
    }


# ─── SSE framing ────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    payload = {"type": event, **data}
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _proposal_event(proposal_type: str, payload: dict) -> str:
    """Wrap a propose_* tool call as a `proposal` SSE event with a
    `proposal_type` discriminator the editor uses to dispatch."""
    return _sse("proposal", {"input": {"proposal_type": proposal_type, **payload}})


# ─── Streaming entry point ──────────────────────────────────────────────────

async def stream_deck_chat(
    user_message: str,
    sections_summary: str,
    history: Optional[list[dict]] = None,
    user_id: Optional[str] = None,
    presentation_id: Optional[str] = None,
    target_section_id: Optional[str] = None,
    deep_thinking: bool = False,
    attachments: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
    if not ANTHROPIC_API_KEY:
        yield _sse("error", {"content": "ANTHROPIC_API_KEY not configured"})
        return

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    tools = _tool_defs()
    # Cache the tool defs — identical across the 6-iteration agentic loop.
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

    # deep_thinking flips the model + adds adaptive thinking + effort=high.
    # Adaptive thinking is the only supported on-mode on Opus 4.7; effort
    # lives inside output_config, not at the top level.
    if deep_thinking:
        model = "claude-opus-4-7"
        extra_kwargs: dict = {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
        }
        max_tokens = 16000
    else:
        model = "claude-sonnet-4-6"
        extra_kwargs = {}
        max_tokens = 12000

    # Split into a stable block (cached every request) + a variable block
    # (cached per-deck — hits cache on follow-up turns about the same deck).
    deck_block = "## CURRENT DECK\n\n" + sections_summary
    if target_section_id:
        deck_block += (
            f"\n\n## FOCUS\nThe operator is currently editing section "
            f"`{target_section_id}`. When proposing edits, default to "
            f"targeting THIS section unless the request is clearly about "
            f"a different one. Prefer `propose_section_edit` over "
            f"`propose_new_section` for edits to this section."
        )
    system = [
        {"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": deck_block, "cache_control": {"type": "ephemeral"}},
    ]

    # Build the first user turn — if attachments came along, we send them
    # as content blocks alongside the text, so Claude sees them directly.
    if attachments:
        first_user_content: list[dict] = []
        for a in attachments:
            atype = a.get("type")
            mt = a.get("media_type") or ""
            b64 = a.get("data_base64") or ""
            if not b64:
                continue
            if atype == "image":
                first_user_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": b64},
                })
            elif atype == "document":
                first_user_content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": mt or "application/pdf", "data": b64},
                })
        first_user_content.append({"type": "text", "text": user_message})
        first_user_turn: dict = {"role": "user", "content": first_user_content}
    else:
        first_user_turn = {"role": "user", "content": user_message}

    messages: list[dict] = list(history or [])[-20:]
    messages.append(first_user_turn)

    total_in = 0
    total_out = 0

    def _record_usage():
        if total_in == 0 and total_out == 0:
            return
        sess = SessionLocal()
        try:
            from services.usage import record_usage
            record_usage(
                sess, source="deck_chat", model=model,
                input_tokens=total_in, output_tokens=total_out,
                user_id=user_id, resource_type="presentation", resource_id=presentation_id,
            )
        finally:
            sess.close()

    # Tool dispatcher — for each tool call, returns (sse_events_to_yield, tool_result_payload)
    def _dispatch_tool(tb) -> tuple[list[str], dict]:
        name = tb.name
        args = tb.input or {}

        # Propose tools: emit a proposal event with proposal_type discriminator
        # and ack with a short tool_result so the model knows it landed.
        propose_map = {
            "propose_section_edit": "section_edit",
            "propose_new_section": "new_section",
            "propose_inline_chart": "inline_chart",
            "propose_diagram": "diagram",
            "propose_react_component": "react_component",
            "propose_section_data_edit": "section_data_edit",
            "propose_section_data_patch": "section_data_patch",
        }
        if name in propose_map:
            ptype = propose_map[name]
            ev = _proposal_event(ptype, args)
            return ([ev], {"ok": True, "ack": f"Proposal '{ptype}' sent to editor."})

        # Legacy propose_section: surface as proposal with kind in the payload
        # (frontend already handles this shape).
        if name == "propose_section":
            ev = _sse("proposal", {"input": args})
            return ([ev], {"ok": True, "ack": "Proposal sent to editor."})

        # Read tools — execute server-side and return result to the model.
        if name == "search_chunks":
            sess = SessionLocal()
            try:
                res = _exec_search_chunks(sess, args)
            finally:
                sess.close()
            summary = _sse("tool_result", {"name": name, "summary": f"Found {res['count']} passages"})
            return ([summary], res)

        if name == "read_deck":
            sess = SessionLocal()
            try:
                res = _exec_read_deck(sess, presentation_id)
            finally:
                sess.close()
            summary = _sse("tool_result", {
                "name": name,
                "summary": f"{res.get('section_count', 0)} sections" if "section_count" in res else res.get("error", "?"),
            })
            return ([summary], res)

        if name == "read_react_component_source":
            sess = SessionLocal()
            try:
                res = _exec_read_react_component_source(sess, presentation_id, args)
            finally:
                sess.close()
            if "error" in res:
                summary = _sse("tool_result", {"name": name, "summary": res["error"]})
            else:
                summary = _sse("tool_result", {
                    "name": name,
                    "summary": f"{res['name']} — {len(res['tsx'])} chars TSX",
                })
            return ([summary], res)

        return ([], {"error": f"unknown_tool:{name}"})

    # Note: the Anthropic SDK retries 5xx errors twice by default (with
    # exponential backoff) before raising. We don't add another retry tier
    # because streaming-context retries would risk duplicating already-
    # yielded events — if the SDK retries surface here, surface the error.
    try:
        for _ in range(6):
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                **extra_kwargs,
            ) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)
                    if et == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", None) == "tool_use":
                            yield _sse("tool_use", {"name": block.name})
                    elif et == "content_block_delta":
                        delta = event.delta
                        dt = getattr(delta, "type", None)
                        if dt == "text_delta":
                            yield _sse("delta", {"content": delta.text})
                        elif dt == "thinking_delta":
                            # Adaptive thinking with display="summarized" emits
                            # human-readable progress here; pipe to the panel so
                            # it can render a disclosure.
                            txt = getattr(delta, "thinking", "") or ""
                            if txt:
                                yield _sse("thinking", {"content": txt})

                final = await stream.get_final_message()
                total_in += getattr(final.usage, "input_tokens", 0) or 0
                total_out += getattr(final.usage, "output_tokens", 0) or 0
                tool_blocks = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
                if not tool_blocks or final.stop_reason != "tool_use":
                    _record_usage()
                    yield _sse("done", {})
                    return

                results = []
                for tb in tool_blocks:
                    events, payload = await asyncio.to_thread(_dispatch_tool, tb)
                    for ev in events:
                        yield ev
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": json.dumps(payload)[:30000],
                    })

                messages.append({"role": "assistant", "content": final.content})
                messages.append({"role": "user", "content": results})

        _record_usage()
        yield _sse("done", {"reason": "iteration_limit"})

    except anthropic.APIStatusError as exc:
        log.error("Deck chat API error: %s", exc)
        _record_usage()
        yield _sse("error", {"content": str(exc)[:300]})
    except Exception as exc:
        log.exception("Deck chat failed")
        _record_usage()
        yield _sse("error", {"content": str(exc)[:300]})


def build_sections_summary(sections: list) -> str:
    """Compact summary of the current deck so Claude knows what's there.
    For a fuller view (including TSX excerpts + data previews) the model
    should call read_deck."""
    if not sections:
        return "(empty deck — no sections yet)"
    lines = []
    for i, s in enumerate(sections):
        sid = s.get("id", f"sec_{i}")
        kind = s.get("kind", "?")
        title = s.get("title", "(untitled)")
        body_preview = ""
        if kind == "narrative":
            body_preview = (s.get("body") or "")[:200]
        elif kind == "table":
            body_preview = f"{len(s.get('headers', []))} cols × {len(s.get('rows', []))} rows"
        elif kind == "react_component":
            tsx = s.get("tsx") or ""
            has_data = s.get("data") is not None
            body_preview = f"react_component, {len(tsx)} chars TSX{', has data' if has_data else ''}"
        elif kind == "attachment":
            body_preview = f"attachment {s.get('attachment_id', '?')}"
        lines.append(f"- [{sid}] {kind}: **{title}** — {body_preview}")
    return "\n".join(lines)
