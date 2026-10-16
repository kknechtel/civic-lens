"""
What the app can call and what each feature uses.

Callers never name a model. They ask for a ROLE (`fast`, `chat`, `deep`,
`extract`, `vision`); the deployment's AI settings map each role to a provider
and model, and services.ai.router decides whose key pays (see keys.py).

FEATURES are the user-visible things that spend money. An admin chooses which
ones the deployment sponsors; everything else needs the user's own key.
"""
from __future__ import annotations

PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "env": "ANTHROPIC_API_KEY", "kinds": ["llm"],
                  "key_hint": "sk-ant-…", "docs": "https://console.anthropic.com/settings/keys"},
    "gemini": {"label": "Google Gemini", "env": "GEMINI_API_KEY", "kinds": ["llm", "embeddings"],
               "key_hint": "AIza…", "docs": "https://aistudio.google.com/apikey"},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY", "kinds": ["llm", "embeddings"],
               "key_hint": "sk-…", "docs": "https://platform.openai.com/api-keys"},
    "voyage": {"label": "Voyage AI (embeddings, rerank)", "env": "VOYAGE_API_KEY", "kinds": ["embeddings"],
               "key_hint": "pa-…", "docs": "https://dash.voyageai.com/api-keys"},
    "youtube": {"label": "YouTube Data API", "env": "YOUTUBE_API_KEY", "kinds": ["data"],
                "key_hint": "AIza…", "docs": "https://console.cloud.google.com/apis/credentials"},
}

# Selectable models per provider (admins may also type any model ID).
MODELS = {
    "anthropic": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
    "gemini": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
    "openai": ["gpt-5-mini", "gpt-5"],
}

ROLES = {
    "fast": "High-volume background work: titles, summaries, query expansion, digests",
    "chat": "The chat assistant",
    "deep": "Deep-thinking chat, reports and presentations",
    "extract": "Structured extraction from financial statements",
    "vision": "Reading images (event flyers, scanned tables)",
}

# Defaults favour cost: nothing defaults to an Opus-class model.
DEFAULT_ROUTES = {
    "fast": [("gemini", "gemini-2.5-flash-lite"), ("anthropic", "claude-haiku-5-5"), ("openai", "gpt-5-mini")],
    "chat": [("anthropic", "claude-haiku-5-5"), ("gemini", "gemini-2.5-flash"), ("openai", "gpt-5-mini")],
    "deep": [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-2.5-pro"), ("openai", "gpt-5")],
    "extract": [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-2.5-flash"), ("openai", "gpt-5-mini")],
    "vision": [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-2.5-flash"), ("openai", "gpt-5-mini")],
}

# feature → (role, triggered by a user?). Background features have no user,
# so only the deployment can pay for them.
FEATURES = {
    "chat": ("chat", True),
    "deep_chat": ("deep", True),
    "reports": ("deep", True),
    "presentations": ("deep", True),
    "document_chat": ("chat", True),
    "integrity_review": ("deep", True),
    "enrichment": ("fast", False),
    "query_expansion": ("fast", False),
    "digests": ("fast", False),
    "financial_extraction": ("extract", False),
    "meeting_summaries": ("fast", False),
    "event_images": ("vision", False),
}

# What a fresh deployment pays for. Background pipeline yes (it builds the
# shared corpus); interactive AI is off until an admin turns it on, so a public
# deployment can't run up a bill by default.
DEFAULT_SPONSORED = {"enrichment", "query_expansion", "digests", "financial_extraction", "meeting_summaries",
                     "event_images"}

DEFAULT_CAPS = {"deployment_monthly_usd": 50.0, "user_monthly_usd": 2.0}

# USD per million tokens (input, output). Editable in admin → AI → rates;
# values here are starting points, not quotes. Unknown models are estimated
# from the nearest family below and marked as estimates.
DEFAULT_RATES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
    "voyage-3": (0.06, 0.0),
}
FAMILY_FALLBACK = [("opus", "claude-opus-4-7"), ("sonnet", "claude-sonnet-4-6"), ("haiku", "claude-haiku-4-5"),
                   ("flash-lite", "gemini-2.5-flash-lite"), ("flash", "gemini-2.5-flash"), ("pro", "gemini-2.5-pro"),
                   ("mini", "gemini-2.5-flash"), ("gpt", "claude-sonnet-4-6")]
