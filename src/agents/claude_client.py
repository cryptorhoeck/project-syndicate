"""
Project Syndicate — Claude API Client

Thin wrapper around the Anthropic API for agent thinking cycles.
Handles: send prompts, track tokens/cost, errors/retries, budget limits.
"""

__version__ = "0.9.0"

import logging
import time
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

# Pricing per million tokens (Claude Sonnet 4)
# These should be updated if model pricing changes
INPUT_COST_PER_MILLION = 3.00   # $3.00 per 1M input tokens
OUTPUT_COST_PER_MILLION = 15.00  # $15.00 per 1M output tokens


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


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Calculate the USD cost of an API call.

    Args:
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Cost in USD.
    """
    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_MILLION
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_MILLION
    return input_cost + output_cost


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
    ) -> APIResponse:
        """Send a thinking cycle to Claude and get structured response.

        Args:
            system_prompt: The system prompt (agent identity + instructions).
            user_prompt: The user prompt (assembled context).
            temperature: Sampling temperature (role-specific).
            max_tokens: Max output tokens (default: MAX_OUTPUT_TOKENS).

        Returns:
            APIResponse with content, token counts, cost, and latency.
        """
        max_tokens = max_tokens or self.MAX_OUTPUT_TOKENS
        start_time = time.time()

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )

                latency_ms = int((time.time() - start_time) * 1000)

                content = ""
                if response.content:
                    content = response.content[0].text

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cost = calculate_cost(input_tokens, output_tokens)

                logger.info(
                    "api_call_complete",
                    extra={
                        "model": self.model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
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
                    model=self.model,
                    stop_reason=response.stop_reason,
                )

            except anthropic.RateLimitError as e:
                last_error = e
                wait = min(2 ** attempt * 5, 30)  # 5, 10, 20, max 30s
                logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1})")
                time.sleep(wait)

            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code >= 500:
                    wait = min(2 ** attempt * 2, 10)
                    logger.warning(f"Server error {e.status_code}, retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise

            except anthropic.APIConnectionError as e:
                last_error = e
                wait = min(2 ** attempt * 2, 10)
                logger.warning(f"Connection error, retrying in {wait}s")
                time.sleep(wait)

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
    ) -> APIResponse:
        """Send a repair prompt after validation failure.

        Uses lower temperature for repair to encourage well-formed output.
        Sends the conversation as a multi-turn exchange.

        Args:
            system_prompt: Original system prompt.
            original_user_prompt: The original user prompt.
            repair_prompt: The repair instructions.
            temperature: Lower temperature for repair attempts.

        Returns:
            APIResponse with the repaired output.
        """
        start_time = time.time()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.MAX_OUTPUT_TOKENS,
            temperature=temperature,
            system=system_prompt,
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
        cost = calculate_cost(input_tokens, output_tokens)

        logger.info(
            "repair_call_complete",
            extra={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
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
            model=self.model,
            stop_reason=response.stop_reason,
        )
