"""LLM client interface, the Anthropic implementation, and cost accounting.

The engine only talks to `LLMClient`. CI uses a fake that implements the same
protocol, so no API key is needed (D-027).
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import anthropic

from pipeline.config import ModelPrice, Settings


@dataclass
class LLMRequest:
    model: str
    system: str
    content: list[dict[str, Any]]  # Messages API user content blocks
    schema: dict[str, Any]
    effort: str = "medium"
    max_tokens: int = 16000
    purpose: str = "document"  # document | column_mapping
    meta: dict[str, Any] = field(default_factory=dict)  # never sent to the API (tests, logs)

    def cache_key(self, prompt_version: str) -> str:
        """Same model + prompt + schema + content -> same answer: reuse it (D-026)."""
        payload = json.dumps(
            {
                "v": prompt_version,
                "model": self.model,
                "effort": self.effort,
                "system": self.system,
                "schema": self.schema,
                "content": self.content,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class LLMResponse:
    status: str  # ok | refusal | max_tokens | invalid | error
    data: dict[str, Any] | None
    model_served: str
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    raw_text: str | None = None


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


def cost_usd(resp: LLMResponse, prices: dict[str, ModelPrice]) -> Decimal:
    price = prices.get(resp.model_served)
    if price is None:
        return Decimal(0)
    total = (
        resp.input_tokens * price.input
        + resp.output_tokens * price.output
        + resp.cache_read_tokens * price.cache_read
        + resp.cache_write_tokens * price.cache_write
    ) / 1_000_000
    return Decimal(str(round(total, 8)))


class AnthropicLLMClient:
    """Calls the Messages API with structured outputs (D-024).

    - The stable system prompt carries `cache_control` for prompt caching.
    - Escalation-model calls opt into server-side refusal fallbacks when enabled.
    - Transport errors, 429s and 5xx are retried by the SDK itself.
    """

    def __init__(self, settings: Settings) -> None:
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        self._client = anthropic.Anthropic(api_key=key, max_retries=4)
        self._fallback_models = (
            {settings.escalation_model} if settings.escalation_fallbacks else set()
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": request.content}],
            "output_config": {
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.schema},
            },
        }
        start = time.monotonic()
        try:
            if request.model in self._fallback_models:
                msg: Any = self._client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            else:
                msg = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            return LLMResponse(
                "error",
                None,
                request.model,
                error=f"{e.status_code}: {e.message}",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except anthropic.APIConnectionError as e:
            return LLMResponse(
                "error",
                None,
                request.model,
                error=f"connection: {e}",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        latency = int((time.monotonic() - start) * 1000)
        usage = msg.usage
        resp = LLMResponse(
            status="ok",
            data=None,
            model_served=msg.model,
            stop_reason=msg.stop_reason,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            latency_ms=latency,
        )
        if msg.stop_reason == "refusal":
            resp.status = "refusal"
            details = getattr(msg, "stop_details", None)
            resp.error = f"refusal: {getattr(details, 'category', None)}"
            return resp
        if msg.stop_reason == "max_tokens":
            resp.status = "max_tokens"
            return resp
        text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), None)
        resp.raw_text = text
        try:
            resp.data = json.loads(text) if text else None
        except json.JSONDecodeError as e:
            resp.status, resp.error = "invalid", f"invalid JSON: {e}"
        if resp.data is None and resp.status == "ok":
            resp.status, resp.error = "invalid", "no text block in response"
        return resp
