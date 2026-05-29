"""
OpenAI LLM wrapper – structured JSON responses and token streaming.

Uses the Langfuse drop-in OpenAI client (``langfuse.openai``) which
automatically traces every LLM call as a ``generation`` observation
nested under the currently active Langfuse span — capturing model name,
token usage, latency, and API errors with zero manual instrumentation.

When Langfuse tracing is disabled the drop-in behaves identically to
the regular ``openai`` client.

Provides three async primitives that the workflow nodes consume:
    • call_llm_json(system, user, …) → dict   – JSON mode, parsed response.
    • call_llm_text(system, user, …) → dict   – plain-text response.
    • stream_llm_tokens(system, user, …)       – token-at-a-time generator.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

# Drop-in replacement: automatically traces all calls as Langfuse
# generation observations.  When LANGFUSE_TRACING_ENABLED=false it
# behaves identically to the standard openai.AsyncOpenAI client.
from langfuse.openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


# ── Structured JSON call ────────────────────────────────────────


async def call_llm_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """
    Call the LLM with JSON response format.  Parses and returns the dict.

    Raises ValueError if the response is not valid JSON.
    Returns a dict with keys ``result``, ``tokens_in``, ``tokens_out``, ``llm_ms``.

    Langfuse tracing (model, tokens, latency) is automatic via the drop-in
    client — no manual spans needed.
    """
    client = _get_client()
    model = model or settings.OPENAI_MODEL

    t0 = time.perf_counter()

    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    raw = response.choices[0].message.content or "{}"

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON: %s", raw[:200])
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    usage = response.usage
    tokens_in = usage.prompt_tokens if usage else 0
    tokens_out = usage.completion_tokens if usage else 0

    logger.info(
        "LLM JSON call: model=%s in=%d out=%d ms=%d",
        model,
        tokens_in,
        tokens_out,
        elapsed_ms,
    )

    return {
        "result": parsed,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_ms": elapsed_ms,
    }


# ── Plain-text call (no JSON mode) ──────────────────────────────


async def call_llm_text(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """
    Call the LLM and return the raw text response (no JSON parsing).

    Returns dict with ``text``, ``tokens_in``, ``tokens_out``, ``llm_ms``.

    Langfuse tracing is automatic via the drop-in client.
    """
    client = _get_client()
    model = model or settings.OPENAI_MODEL

    t0 = time.perf_counter()

    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    text = response.choices[0].message.content or ""
    usage = response.usage
    tokens_in = usage.prompt_tokens if usage else 0
    tokens_out = usage.completion_tokens if usage else 0

    logger.info(
        "LLM text call: model=%s in=%d out=%d ms=%d",
        model, tokens_in, tokens_out, elapsed_ms,
    )

    return {
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_ms": elapsed_ms,
    }


# ── Token streaming ─────────────────────────────────────────────


async def stream_llm_tokens(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> AsyncGenerator[str, None]:
    """
    Stream tokens from the LLM.

    Yields individual text delta strings (may be partial words).
    Token usage is captured via ``stream_options={"include_usage": True}``
    so Langfuse can compute cost accurately even on streaming responses.
    """
    client = _get_client()
    model = model or settings.OPENAI_MODEL

    stream = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    async for chunk in stream:
        # OpenAI sends a final empty-choices chunk carrying usage data
        # when include_usage=True.  Skip it for content emission.
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
