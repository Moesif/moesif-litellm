import asyncio
import time
import pytest
import respx
import httpx
from unittest.mock import patch
from moesif_litellm.callback import MoesifLogger


def _make_logger(**kwargs):
    kwargs.setdefault("application_id", "test-app-id")

    def _close_coro(coro):
        coro.close()  # prevent "coroutine never awaited" warnings in test context

    with patch("asyncio.create_task", side_effect=_close_coro):
        logger = MoesifLogger(**kwargs)
    return logger


@pytest.fixture
def logger():
    return _make_logger()


@pytest.fixture
def kwargs_success(base_kwargs):
    return base_kwargs


@pytest.fixture
def kwargs_failure(base_kwargs):
    kw = dict(base_kwargs)
    payload = dict(kw["standard_logging_object"])
    payload["status"] = "failure"
    payload["error_str"] = "Timeout"
    payload["error_information"] = {"error_class": "Timeout"}
    kw["standard_logging_object"] = payload
    return kw


class TestQueueAccumulation:
    async def test_success_event_added_to_queue(self, logger, kwargs_success):
        assert len(logger.log_queue) == 0
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())
        assert len(logger.log_queue) == 1

    async def test_failure_event_added_to_queue(self, logger, kwargs_failure):
        await logger.async_log_failure_event(kwargs_failure, None, time.time(), time.time())
        assert len(logger.log_queue) == 1

    async def test_multiple_events_accumulate(self, logger, kwargs_success):
        for _ in range(5):
            await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())
        assert len(logger.log_queue) == 5

    async def test_sampled_out_event_not_queued(self, logger, kwargs_success):
        logger.moesif_config.sample_rate = 0
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())
        assert len(logger.log_queue) == 0


class TestBatchFlushTrigger:
    async def test_flush_triggered_at_batch_size(self, kwargs_success):
        logger = _make_logger(batch_size=3)
        logger.flush_lock = asyncio.Lock()

        flush_called = []

        async def fake_flush():
            flush_called.append(True)

        logger.flush_queue = fake_flush

        for _ in range(3):
            await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())

        assert flush_called, "flush_queue should have been called when batch_size reached"


class TestAsyncSendBatch:
    async def test_sends_to_correct_url(self, logger, kwargs_success):
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())
        assert len(logger.log_queue) == 1

        with respx.mock:
            respx.post("https://api.moesif.net/v1/events/batch").mock(
                return_value=httpx.Response(201)
            )
            await logger.async_send_batch()

        assert len(logger.log_queue) == 0

    async def test_sends_application_id_header(self, logger, kwargs_success):
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())

        with respx.mock:
            route = respx.post("https://api.moesif.net/v1/events/batch").mock(
                return_value=httpx.Response(201)
            )
            await logger.async_send_batch()
            assert route.called
            sent_headers = route.calls[0].request.headers
            assert sent_headers["x-moesif-application-id"] == "test-app-id"

    async def test_non_201_requeues_events(self, logger, kwargs_success):
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())
        assert len(logger.log_queue) == 1

        with respx.mock:
            respx.post("https://api.moesif.net/v1/events/batch").mock(
                return_value=httpx.Response(500)
            )
            await logger.async_send_batch()

        assert len(logger.log_queue) == 1, "Events should be re-queued on non-201"

    async def test_http_exception_requeues_and_raises(self, logger, kwargs_success):
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())

        with respx.mock:
            respx.post("https://api.moesif.net/v1/events/batch").mock(
                side_effect=httpx.ConnectError("unreachable")
            )
            with pytest.raises(httpx.ConnectError):
                await logger.async_send_batch()

        assert len(logger.log_queue) == 1, "Events should be re-queued on exception"

    async def test_empty_queue_is_noop(self, logger):
        with respx.mock:
            respx.post("https://api.moesif.net/v1/events/batch").mock(
                return_value=httpx.Response(201)
            )
            await logger.async_send_batch()
            assert not respx.calls, "No HTTP request should be made for empty queue"

    async def test_batch_payload_is_json_array(self, logger, kwargs_success):
        for _ in range(3):
            await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())

        with respx.mock:
            route = respx.post("https://api.moesif.net/v1/events/batch").mock(
                return_value=httpx.Response(201)
            )
            await logger.async_send_batch()
            import json
            body = json.loads(route.calls[0].request.content)
            assert isinstance(body, list)
            assert len(body) == 3


class TestSyncPreCallHook:
    def test_blocks_when_rule_matches(self, logger):
        import litellm
        logger.governance._rules = [{
            "_id": "rule-sync-1",
            "type": "regex",
            "block": True,
            "response": {"status": 403, "headers": {}, "body": {"error": "blocked"}},
            "regex_config": [{"conditions": [{"path": "request.route", "value": "gemini"}]}],
        }]
        logger.governance._fetched_once = True
        data = {"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}]}

        with respx.mock:
            respx.post("https://api.moesif.net/v1/events/batch").mock(return_value=httpx.Response(201))
            with pytest.raises(litellm.PermissionDeniedError):
                logger.pre_call_hook(None, None, data, "completion")

    def test_passes_when_no_rules(self, logger):
        logger.governance._rules = []
        data = {"model": "gemini-flash", "messages": []}
        result = logger.pre_call_hook(None, None, data, "completion")
        assert result is data

    def test_passes_when_rule_does_not_match(self, logger):
        logger.governance._rules = [{
            "_id": "rule-sync-2",
            "type": "regex",
            "block": True,
            "response": {"status": 403, "headers": {}, "body": {"error": "blocked"}},
            "regex_config": [{"conditions": [{"path": "request.route", "value": "openai"}]}],
        }]
        logger.governance._fetched_once = True
        data = {"model": "gemini-flash", "messages": []}
        result = logger.pre_call_hook(None, None, data, "completion")
        assert result is data


class TestConfig:
    def test_missing_app_id_raises(self):
        import os
        os.environ.pop("MOESIF_APPLICATION_ID", None)
        with pytest.raises(ValueError, match="application_id"):
            _make_logger(application_id="")

    async def test_custom_base_url(self, kwargs_success):
        logger = _make_logger(moesif_base_url="https://my-moesif.example.com")
        await logger.async_log_success_event(kwargs_success, None, time.time(), time.time())

        with respx.mock:
            route = respx.post("https://my-moesif.example.com/v1/events/batch").mock(
                return_value=httpx.Response(201)
            )
            await logger.async_send_batch()
            assert route.called
