"""
Chat eval: does the assistant answer from, and cite, the right document?

Runs each question in jurisdictions/<deployment>/evals/search.yaml through the
real chat agent with the deployment's configured model and key, then checks
the grounded citations:
  cited      — an expected document is among the VERIFIED citations
  unverified — citations to documents the tools never returned (should be 0)
Needs an AI key (Admin → AI, or env). Costs a few cents per question.

    python -m scripts.eval_chat long-beach-ny --limit 5
    python -m scripts.eval_chat atlantic-highlands-nj --feature deep_chat
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time


async def ask(route, question: str) -> dict:
    from services.chat import agent, prompt, tools

    class Req:  # the bits of ChatRequest the prompt builder reads
        report_mode = deep_thinking = False
        presentation_id = presentation_summary = target_section_id = None

    stable, variable = prompt._split_system_parts(Req(), "")
    out: dict = {}

    def run_tool(name, args):
        from database import SessionLocal
        with SessionLocal() as s:
            return tools._execute_tool(name, args, s)

    def on_done(text, stats):
        out.update(text=text, stats=stats)
        return 0.0

    ctx = agent.ChatContext(route=route, system=[stable, variable], history=[], query=question,
                            tools=tools._tool_defs(), execute=run_tool, describe=tools._describe_tool_call,
                            summarize=tools._summarize_tool_result, fit=tools._fit_json, session_id="eval",
                            on_done=on_done)
    async for chunk in agent.run(ctx):
        if chunk.startswith("event: citations"):
            out["citations"] = json.loads(chunk.split("data: ", 1)[1])
        elif chunk.startswith("event: error"):
            out["error"] = json.loads(chunk.split("data: ", 1)[1]).get("content")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deployment")
    ap.add_argument("--feature", default="chat")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-cited", type=float, default=0.0, help="fail below this share of questions cited (0–1)")
    args = ap.parse_args()
    os.environ.setdefault("DEPLOYMENT", args.deployment)

    from scripts.eval_search import load_cases
    from services.ai import estimate_cost, resolve
    from services.ai.router import NoAIAccess
    try:
        route = resolve(args.feature)
    except NoAIAccess as exc:
        print(f"no AI access: {exc.reason}")
        return 2
    cases = load_cases(args.deployment)[: args.limit or None]
    print(f"{args.deployment} · {route.provider}:{route.model} · {len(cases)} questions\n")
    cited = unverified = 0
    usd = 0.0
    t0 = time.time()
    for case in cases:
        r = asyncio.run(ask(route, case["q"]))
        if r.get("error"):
            print(f"  ERR  {case['q']} — {r['error']}")
            continue
        want = [w.lower() for w in case["expect"]]
        ver = [v["filename"] for v in (r.get("citations") or {}).get("verified", [])]
        hit = any(w in fn.lower() for fn in ver for w in want)
        unv = len((r.get("citations") or {}).get("unverified", []))
        st = r.get("stats") or {}
        usd += estimate_cost(route.model, st.get("input", 0), st.get("output", 0))[0]
        cited += hit
        unverified += unv
        print(f"  {'CITED' if hit else 'miss ':5} {('!' + str(unv)) if unv else '  '}  {case['q']}  ({st.get('tool_calls_made', 0)} tools)")
    n = len(cases) or 1
    print(f"\ncited {cited}/{len(cases)} ({cited / n:.0%}) · unverified citations {unverified} · "
          f"~${usd:.3f} · {time.time() - t0:.0f}s")
    return 1 if cited / n < args.min_cited else 0


if __name__ == "__main__":
    sys.exit(main())
