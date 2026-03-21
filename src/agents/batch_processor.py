"""
Project Syndicate — Batch Processor

Handles non-urgent API calls via Anthropic's Batch API for 50% cost savings.

Suitable for:
- Genesis evaluation summaries
- Agent reflection cycles (every 10th cycle)
- Post-mortem generation
- Library contribution reviews

NOT suitable for:
- Real-time thinking cycles
- Trade execution decisions
- Anything the agent is waiting on before acting
"""

__version__ = "0.1.0"

import asyncio
import logging
import time
from dataclasses import dataclass, field

import anthropic

from src.common.config import config

logger = logging.getLogger(__name__)


class BatchTimeoutError(Exception):
    """Raised when a batch does not complete within the timeout."""
    pass


@dataclass
class BatchRequest:
    """A single request to include in a batch."""
    custom_id: str
    model: str
    system: str
    messages: list[dict]
    temperature: float = 0.5
    max_tokens: int = 1024


@dataclass
class BatchResult:
    """Result of a single request within a completed batch."""
    custom_id: str
    content: str
    usage: dict = field(default_factory=dict)
    success: bool = True
    error: str | None = None


class BatchProcessor:
    """Processes non-urgent API work via Anthropic's Batch API.

    Batch API provides 50% cost savings but results are not immediate.
    Typical completion time is minutes to hours.
    """

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: Anthropic API key. Uses ANTHROPIC_API_KEY env var if None.
        """
        self.client = anthropic.Anthropic(api_key=api_key or config.anthropic_api_key or None)

    async def submit_batch(self, requests: list[BatchRequest]) -> str:
        """Submit a batch of API requests.

        Args:
            requests: List of BatchRequest objects.

        Returns:
            Batch ID for tracking.
        """
        batch_requests = []
        for req in requests:
            batch_requests.append({
                "custom_id": req.custom_id,
                "params": {
                    "model": req.model,
                    "max_tokens": req.max_tokens,
                    "temperature": req.temperature,
                    "system": req.system,
                    "messages": req.messages,
                },
            })

        batch = self.client.messages.batches.create(requests=batch_requests)

        logger.info(
            "batch_submitted",
            extra={
                "batch_id": batch.id,
                "request_count": len(requests),
            },
        )
        return batch.id

    async def check_batch_status(self, batch_id: str) -> dict:
        """Check if a batch has completed.

        Args:
            batch_id: The batch ID from submit_batch.

        Returns:
            Dict with 'status' key and batch metadata.
        """
        batch = self.client.messages.batches.retrieve(batch_id)
        return {
            "status": batch.processing_status,
            "is_complete": batch.processing_status == "ended",
            "request_counts": {
                "processing": batch.request_counts.processing,
                "succeeded": batch.request_counts.succeeded,
                "errored": batch.request_counts.errored,
                "canceled": batch.request_counts.canceled,
                "expired": batch.request_counts.expired,
            },
        }

    async def retrieve_batch_results(self, batch_id: str) -> list[BatchResult]:
        """Retrieve completed batch results.

        Args:
            batch_id: The batch ID from submit_batch.

        Returns:
            List of BatchResult objects matched by custom_id.
        """
        results = []
        for entry in self.client.messages.batches.results(batch_id):
            if entry.result.type == "succeeded":
                content = ""
                if entry.result.message.content:
                    content = entry.result.message.content[0].text
                usage = {
                    "input_tokens": entry.result.message.usage.input_tokens,
                    "output_tokens": entry.result.message.usage.output_tokens,
                }
                results.append(BatchResult(
                    custom_id=entry.custom_id,
                    content=content,
                    usage=usage,
                    success=True,
                ))
            else:
                error_msg = str(entry.result.error) if hasattr(entry.result, "error") else "unknown_error"
                results.append(BatchResult(
                    custom_id=entry.custom_id,
                    content="",
                    success=False,
                    error=error_msg,
                ))

        logger.info(
            "batch_results_retrieved",
            extra={
                "batch_id": batch_id,
                "total": len(results),
                "succeeded": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            },
        )
        return results

    async def submit_and_wait(
        self,
        requests: list[BatchRequest],
        timeout_seconds: int | None = None,
        poll_interval: int | None = None,
    ) -> list[BatchResult]:
        """Submit a batch and poll until complete or timeout.

        Args:
            requests: List of BatchRequest objects.
            timeout_seconds: Max wait time. Defaults to config value.
            poll_interval: Seconds between status checks. Defaults to config value.

        Returns:
            List of BatchResult objects.

        Raises:
            BatchTimeoutError: If batch does not complete within timeout.
        """
        timeout = timeout_seconds or config.batch_timeout_seconds
        interval = poll_interval or config.batch_poll_interval_seconds

        batch_id = await self.submit_batch(requests)

        start = time.time()
        while time.time() - start < timeout:
            status = await self.check_batch_status(batch_id)
            if status["is_complete"]:
                return await self.retrieve_batch_results(batch_id)
            await asyncio.sleep(interval)

        raise BatchTimeoutError(
            f"Batch {batch_id} did not complete within {timeout}s"
        )
