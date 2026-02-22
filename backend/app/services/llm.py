"""
OpenAI LLM wrapper – structured JSON responses and token streaming.

Provides two async primitives that the workflow nodes consume:
    • call_llm_json(system, user, …) → dict   – JSON mode, parsed response.
    • stream_llm_tokens(system, user, …) → AsyncGenerator[str] – token-at-a-time.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger
from app.services.observability import get_tracer

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
    parent_span: Any = None,
) -> dict[str, Any]:
    """
    Call the LLM with JSON response format.  Parses and returns the dict.

    Raises ValueError if the response is not valid JSON.
    Returns a tuple-like dict with keys ``result`` and ``usage``.

    If *parent_span* is provided, a Langfuse generation is logged under it.
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

    # ── Langfuse generation span ──────────────────────────
    if parent_span is not None:
        tracer = get_tracer()
        tracer.log_generation(
            parent_span,
            name="llm.call_json",
            model=model,
            input={"system_chars": len(system), "user_chars": len(user)},
            output={"result_keys": list(parsed.keys())[:10]},
            usage={"input": tokens_in, "output": tokens_out},
            metadata={"temperature": temperature, "max_tokens": max_tokens, "llm_ms": elapsed_ms},
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
    parent_span: Any = None,
) -> dict[str, Any]:
    """
    Call the LLM and return the raw text response (no JSON parsing).

    Returns dict with ``text``, ``tokens_in``, ``tokens_out``, ``llm_ms``.

    If *parent_span* is provided, a Langfuse generation is logged under it.
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

    # ── Langfuse generation span ──────────────────────────
    if parent_span is not None:
        tracer = get_tracer()
        tracer.log_generation(
            parent_span,
            name="llm.call_text",
            model=model,
            input={"system_chars": len(system), "user_chars": len(user)},
            output={"text_chars": len(text)},
            usage={"input": tokens_in, "output": tokens_out},
            metadata={"temperature": temperature, "max_tokens": max_tokens, "llm_ms": elapsed_ms},
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
    parent_span: Any = None,
) -> AsyncGenerator[str, None]:
    """
    Stream tokens from the LLM.

    Yields individual text delta strings (may be partial words).
    If *parent_span* is provided, a Langfuse generation is logged after stream ends.
    """
    client = _get_client()
    model = model or settings.OPENAI_MODEL

    t0 = time.perf_counter()
    total_chars = 0

    stream = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            total_chars += len(delta.content)
            yield delta.content

    # ── Langfuse generation span (after stream completes) ─
    if parent_span is not None:
        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        tracer = get_tracer()
        tracer.log_generation(
            parent_span,
            name="llm.stream_tokens",
            model=model,
            input={"system_chars": len(system), "user_chars": len(user)},
            output={"streamed_chars": total_chars},
            metadata={"temperature": temperature, "max_tokens": max_tokens, "llm_ms": elapsed_ms},
        )
