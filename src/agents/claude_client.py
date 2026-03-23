"""
Project Syndicate — Claude API Client

Thin wrapper around the Anthropic API for agent thinking cycles.
Handles: send prompts, track tokens/cost, errors/retries, budget limits.
Supports prompt caching and multi-model routing (Phase 3.5).
"""

__version__ = "1.0.0"

import asyncio
import logging
import time
from dataclasses import dataclass

import anthropic

from src.common.config import config

logger = logging.getLogger(__name__)

# Model pricing per million tokens — update when Anthropic changes pricing
MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input_per_million": 3.00, "output_per_million": 15.00},
    "claude-sonnet-4-6": {"input_per_million": 3.00, "output_per_million": 15.00},
    "claude-sonnet-4-5-20250514": {"input_per_million": 3.00, "output_per_million": 15.00},
    "claude-haiku-4-5-20251001": {"input_per_million": 1.00, "output_per_million": 5.00},
}


def get_pricing(model: str) -> dict:
    """Get pricing for a model, with fuzzy matching and safe fallback.

    Args:
        model: Model ID string.

    Returns:
        Dict with input_per_million and output_per_million.
    """
    # Exact match
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Partial match
    for key, pricing in MODEL_PRICING.items():
        if key in model or model in key:
            return pricing
    # Safe fallback: assume Sonnet rates (never undercharge)
    return {"input_per_million": 3.00, "output_per_million": 15.00}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calculate the USD cost of an API call including cache pricing.

    Cache writes cost 1.25x input rate.
    Cache reads cost 0.1x input rate.

    Args:
        model: Model ID for pricing lookup.
        input_tokens: Standard (non-cached) input tokens.
        output_tokens: Output tokens.
        cache_creation_tokens: Tokens written to cache (1.25x rate).
        cache_read_tokens: Tokens read from cache (0.1x rate).

    Returns:
        Cost in USD.
    """
    pricing = get_pricing(model)
    input_rate = pricing["input_per_million"]
    output_rate = pricing["output_per_million"]

    standard_input_cost = (input_tokens / 1_000_000) * input_rate
    output_cost = (output_tokens / 1_000_000) * output_rate
    cache_write_cost = (cache_creation_tokens / 1_000_000) * input_rate * 1.25
    cache_read_cost = (cache_read_tokens / 1_000_000) * input_rate * 0.10

    return standard_input_cost + output_cost + cache_write_cost + cache_read_cost


@dataclass
class APIResponse:
    """Structured response from a Claude API call."""
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    model: str
    stop_reason: str | None = None
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class ClaudeClient:
    """Wrapper around the Anthropic API for agent thinking cycles."""

    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    MAX_RETRIES = 2
    MAX_OUTPUT_TOKENS = 1024  # agent responses should be concise

    def __init__(self, api_key: str | None = None, model: str | None = None):
        """
        Args:
            api_key: Anthropic API key. Uses ANTHROPIC_API_KEY env var if None.
            model: Model ID override. Defaults to Claude Sonnet.
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> APIResponse:
        """Send a thinking cycle to Claude and get structured response.

        Args:
            system_prompt: The system prompt (agent identity + instructions).
            user_prompt: The user prompt (assembled context).
            temperature: Sampling temperature (role-specific).
            max_tokens: Max output tokens (default: MAX_OUTPUT_TOKENS).
            model: Model override for this call. Defaults to self.model.

        Returns:
            APIResponse with content, token counts, cost, and latency.
        """
        max_tokens = max_tokens or self.MAX_OUTPUT_TOKENS
        effective_model = model or self.model
        start_time = time.time()

        # Build system param with optional cache_control
        if config.prompt_caching_enabled:
            system_param = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system_prompt

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.messages.create(
                    model=effective_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_param,
                    messages=[{"role": "user", "content": user_prompt}],
                )

                latency_ms = int((time.time() - start_time) * 1000)

                content = ""
                if response.content:
                    content = response.content[0].text

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0

                cost = calculate_cost(
                    effective_model, input_tokens, output_tokens,
                    cache_creation, cache_read,
                )

                logger.info(
                    "api_call_complete",
                    extra={
                        "model": effective_model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_creation_tokens": cache_creation,
                        "cache_read_tokens": cache_read,
                        "cost_usd": cost,
                        "latency_ms": latency_ms,
                        "attempt": attempt + 1,
                    },
                )

                return APIResponse(
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    model=effective_model,
                    stop_reason=response.stop_reason,
                    cache_creation_tokens=cache_creation,
                    cache_read_tokens=cache_read,
                )

            except anthropic.RateLimitError as e:
                last_error = e
                wait = min(2 ** attempt * 5, 30)  # 5, 10, 20, max 30s
                logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1})")
                await asyncio.sleep(wait)

            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code >= 500:
                    wait = min(2 ** attempt * 2, 10)
                    logger.warning(f"Server error {e.status_code}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    raise

            except anthropic.APIConnectionError as e:
                last_error = e
                wait = min(2 ** attempt * 2, 10)
                logger.warning(f"Connection error, retrying in {wait}s")
                await asyncio.sleep(wait)

        # All retries exhausted
        raise RuntimeError(
            f"Claude API call failed after {self.MAX_RETRIES + 1} attempts: {last_error}"
        )

    async def call_repair(
        self,
        system_prompt: str,
        original_user_prompt: str,
        repair_prompt: str,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> APIResponse:
        """Send a repair prompt after validation failure.

        Uses lower temperature for repair to encourage well-formed output.
        Sends the conversation as a multi-turn exchange.

        Args:
            system_prompt: Original system prompt.
            original_user_prompt: The original user prompt.
            repair_prompt: The repair instructions.
            temperature: Lower temperature for repair attempts.
            model: Model override for this call. Defaults to self.model.

        Returns:
            APIResponse with the repaired output.
        """
        effective_model = model or self.model
        start_time = time.time()

        # Build system param with optional cache_control
        if config.prompt_caching_enabled:
            system_param = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system_prompt

        response = self.client.messages.create(
            model=effective_model,
            max_tokens=self.MAX_OUTPUT_TOKENS,
            temperature=temperature,
            system=system_param,
            messages=[
                {"role": "user", "content": original_user_prompt},
                {"role": "assistant", "content": "I'll provide my analysis in JSON format."},
                {"role": "user", "content": repair_prompt},
            ],
        )

        latency_ms = int((time.time() - start_time) * 1000)

        content = ""
        if response.content:
            content = response.content[0].text

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0

        cost = calculate_cost(
            effective_model, input_tokens, output_tokens,
            cache_creation, cache_read,
        )

        logger.info(
            "repair_call_complete",
            extra={
                "model": effective_model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_tokens": cache_creation,
                "cache_read_tokens": cache_read,
                "cost_usd": cost,
                "latency_ms": latency_ms,
            },
        )

        return APIResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            model=effective_model,
            stop_reason=response.stop_reason,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
        )
