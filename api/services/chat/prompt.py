"""
System prompt for the chat assistant, built from the deployment profile.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from municipality import get_municipality
from services.rag_search import get_document_excerpt


# ─── System prompts ──────────────────────────────────────────────────────────

def _background() -> str:
    """Who the assistant is and what it knows — from the municipality profile."""
    muni = get_municipality()
    head = (f"You are the research assistant for {muni.name}, {muni.state}: you help residents, reporters and "
            "officials find and understand the public record. You are precise, plain-spoken and neutral.")
    parts = [head]
    if muni.ai.background.strip():
        parts.append(muni.ai.background.strip())
    return "\n\n".join(parts)


WORKING_INSTRUCTIONS = """## How to answer

You answer questions about public records. The records are in a search index you reach through tools; you have no other knowledge of this government's documents.

1. SEARCH FIRST. Call `search_chunks` with the user's question (rephrase into the words a resolution, budget or minutes would use if helpful). Run 2–3 searches with different wording before concluding something isn't there. Use `read_document` when a passage points to a document you need more of.
2. ANSWER FROM WHAT THE TOOLS RETURNED. Every dollar amount, date, vote, name or ordinance number comes from a tool result in this conversation — never from memory or inference. If the records don't say, write "The records I searched don't show …" and name what you searched.
3. CITE. Put `[source: <filename>]` after each fact, using the exact `filename` from a tool result. Never cite a file the tools didn't return. Cite each source separately: `[source: a.pdf] [source: b.pdf]`.
4. BUDGET YOUR TOOLS. Stop searching after 6 tool calls and answer with what you have. Call independent searches together in one turn.
5. LEAD WITH THE ANSWER. First sentence answers the question. Then the supporting facts. No preamble, no "let me search", no restating the question. Use a table for 3+ comparable items. Keep it under ~250 words unless asked for more.
6. `share_plan` is optional: use it only for questions that need 3+ distinct steps.

## WHICH GOVERNMENT

{which_town}

## Specialised tools

- Budgets, taxes, debt, fund balance: `get_financial_summary` for headline figures, `get_drill_results` for analysis, `get_line_items` for specific lines, `get_anomalies` for pre-computed concerns.
- Vendors and contracts: `search_awards` (each council/board spending action with how it was procured), `search_contracts`, `get_vendor_summary`.
- "Anything suspicious", no-bid work, red flags: `get_integrity_flags`, then read the evidence documents. Flags are leads for review, not findings. Present the innocent explanations alongside, and never call a person or vendor corrupt.
- Meeting recordings appear in `search_chunks` with doc_type `recording_*`; their text carries `[HH:MM:SS]` markers. Quote the timestamp when citing a moment.

## Local facts

{jurisdiction_notes}

## Charts

For a trend with 3+ data points you may add a chart:

```chart
{"type":"bar","data":{"labels":["2022","2023","2024"],"datasets":[{"label":"Revenue","data":[100,200,300],"backgroundColor":"{brand_color}"}]}}
```
Types: bar, line, pie.
"""


REPORT_MODE_SUFFIX = """

## REPORT MODE ACTIVE

Structure your response as a formal analytical report:

**# Title**
One-line headline that captures the answer.

**## Executive Summary**
3-5 bullets. The decision-relevant takeaways.

**## Findings**
Numbered findings, each with:
- A clear claim
- Supporting numbers/citations
- Year-over-year context where relevant

**## Data**
Tables and charts. Always include at least one chart for any trend.

**## Sources**
Bulleted list of every document cited, in `[source: filename.pdf]` format.

Tone: authoritative, measured, evidence-driven. Lead with findings, not process."""


DEEP_THINKING_SUFFIX = """

## DEEP THINKING ACTIVE

You have extended reasoning enabled. Use it to:
- Cross-reference figures across multiple years/documents
- Detect inconsistencies between budgets and actuals
- Reason about the implications of policy changes
- Build the strongest analytical chain you can before writing the response.

After thinking, the user only sees your final response — make it crisp."""


def _which_town() -> str:
    muni = get_municipality()
    if len(muni.entities) == 1:
        return f"All records belong to {muni.primary_entity.label}."
    return (
        "Results are tagged with `municipality` — the government that published the record:\n"
        f"{muni.entities_prompt()}\n"
        "When the user asks about one of these, pass the `municipality` filter to search_chunks, "
        "and never attribute one government's record to another (neighbouring towns and the school "
        "district are separate bodies with separate budgets)."
    )


def _jurisdiction_notes() -> str:
    muni = get_municipality()
    notes = list(muni.state_profile.ai_notes) if muni.state_profile else []
    fy = muni.fiscal_year
    if fy.start_month == 1:
        notes.append(f"{muni.short_name} runs on a calendar fiscal year.")
    else:
        notes.append(f"{muni.short_name}'s fiscal year starts in month {fy.start_month} "
                     f"(label fiscal years like 2025-26).")
    notes.append(f"School districts run on a fiscal year starting in month {fy.school_start_month}.")
    notes += muni.ai.notes
    return "\n".join(f"   - {n}" for n in notes)


def _working_instructions() -> str:
    # str.replace, not .format — the instructions contain literal JSON braces.
    return (WORKING_INSTRUCTIONS
            .replace("{which_town}", _which_town())
            .replace("{jurisdiction_notes}", _jurisdiction_notes())
            .replace("{brand_color}", get_municipality().branding.theme_color or "#2563eb"))

# ─── Streaming chat ─────────────────────────────────────────────────────────

def _split_system_parts(req, doc_context: str) -> tuple[str, str]:
    """Return (stable_text, variable_text).

    `stable` is identical across iterations of an agentic loop and across
    requests with the same mode flags — it caches well. `variable` carries
    per-request context (attached doc, presentation summary, focused slide)
    so it gets its own cache breakpoint that hits on follow-up turns about
    the same target.
    """
    stable_parts = [_background(), _working_instructions()]
    if req.report_mode:
        stable_parts.append(REPORT_MODE_SUFFIX)
    if req.deep_thinking:
        stable_parts.append(DEEP_THINKING_SUFFIX)

    variable_parts: list[str] = []
    if doc_context:
        variable_parts.append(doc_context)
    if req.presentation_id and req.presentation_summary:
        variable_parts.append(
            "## ACTIVE PRESENTATION (deck mode)\n\n"
            f"{req.presentation_summary}\n\n"
            "When proposing changes, use the `propose_section` tool. To "
            "REPLACE an existing section, include its `section_id` from "
            "the list above. To APPEND a new section, omit `section_id`."
        )
    if req.presentation_id and req.target_section_id:
        variable_parts.append(
            "## FOCUSED SLIDE\n\n"
            f"The operator is currently focused on section "
            f"`{req.target_section_id}`. Default to editing THIS section "
            f"(pass `section_id={req.target_section_id}` to propose_section) "
            f"unless the request clearly asks to add a new section or edit a "
            f"different one."
        )

    return "\n\n".join(stable_parts), "\n\n".join(variable_parts)


def _build_system_prompt(req, doc_context: str) -> str:
    """String form — used by Gemini fallback (no caching API)."""
    stable, variable = _split_system_parts(req, doc_context)
    return stable + ("\n\n" + variable if variable else "")


def _build_system_blocks(req, doc_context: str) -> list[dict]:
    """Anthropic system message as cacheable content blocks.

    Block 1 (stable) gets cache_control so the long BACKGROUND +
    WORKING_INSTRUCTIONS payload is reused across iterations of an agentic
    loop — Anthropic's 5-min ephemeral cache means iterations 2-8 read the
    cached prefix at ~10% of the standard input rate.

    Block 2 (variable, if present) gets its own breakpoint so follow-up
    turns about the same attached doc or presentation also hit cache.
    """
    stable, variable = _split_system_parts(req, doc_context)
    blocks: list[dict] = [{
        "type": "text",
        "text": stable,
        "cache_control": {"type": "ephemeral"},
    }]
    if variable:
        blocks.append({
            "type": "text",
            "text": variable,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


def _attached_doc_context(db: Session, document_id: Optional[str]) -> str:
    """When the user attaches a single document, inline its first 30K chars
    so Claude doesn't need to call `read_document` first."""
    if not document_id:
        return ""
    doc = get_document_excerpt(db, document_id, max_chars=30000)
    if not doc:
        return ""
    return (
        f"## ATTACHED DOCUMENT (inline)\n\n"
        f"**{doc['filename']}** (FY {doc.get('fiscal_year') or '?'}, "
        f"{doc.get('doc_type') or 'document'})\n\n"
        f"{doc.get('excerpt') or ''}\n"
    )


