"""
Provider-neutral tool-use loop for the chat assistant.

    async for sse in run(ctx): yield sse

Each iteration: one streaming model turn (providers.py) → run the requested
tools concurrently (each in its own short-lived DB session) → feed results
back. Stops when the model answers without tools, after MAX_TOOL_TURNS, or
after a hard tool-call cap. Then grounds citations, saves the answer and
records usage against whoever paid (route.payer).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import grounding
from .providers import ProviderError, adapter_for

log = logging.getLogger(__name__)

MAX_TOOL_TURNS = 6
MAX_TOOL_CALLS = 10
MAX_CONTINUATIONS = 2
WRAP_UP = ("You have used your tool budget. Write the final answer now from the results above, "
           "citing them; say plainly what you could not find.")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps({'type': event, **data}, default=str)}\n\n"


@dataclass
class ChatContext:
    route: object                     # services.ai.Route
    system: list[str]                 # [stable, variable]
    history: list[dict]               # neutral messages (user/assistant text only)
    query: str
    tools: list[dict]                 # Anthropic-style schemas {name, description, input_schema}
    execute: Callable[[str, dict], dict]
    describe: Callable[[str, dict], str]
    summarize: Callable[[str, dict], str]
    fit: Callable[[dict], str]
    session_id: str
    max_tokens: int = 4000
    on_done: Optional[Callable[[str, dict], None]] = None   # (final_text, stats)
    is_disconnected: Optional[Callable[[], "asyncio.Future"]] = None
    sources: grounding.Sources = field(default_factory=grounding.Sources)


async def run(ctx: ChatContext):
    adapter = adapter_for(ctx.route)
    messages = [*ctx.history[-20:], {"role": "user", "text": ctx.query}]
    answer, calls_made, turns, continuations = "", 0, 0, 0
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    force_answer = False

    try:
        while True:
            if ctx.is_disconnected and await ctx.is_disconnected():
                return
            yield sse("status", {"text": "Thinking…" if turns == 0 else "Reading results…", "stage": "model_call",
                                 "iteration": turns + 1, "model": ctx.route.model})
            result = None
            async for kind, payload in adapter.turn(ctx.system, messages, ctx.tools, ctx.max_tokens, force_answer):
                if kind == "text":
                    answer += payload
                    yield sse("delta", {"content": payload, "session_id": ctx.session_id})
                elif kind == "tool_start":
                    yield sse("tool_use", {"name": payload})
                elif kind == "final":
                    result = payload
            for k in usage:
                usage[k] += result.usage.get(k, 0)
            turns += 1

            if result.stop == "max_tokens" and not result.tool_calls and continuations < MAX_CONTINUATIONS:
                continuations += 1
                messages += [{"role": "assistant", "text": result.text},
                             {"role": "user", "text": "Continue exactly where you stopped; don't repeat anything."}]
                yield sse("continuation", {"count": continuations, "max": MAX_CONTINUATIONS})
                continue
            if not result.tool_calls or force_answer:
                break

            calls = result.tool_calls[: max(0, MAX_TOOL_CALLS - calls_made)] or result.tool_calls[:1]
            calls_made += len(calls)
            for c in calls:
                if c["name"] == "share_plan":
                    yield sse("plan", {"steps": c["args"].get("steps") or [], "rationale": c["args"].get("rationale") or ""})
                elif c["name"] == "propose_section":
                    yield sse("proposal", {"input": c["args"]})
                else:
                    yield sse("tool_call", {"name": c["name"], "input": c["args"],
                                            "description": ctx.describe(c["name"], c["args"])})
            outputs = await asyncio.gather(*(asyncio.to_thread(ctx.execute, c["name"], c["args"]) for c in calls))
            results = []
            for c, res in zip(calls, outputs):
                ctx.sources.add(c["name"], res)
                if c["name"] not in ("share_plan", "propose_section"):
                    yield sse("tool_result", {"name": c["name"], "summary": ctx.summarize(c["name"], res)})
                results.append({"id": c["id"], "name": c["name"], "content": ctx.fit(res)})
            # Tool calls the model asked for past the cap still need a result.
            for c in result.tool_calls[len(calls):]:
                results.append({"id": c["id"], "name": c["name"],
                                "content": json.dumps({"error": "tool budget exhausted; answer with what you have"})})
            messages += [{"role": "assistant", "text": result.text, "tool_calls": result.tool_calls, "raw": result.raw},
                         {"role": "tool", "results": results}]

            if turns >= MAX_TOOL_TURNS or calls_made >= MAX_TOOL_CALLS:
                # One last turn without tools so the model must answer.
                messages.append({"role": "user", "text": WRAP_UP})
                force_answer = True
    except ProviderError as exc:
        log.warning("provider error (%s %s): %s", ctx.route.provider, ctx.route.model, exc)
        msg = ("The AI provider is rate-limiting requests; try again in a moment." if exc.status == 429 else
               "The AI provider rejected the API key. Check it in Settings." if exc.status in (401, 403) else
               "The AI provider is having trouble; try again shortly." if (exc.status or 0) >= 500 else
               f"AI provider error: {exc}")
        yield sse("error", {"content": msg})
        return

    check = grounding.check(answer, ctx.sources)
    yield sse("citations", check)
    stored = grounding.mark_unverified(answer, check["unverified"])
    stats = {"tool_calls_made": calls_made, "turns": turns, **usage, "provider": ctx.route.provider,
             "model": ctx.route.model, "payer": ctx.route.payer, "unverified_citations": len(check["unverified"])}
    cost = 0.0
    if ctx.on_done:
        cost = await asyncio.to_thread(ctx.on_done, stored, stats) or 0.0
    yield sse("done", {"session_id": ctx.session_id, "tool_calls_made": calls_made, "model": ctx.route.model,
                       "provider": ctx.route.provider, "paid_by": ctx.route.payer,
                       "input_tokens": usage["input"], "output_tokens": usage["output"],
                       "estimated_cost_usd": round(cost, 4)})
