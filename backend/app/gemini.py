"""Gemini's REST API, for the two callers that ask it: the mail reader (email_ai.py) and
the assistant (assistant.py). Each tries the models of DO_AI_MODELS in order."""
from __future__ import annotations

from typing import Any

import httpx

from .config import Settings

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# A model that is retired, rate-limited or busy: the next model answers instead.
_TRY_NEXT = (404, 429, 500, 503)


class Busy(Exception):
    """This model can't answer now. The next one may."""


class GeminiError(Exception):
    """Gemini refused the request, or its answer could not be read."""


def models(settings: Settings) -> list[str]:
    return [m.strip() for m in settings.ai_models.split(",") if m.strip()]


async def generate(client: httpx.AsyncClient, settings: Settings, model: str, request: dict[str, Any]) -> dict[str, Any]:
    """The content of the model's first candidate. A timeout counts as busy: a busy model
    can hold a request for half a minute before it answers 503."""
    try:
        response = await client.post(
            API.format(model=model), headers={"x-goog-api-key": settings.google_api_key}, json=request
        )
    except httpx.TransportError as exc:
        raise Busy(f"{model} {'timed out' if isinstance(exc, httpx.TimeoutException) else 'could not be reached'}") from exc
    if response.status_code in _TRY_NEXT:
        raise Busy(f"{model} answered {response.status_code}")
    if response.status_code != 200:
        raise GeminiError(f"{model} answered {response.status_code}: {response.text[:300]}")
    try:
        return response.json()["candidates"][0]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiError(f"{model}'s answer could not be read: {exc}") from exc


def text_of(content: dict[str, Any]) -> str:
    """The answer's text, without the model's thoughts."""
    return "".join(p.get("text", "") for p in content.get("parts") or [] if not p.get("thought"))
