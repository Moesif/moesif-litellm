import asyncio
from typing import Optional

import httpx
from litellm._logging import verbose_logger
from litellm.integrations.custom_batch_logger import CustomBatchLogger

from moesif_litellm.config import MoesifConfig
from moesif_litellm.event_mapper import build_moesif_event

try:
    from moesif_litellm import __version__
except ImportError:
    __version__ = "0.1.0"


class MoesifLogger(CustomBatchLogger):

    def __init__(self, **config_kwargs):
        self.moesif_config = MoesifConfig(**config_kwargs)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._flush_task: Optional[asyncio.Task] = None

        # Schedule flush task, then create lock,
        # then call super(). The task won't actually run until the next event
        # loop iteration, by which point flush_lock is already set.
        try:
            self._flush_task = asyncio.create_task(self.periodic_flush())
        except RuntimeError:
            # No running event loop (e.g., created at module level in tests).
            # _ensure_flush_task() will retry on first async event.
            pass

        self.flush_lock = asyncio.Lock()

        super().__init__(
            flush_lock=self.flush_lock,
            batch_size=self.moesif_config.batch_size,
            flush_interval=self.moesif_config.flush_interval,
            max_queue_size=self.moesif_config.max_queue_size,
        )

    # ── LiteLLM async hooks ───────────────────────────────────────────────────

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._ensure_flush_task()
        try:
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=False,
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
            event = build_moesif_event(
                kwargs, response_obj, start_time, end_time,
                self.moesif_config, is_error=True,
            )
        except Exception:
            verbose_logger.exception("Moesif: error building failure event")
            return
        if event is not None:
            self.log_queue.append(event)
            if len(self.log_queue) >= self.batch_size:
                await self.flush_queue()

    # ── Sync stubs (SDK mode without async event loop) ────────────────────────

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
            self.log_queue.append(event)
            if len(self.log_queue) >= self.batch_size:
                asyncio.run(self._send_batch_now())

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
            self.log_queue.append(event)
            if len(self.log_queue) >= self.batch_size:
                asyncio.run(self._send_batch_now())

    # ── Batch sender ──────────────────────────────────────────────────────────

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

    # ── Internals ─────────────────────────────────────────────────────────────

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
        """One-shot send used by sync stubs."""
        async with self.flush_lock:
            await self.async_send_batch()

    def _ensure_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            try:
                self._flush_task = asyncio.create_task(self.periodic_flush())
            except RuntimeError:
                pass
