"""
One streaming tool-use turn, for any provider.

Neutral conversation format (what the agent loop keeps):
    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}], "raw": <provider-native, optional>}
    {"role": "tool", "results": [{"id", "name", "content": "<json string>"}]}

`turn()` yields ("text", str) and ("tool_start", name) while streaming, then
exactly one ("final", TurnResult).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stop: str = "end"  # end | tool_use | max_tokens
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    raw: object = None


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ── Anthropic ───────────────────────────────────────────────────────────────

class AnthropicAdapter:
    def __init__(self, key: str, model: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=key)
        self.model = model

    def _messages(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                if m.get("raw") is not None:
                    out.append({"role": "assistant", "content": m["raw"]})
                    continue
                blocks = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                           for c in m.get("tool_calls") or []]
                out.append({"role": "assistant", "content": blocks or m.get("text") or ""})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        # Cache the conversation prefix: the last user message gets a breakpoint.
        for msg in reversed(out):
            if msg["role"] == "user":
                c = msg["content"]
                if isinstance(c, str):
                    msg["content"] = [{"type": "text", "text": c, "cache_control": {"type": "ephemeral"}}]
                elif c:
                    c[-1] = {**c[-1], "cache_control": {"type": "ephemeral"}}
                break
        return out

    async def turn(self, system: list[str], messages: list[dict], tools: list[dict], max_tokens: int,
                   force_answer: bool = False) -> AsyncIterator:
        sys_blocks = [{"type": "text", "text": t, "cache_control": {"type": "ephemeral"}} for t in system if t]
        tool_defs = [dict(t) for t in tools]
        if tool_defs:
            tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
        text = ""
        try:
            extra = {"tool_choice": {"type": "none"}} if force_answer and tool_defs else {}
            async with self.client.messages.stream(model=self.model, max_tokens=max_tokens, system=sys_blocks,
                                                   tools=tool_defs, messages=self._messages(messages),
                                                   **extra) as stream:
                async for ev in stream:
                    et = getattr(ev, "type", None)
                    if et == "content_block_start" and getattr(ev.content_block, "type", None) == "tool_use":
                        yield ("tool_start", ev.content_block.name)
                    elif et == "content_block_delta" and getattr(ev.delta, "type", None) == "text_delta":
                        text += ev.delta.text
                        yield ("text", ev.delta.text)
                final = await stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(getattr(exc, "message", str(exc)), getattr(exc, "status_code", None)) from exc
        u = final.usage
        calls = [{"id": b.id, "name": b.name, "args": dict(b.input or {})}
                 for b in final.content if getattr(b, "type", None) == "tool_use"]
        yield ("final", TurnResult(
            text=text, tool_calls=calls,
            stop={"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(final.stop_reason, "end"),
            usage={"input": u.input_tokens or 0, "output": u.output_tokens or 0,
                   "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                   "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0},
            raw=[b.model_dump(exclude_none=True) for b in final.content],
        ))


# ── Gemini ──────────────────────────────────────────────────────────────────

_SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum"}


def _gemini_schema(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k in _SCHEMA_KEYS}
    if "properties" in out:
        out["properties"] = {k: _gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _gemini_schema(out["items"])
    return out


class GeminiAdapter:
    def __init__(self, key: str, model: str):
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key)
        self.model = model

    def _contents(self, messages: list[dict]) -> list:
        t = self.types
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append(t.Content(role="user", parts=[t.Part(text=m["text"])]))
            elif m["role"] == "assistant":
                parts = [t.Part(text=m["text"])] if m.get("text") else []
                parts += [t.Part(function_call=t.FunctionCall(name=c["name"], args=c["args"]))
                          for c in m.get("tool_calls") or []]
                out.append(t.Content(role="model", parts=parts or [t.Part(text="")]))
            elif m["role"] == "tool":
                parts = []
                for r in m["results"]:
                    try:
                        resp = json.loads(r["content"])
                    except (TypeError, ValueError):
                        resp = {"text": r["content"]}
                    parts.append(t.Part(function_response=t.FunctionResponse(
                        name=r["name"], response=resp if isinstance(resp, dict) else {"result": resp})))
                out.append(t.Content(role="user", parts=parts))
        return out

    async def turn(self, system: list[str], messages: list[dict], tools: list[dict], max_tokens: int,
                   force_answer: bool = False) -> AsyncIterator:
        t = self.types
        decls = [t.FunctionDeclaration(name=x["name"], description=x.get("description", ""),
                                       parameters=_gemini_schema(x.get("input_schema") or {"type": "object"}))
                 for x in tools]
        cfg = t.GenerateContentConfig(
            system_instruction="\n\n".join(s for s in system if s), max_output_tokens=max_tokens,
            tools=[t.Tool(function_declarations=decls)] if decls else None,
            tool_config=t.ToolConfig(function_calling_config=t.FunctionCallingConfig(mode="NONE"))
            if force_answer and decls else None,
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True))
        text, calls, usage, finish = "", [], {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, None
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=self.model, contents=self._contents(messages), config=cfg)
            async for chunk in stream:
                for cand in chunk.candidates or []:
                    finish = cand.finish_reason or finish
                    for part in (cand.content.parts if cand.content else None) or []:
                        if part.text and not part.thought:
                            text += part.text
                            yield ("text", part.text)
                        if part.function_call:
                            calls.append({"id": f"call_{len(calls)}", "name": part.function_call.name,
                                          "args": dict(part.function_call.args or {})})
                            yield ("tool_start", part.function_call.name)
                um = chunk.usage_metadata
                if um:
                    usage["input"] = um.prompt_token_count or usage["input"]
                    usage["output"] = um.candidates_token_count or usage["output"]
                    usage["cache_read"] = getattr(um, "cached_content_token_count", 0) or 0
        except Exception as exc:
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            raise ProviderError(str(exc)[:300], status if isinstance(status, int) else None) from exc
        stop = "tool_use" if calls else ("max_tokens" if str(finish).endswith("MAX_TOKENS") else "end")
        yield ("final", TurnResult(text=text, tool_calls=calls, stop=stop, usage=usage))


# ── OpenAI ──────────────────────────────────────────────────────────────────

class OpenAIAdapter:
    def __init__(self, key: str, model: str):
        import openai
        self._openai = openai
        self.client = openai.AsyncOpenAI(api_key=key)
        self.model = model

    def _messages(self, system: list[str], messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": "\n\n".join(s for s in system if s)}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                         for c in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        return out

    async def turn(self, system: list[str], messages: list[dict], tools: list[dict], max_tokens: int,
                   force_answer: bool = False) -> AsyncIterator:
        tool_defs = [{"type": "function", "function": {"name": x["name"], "description": x.get("description", ""),
                                                       "parameters": x.get("input_schema") or {"type": "object"}}}
                     for x in tools]
        text, acc, usage, finish = "", {}, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, None
        try:
            stream = await self.client.chat.completions.create(
                model=self.model, messages=self._messages(system, messages), tools=tool_defs or None,
                **({"tool_choice": "none"} if force_answer and tool_defs else {}),
                max_completion_tokens=max_tokens, stream=True, stream_options={"include_usage": True})
            async for chunk in stream:
                if chunk.usage:
                    usage["input"] = chunk.usage.prompt_tokens or 0
                    usage["output"] = chunk.usage.completion_tokens or 0
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    usage["cache_read"] = getattr(details, "cached_tokens", 0) or 0
                for ch in chunk.choices or []:
                    finish = ch.finish_reason or finish
                    d = ch.delta
                    if d and d.content:
                        text += d.content
                        yield ("text", d.content)
                    for tc in (d.tool_calls if d else None) or []:
                        slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                            yield ("tool_start", tc.function.name)
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
        except self._openai.APIStatusError as exc:
            raise ProviderError(str(exc)[:300], exc.status_code) from exc
        calls = []
        for i in sorted(acc):
            try:
                args = json.loads(acc[i]["args"] or "{}")
            except ValueError:
                args = {}
            calls.append({"id": acc[i]["id"] or f"call_{i}", "name": acc[i]["name"], "args": args})
        stop = "tool_use" if calls else ("max_tokens" if finish == "length" else "end")
        yield ("final", TurnResult(text=text, tool_calls=calls, stop=stop, usage=usage))


ADAPTERS = {"anthropic": AnthropicAdapter, "gemini": GeminiAdapter, "openai": OpenAIAdapter}


def adapter_for(route) -> object:
    return ADAPTERS[route.provider](route.key, route.model)
