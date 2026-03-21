"""Tests for the Batch Processor — Phase 3.5."""

__version__ = "0.1.0"

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass

from src.agents.batch_processor import (
    BatchProcessor,
    BatchRequest,
    BatchResult,
    BatchTimeoutError,
)


def _make_request(custom_id: str = "test-1") -> BatchRequest:
    return BatchRequest(
        custom_id=custom_id,
        model="claude-haiku-4-5-20251001",
        system="You are a test assistant.",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=0.5,
        max_tokens=100,
    )


class TestBatchRequest:
    def test_default_values(self):
        req = BatchRequest(
            custom_id="test-1",
            model="claude-haiku-4-5-20251001",
            system="Test",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert req.temperature == 0.5
        assert req.max_tokens == 1024

    def test_custom_values(self):
        req = _make_request("custom-id")
        assert req.custom_id == "custom-id"
        assert req.model == "claude-haiku-4-5-20251001"


class TestBatchResult:
    def test_success_result(self):
        result = BatchResult(
            custom_id="test-1",
            content="Hello!",
            usage={"input_tokens": 10, "output_tokens": 5},
            success=True,
        )
        assert result.success
        assert result.error is None

    def test_error_result(self):
        result = BatchResult(
            custom_id="test-1",
            content="",
            success=False,
            error="Rate limited",
        )
        assert not result.success
        assert result.error == "Rate limited"


class TestBatchSubmission:
    @pytest.mark.asyncio
    async def test_submit_batch_returns_batch_id(self):
        mock_batch = MagicMock()
        mock_batch.id = "batch_abc123"

        with patch("src.agents.batch_processor.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.batches.create.return_value = mock_batch
            mock_anthropic.Anthropic.return_value = mock_client

            processor = BatchProcessor(api_key="test-key")
            batch_id = await processor.submit_batch([_make_request()])

            assert batch_id == "batch_abc123"
            mock_client.messages.batches.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_batch_formats_requests(self):
        mock_batch = MagicMock()
        mock_batch.id = "batch_xyz"

        with patch("src.agents.batch_processor.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.batches.create.return_value = mock_batch
            mock_anthropic.Anthropic.return_value = mock_client

            processor = BatchProcessor(api_key="test-key")
            await processor.submit_batch([
                _make_request("req-1"),
                _make_request("req-2"),
            ])

            call_args = mock_client.messages.batches.create.call_args
            requests = call_args[1]["requests"]
            assert len(requests) == 2
            assert requests[0]["custom_id"] == "req-1"
            assert requests[1]["custom_id"] == "req-2"


class TestBatchStatus:
    @pytest.mark.asyncio
    async def test_check_completed_batch(self):
        mock_batch = MagicMock()
        mock_batch.processing_status = "ended"
        mock_batch.request_counts.processing = 0
        mock_batch.request_counts.succeeded = 3
        mock_batch.request_counts.errored = 0
        mock_batch.request_counts.canceled = 0
        mock_batch.request_counts.expired = 0

        with patch("src.agents.batch_processor.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.batches.retrieve.return_value = mock_batch
            mock_anthropic.Anthropic.return_value = mock_client

            processor = BatchProcessor(api_key="test-key")
            status = await processor.check_batch_status("batch_123")

            assert status["is_complete"]
            assert status["request_counts"]["succeeded"] == 3

    @pytest.mark.asyncio
    async def test_check_pending_batch(self):
        mock_batch = MagicMock()
        mock_batch.processing_status = "in_progress"
        mock_batch.request_counts.processing = 2
        mock_batch.request_counts.succeeded = 1
        mock_batch.request_counts.errored = 0
        mock_batch.request_counts.canceled = 0
        mock_batch.request_counts.expired = 0

        with patch("src.agents.batch_processor.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.batches.retrieve.return_value = mock_batch
            mock_anthropic.Anthropic.return_value = mock_client

            processor = BatchProcessor(api_key="test-key")
            status = await processor.check_batch_status("batch_123")

            assert not status["is_complete"]
            assert status["request_counts"]["processing"] == 2


class TestBatchTimeout:
    @pytest.mark.asyncio
    async def test_submit_and_wait_timeout(self):
        mock_batch = MagicMock()
        mock_batch.id = "batch_timeout"

        mock_status = MagicMock()
        mock_status.processing_status = "in_progress"
        mock_status.request_counts.processing = 1
        mock_status.request_counts.succeeded = 0
        mock_status.request_counts.errored = 0
        mock_status.request_counts.canceled = 0
        mock_status.request_counts.expired = 0

        with patch("src.agents.batch_processor.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.batches.create.return_value = mock_batch
            mock_client.messages.batches.retrieve.return_value = mock_status
            mock_anthropic.Anthropic.return_value = mock_client

            processor = BatchProcessor(api_key="test-key")

            with pytest.raises(BatchTimeoutError):
                await processor.submit_and_wait(
                    [_make_request()],
                    timeout_seconds=1,
                    poll_interval=1,
                )
