import time
import pytest
from unittest.mock import patch
from moesif_litellm.callback import MoesifLogger


def _make_logger(**kwargs):
    kwargs.setdefault("application_id", "test-app-id")

    def _close_coro(coro):
        coro.close()

    with patch("asyncio.create_task", side_effect=_close_coro):
        return MoesifLogger(**kwargs)


@pytest.fixture
def logger():
    return _make_logger()


@pytest.fixture
def stream_kwargs(base_kwargs):
    """Kwargs that mimic what LiteLLM passes for a completed streaming response."""
    kw = dict(base_kwargs)
    kw["stream"] = True
    # LiteLLM assembles a complete ModelResponse and puts it here before calling
    # async_log_success_event — same structure as a non-streaming response.
    payload = dict(kw["standard_logging_object"])
    payload["call_type"] = "acompletion"
    payload["response"] = {
        "id": "chatcmpl-stream-xyz",
        "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
    }
    kw["standard_logging_object"] = payload
    return kw


class TestStreamingSupport:
    async def test_streaming_response_queued(self, logger, stream_kwargs):
        """async_log_success_event captures the assembled streaming response."""
        assert len(logger.log_queue) == 0
        await logger.async_log_success_event(stream_kwargs, None, time.time(), time.time())
        assert len(logger.log_queue) == 1

    async def test_streaming_event_has_correct_fields(self, logger, stream_kwargs):
        await logger.async_log_success_event(stream_kwargs, None, time.time(), time.time())
        event = logger.log_queue[0]
        assert event["request"]["verb"] == "POST"
        assert event["response"]["status"] == 200
        assert event["weight"] == 1

    async def test_intermediate_stream_chunks_ignored(self, logger, stream_kwargs):
        """async_log_stream_event is a no-op — intermediate chunks are not queued."""
        for _ in range(5):
            await logger.async_log_stream_event(stream_kwargs, {"chunk": "data"}, time.time(), time.time())
        assert len(logger.log_queue) == 0

    async def test_streaming_failure_queued(self, logger, base_kwargs):
        """Streaming errors are captured via async_log_failure_event."""
        kw = dict(base_kwargs)
        kw["stream"] = True
        payload = dict(kw["standard_logging_object"])
        payload["status"] = "failure"
        payload["error_str"] = "Stream interrupted"
        payload["error_information"] = {"error_class": "APIConnectionError"}
        kw["standard_logging_object"] = payload
        await logger.async_log_failure_event(kw, None, time.time(), time.time())
        assert len(logger.log_queue) == 1
        assert logger.log_queue[0]["response"]["status"] != 200