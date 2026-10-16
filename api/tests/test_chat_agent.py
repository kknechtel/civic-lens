"""Chat agent loop with a scripted provider: tools, grounding, budgets, provider message shapes."""
import asyncio
import json

import pytest

from services.ai.router import Route
from services.chat import agent, grounding
from services.chat.providers import AnthropicAdapter, GeminiAdapter, OpenAIAdapter, TurnResult

ROUTE = Route("chat", "chat", "anthropic", "claude-haiku-5-5", "sk-test", "deployment")


class Script:
    """Fake adapter: yields the scripted turns in order; records what it was sent."""

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    async def turn(self, system, messages, tools, max_tokens, force_answer=False):
        self.seen.append({"messages": [dict(m) for m in messages], "force_answer": force_answer})
        t = self.turns.pop(0) if self.turns else TurnResult(text="done")
        for piece in (t.text or "").split(" "):
            if piece:
                yield ("text", piece + " ")
        yield ("final", t)


def _events(chunks):
    out = []
    for c in chunks:
        if c.startswith("event:"):
            name = c.split("\n", 1)[0][7:]
            out.append((name, json.loads(c.split("data: ", 1)[1])))
    return out


def _run(monkeypatch, script, execute=None, **kw):
    monkeypatch.setattr(agent, "adapter_for", lambda route: script)
    saved = {}

    def on_done(text, stats):
        saved.update(text=text, stats=stats)
        return 0.0012

    ctx = agent.ChatContext(
        route=ROUTE, system=["sys", ""], history=[], query="who is the borough engineer?",
        tools=[{"name": "search_chunks", "description": "d", "input_schema": {"type": "object"}}],
        execute=execute or (lambda name, args: {"count": 1, "chunks": [
            {"document_id": "d1", "filename": "2025-087 Appointing Engineer.pdf", "title": "Appointing Engineer"}]}),
        describe=lambda n, a: f"Searching {a.get('query')}", summarize=lambda n, r: "1 result",
        fit=lambda r: json.dumps(r), session_id="s1", on_done=on_done, **kw)

    async def collect():
        return [c async for c in agent.run(ctx)]
    return _events(asyncio.run(collect())), saved


def test_tool_round_trip_and_grounded_citations(monkeypatch):
    script = Script([
        TurnResult(tool_calls=[{"id": "t1", "name": "search_chunks", "args": {"query": "borough engineer"}}], stop="tool_use"),
        TurnResult(text="Doug Rohmeyer of Colliers [source: 2025-087 Appointing Engineer.pdf] since 2019 [source: made-up.pdf]"),
    ])
    events, saved = _run(monkeypatch, script)
    names = [e[0] for e in events]
    assert names.index("tool_call") < names.index("tool_result") < names.index("citations") < names.index("done")
    cites = dict(events)["citations"]
    assert [v["document_id"] for v in cites["verified"]] == ["d1"]
    assert cites["unverified"] == ["made-up.pdf"]
    assert "[unverified source: made-up.pdf]" in saved["text"] and "[source: 2025-087" in saved["text"]
    assert dict(events)["done"]["paid_by"] == "deployment" and dict(events)["done"]["estimated_cost_usd"] == 0.0012
    # Second turn saw the tool result.
    assert script.seen[1]["messages"][-1]["role"] == "tool"


def test_tool_budget_forces_an_answer(monkeypatch):
    calls = [TurnResult(tool_calls=[{"id": f"t{i}", "name": "search_chunks", "args": {"query": str(i)}}], stop="tool_use")
             for i in range(20)]
    script = Script(calls)
    events, saved = _run(monkeypatch, script)
    assert len(script.seen) == agent.MAX_TOOL_TURNS + 1
    assert script.seen[-1]["force_answer"] and script.seen[-1]["messages"][-1]["text"] == agent.WRAP_UP
    assert saved["stats"]["tool_calls_made"] == agent.MAX_TOOL_TURNS


def test_overflow_tool_calls_still_get_results(monkeypatch):
    many = [{"id": f"t{i}", "name": "search_chunks", "args": {}} for i in range(agent.MAX_TOOL_CALLS + 3)]
    script = Script([TurnResult(tool_calls=many, stop="tool_use"), TurnResult(text="ok")])
    _run(monkeypatch, script)
    tool_msg = next(m for m in reversed(script.seen[1]["messages"]) if m["role"] == "tool")
    results = tool_msg["results"]
    assert len(results) == len(many)  # providers reject a tool call with no result
    assert "budget" in results[-1]["content"]


def test_grounding_matches_titles_and_missing_extensions():
    s = grounding.Sources()
    s.add("search_awards", {"awards": [{"filename": "Resolutions — 2025-068 Upper Prospect Wall.pdf", "document_id": "x"}]})
    out = grounding.check("[source: Resolutions — 2025-068 Upper Prospect Wall] [source: nope.pdf]", s)
    assert len(out["verified"]) == 1 and out["unverified"] == ["nope.pdf"]


NEUTRAL = [
    {"role": "user", "text": "q"},
    {"role": "assistant", "text": "looking", "tool_calls": [{"id": "c1", "name": "search_chunks", "args": {"query": "x"}}]},
    {"role": "tool", "results": [{"id": "c1", "name": "search_chunks", "content": json.dumps({"count": 0})}]},
]


def test_anthropic_message_shape():
    msgs = AnthropicAdapter("sk-test", "claude-haiku-5-5")._messages(NEUTRAL)
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "search_chunks", "input": {"query": "x"}}
    assert msgs[2]["content"][0]["type"] == "tool_result" and msgs[2]["content"][0]["tool_use_id"] == "c1"
    assert msgs[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}  # cache breakpoint on the last user turn


def test_gemini_content_shape():
    contents = GeminiAdapter("AIza-test", "gemini-2.5-flash")._contents(NEUTRAL)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[1].function_call.name == "search_chunks"
    assert contents[2].parts[0].function_response.response == {"count": 0}


def test_openai_message_shape():
    msgs = OpenAIAdapter("sk-test", "gpt-5-mini")._messages(["sys"], NEUTRAL)
    assert msgs[0]["role"] == "system"
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"query": "x"})
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"count": 0})}


def test_history_alternates_and_starts_with_user():
    from types import SimpleNamespace as R
    from routes.chat import _neutral_history
    rows = [R(role="assistant", content="hi"), R(role="user", content="a"), R(role="user", content="b"),
            R(role="assistant", content="c")]
    assert _neutral_history(rows) == [{"role": "user", "text": "a\n\nb"}, {"role": "assistant", "text": "c"}]
