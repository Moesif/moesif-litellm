import asyncio
from typing import Optional

import httpx
from litellm._logging import verbose_logger
from litellm.integrations.custom_batch_logger import CustomBatchLogger

from moesif_litellm.config import MoesifConfig
from moesif_litellm.event_mapper import build_moesif_event
from moesif_litellm.governance import GovernanceBlockedException, GovernanceRulesManager
from moesif_litellm.user_resolver import resolve_company_id, resolve_user_id

try:
    from moesif_litellm import __version__
except ImportError:
    __version__ = "0.1.0"


def _make_block_exception(exc: "GovernanceBlockedException") -> Exception:
    import litellm
    message = str(exc)
    status = exc.status

    mock_request = httpx.Request(method="POST", url="https://api.moesif.net")
    mock_response = httpx.Response(status_code=status, request=mock_request)

    if status == 401:
        return litellm.AuthenticationError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 403:
        return litellm.PermissionDeniedError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 404:
        return litellm.NotFoundError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 408:
        return litellm.Timeout(message=message, llm_provider="moesif", model="")
    if status == 422:
        return litellm.UnprocessableEntityError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 429:
        return litellm.RateLimitError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 500:
        return litellm.InternalServerError(message=message, llm_provider="moesif", model="", response=mock_response)
    if status == 503:
        return litellm.ServiceUnavailableError(message=message, llm_provider="moesif", model="", response=mock_response)
    return litellm.BadRequestError(message=message, llm_provider="moesif", model="", response=mock_response)


class MoesifLogger(CustomBatchLogger):

    def __init__(self, **config_kwargs):
        self.moesif_config = MoesifConfig(**config_kwargs)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._flush_task: Optional[asyncio.Task] = None

        self.governance = GovernanceRulesManager(
            application_id=self.moesif_config.application_id,
            base_url=self.moesif_config.moesif_base_url,
            user_agent=f"moesif-litellm/{__version__}",
        )

        try:
            self._flush_task = asyncio.create_task(self.periodic_flush())
            self.governance.start()
        except RuntimeError:
            # No running event loop (created at module level in sync scripts).
            # _ensure_flush_task() retries on the first async event.
            pass

        self.flush_lock = asyncio.Lock()

        super().__init__(
            flush_lock=self.flush_lock,
            batch_size=self.moesif_config.batch_size,
            flush_interval=self.moesif_config.flush_interval,
            max_queue_size=self.moesif_config.max_queue_size,
        )

    def pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if not self.governance.rules:
            return data

        payload = data.get("standard_logging_object") or {}
        user_id = resolve_user_id(data, payload, self.moesif_config)
        company_id = resolve_company_id(data, payload, self.moesif_config)
        model = data.get("model", "")

        exc = self.governance.check_request_blocked(
            user_id=user_id,
            company_id=company_id,
            request_verb="POST",
            request_route=f"/v1/chat/completions/{model}",
        )
        if exc:
            verbose_logger.warning(
                "Moesif governance: blocking request (sync) for user=%s company=%s rule=%s",
                user_id, company_id, exc.rule_id,
            )
            self._sync_send([self._build_blocked_event(data, exc, user_id, company_id)])
            raise _make_block_exception(exc)

        return data

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        self._ensure_flush_task()
        if not self.governance._fetched_once:
            await self.governance.fetch_once()
        if not self.governance.rules:
            return data

        payload = data.get("standard_logging_object") or {}
        user_id = resolve_user_id(data, payload, self.moesif_config)
        company_id = resolve_company_id(data, payload, self.moesif_config)
        model = data.get("model", "")

        exc = self.governance.check_request_blocked(
            user_id=user_id,
            company_id=company_id,
            request_verb="POST",
            request_route=f"/v1/chat/completions/{model}",
        )
        if exc:
            verbose_logger.warning(
                "Moesif governance: blocking request for user=%s company=%s rule=%s",
                user_id, company_id, exc.rule_id,
            )
            await self._send_blocked_event(data, exc, user_id, company_id)
            raise _make_block_exception(exc)

        return data

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        # Intermediate chunks are skipped; async_log_success_event receives the
        # fully assembled response once streaming completes.
        pass

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._ensure_flush_task()
        try:
            payload = kwargs.get("standard_logging_object") or {}
            user_id = resolve_user_id(kwargs, payload, self.moesif_config)
            company_id = resolve_company_id(kwargs, payload, self.moesif_config)
            sample_rate = self.governance.get_effective_sample_rate(
                user_id, company_id, self.moesif_config.sample_rate
            )
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=False, effective_sample_rate=sample_rate,
            )
        except Exception:
            verbose_logger.exception("Moesif: error building success event")
            return
        if event is not None:
            self.log_queue.append(event)
            if len(self.log_queue) >= self.batch_size:
                await self.flush_queue()

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._ensure_flush_task()
        try:
            payload = kwargs.get("standard_logging_object") or {}
            user_id = resolve_user_id(kwargs, payload, self.moesif_config)
            company_id = resolve_company_id(kwargs, payload, self.moesif_config)
            sample_rate = self.governance.get_effective_sample_rate(
                user_id, company_id, self.moesif_config.sample_rate
            )
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=True, effective_sample_rate=sample_rate,
            )
        except Exception:
            verbose_logger.exception("Moesif: error building failure event")
            return
        if event is not None:
            self.log_queue.append(event)
            if len(self.log_queue) >= self.batch_size:
                await self.flush_queue()

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=False,
            )
        except Exception:
            verbose_logger.exception("Moesif: error building success event (sync)")
            return
        if event is not None:
            self._sync_send([event])

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=True,
            )
        except Exception:
            verbose_logger.exception("Moesif: error building failure event (sync)")
            return
        if event is not None:
            self._sync_send([event])

    async def async_send_batch(self):
        if not self.log_queue:
            return

        batch = self.log_queue[:]
        self.log_queue = []

        try:
            client = await self._get_http_client()
            response = await client.post(
                f"{self.moesif_config.moesif_base_url}/v1/events/batch",
                json=batch,
            )
            if response.status_code != 201:
                self.log_queue = batch + self.log_queue
                verbose_logger.warning(
                    "Moesif: unexpected HTTP %s sending %d events; re-queued",
                    response.status_code, len(batch),
                )
        except Exception:
            self.log_queue = batch + self.log_queue
            raise

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
                headers={
                    "X-Moesif-Application-Id": self.moesif_config.application_id,
                    "Content-Type": "application/json",
                    "User-Agent": f"moesif-litellm/{__version__}",
                },
            )
        return self._http_client

    async def _send_batch_now(self):
        async with self.flush_lock:
            await self.async_send_batch()

    def _sync_send(self, batch: list):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
                headers={
                    "X-Moesif-Application-Id": self.moesif_config.application_id,
                    "Content-Type": "application/json",
                    "User-Agent": f"moesif-litellm/{__version__}",
                },
            ) as client:
                response = client.post(
                    f"{self.moesif_config.moesif_base_url}/v1/events/batch",
                    json=batch,
                )
                if response.status_code != 201:
                    verbose_logger.warning(
                        "Moesif: unexpected HTTP %s sending %d events (sync)",
                        response.status_code, len(batch),
                    )
        except Exception:
            verbose_logger.exception("Moesif: error sending events (sync)")

    def _build_blocked_event(self, data: dict, exc: "GovernanceBlockedException", user_id, company_id) -> dict:
        import datetime
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")
        model = data.get("model", "")
        return {
            "request": {
                "time": now,
                "verb": "POST",
                "uri": f"{self.moesif_config.moesif_base_url}/v1/chat/completions",
                "headers": {"content-type": "application/json"},
                "body": {"model": model, "messages": data.get("messages", [])} if self.moesif_config.capture_request_body else None,
            },
            "response": {
                "time": now,
                "status": exc.status,
                "headers": exc.headers or {},
                "body": exc.body if self.moesif_config.capture_response_body else None,
            },
            "user_id": user_id,
            "company_id": company_id,
            "direction": "Outgoing",
            "weight": 1,
            "blocked_by": exc.rule_id,
            "metadata": {
                "litellm": {"model": model},
            },
        }

    async def _send_blocked_event(self, data: dict, exc: "GovernanceBlockedException", user_id, company_id):
        try:
            client = await self._get_http_client()
            await client.post(
                f"{self.moesif_config.moesif_base_url}/v1/events/batch",
                json=[self._build_blocked_event(data, exc, user_id, company_id)],
            )
        except Exception:
            verbose_logger.exception("Moesif: error sending blocked event")

    def _ensure_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            try:
                self._flush_task = asyncio.create_task(self.periodic_flush())
            except RuntimeError:
                pass
        self.governance.start()
