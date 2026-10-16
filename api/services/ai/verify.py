"""Check an API key with a cheap, read-only call to its provider."""
from __future__ import annotations

import requests

_TIMEOUT = 15


def verify(provider: str, key: str) -> tuple[bool, str]:
    try:
        if provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models", timeout=_TIMEOUT,
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        elif provider == "gemini":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=_TIMEOUT,
                             params={"key": key, "pageSize": 1})
        elif provider == "openai":
            r = requests.get("https://api.openai.com/v1/models", timeout=_TIMEOUT,
                             headers={"Authorization": f"Bearer {key}"})
        elif provider == "voyage":
            r = requests.post("https://api.voyageai.com/v1/embeddings", timeout=_TIMEOUT,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"input": ["ok"], "model": "voyage-3"})
        elif provider == "youtube":
            r = requests.get("https://www.googleapis.com/youtube/v3/videoCategories", timeout=_TIMEOUT,
                             params={"part": "snippet", "regionCode": "US", "key": key})
        else:
            return False, f"unknown provider {provider}"
    except requests.RequestException as exc:
        return False, f"could not reach {provider}: {exc.__class__.__name__}"
    if r.status_code == 200:
        return True, "ok"
    if r.status_code in (401, 403):
        return False, "rejected by the provider (wrong or revoked key)"
    if r.status_code == 429:
        return True, "valid, but currently rate-limited"
    return False, f"provider returned HTTP {r.status_code}"
