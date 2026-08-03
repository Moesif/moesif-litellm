import asyncio
import time
from typing import Dict, List, Optional

import httpx
from litellm._logging import verbose_logger


class GovernanceRulesManager:
    """Fetches and caches governance rules from Moesif /v1/rules.

    Refreshes every rules_refresh_interval seconds using ETag-based caching
    so unchanged rules never re-download.
    """

    REFRESH_INTERVAL = 60  # seconds

    def __init__(self, application_id: str, base_url: str, user_agent: str):
        self._application_id = application_id
        self._base_url = base_url
        self._user_agent = user_agent

        self._rules: List[dict] = []
        self._etag: Optional[str] = None
        self._last_fetch: float = 0.0
        self._refresh_task: Optional[asyncio.Task] = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def rules(self) -> List[dict]:
        return self._rules

    def start(self):
        """Start the background refresh task. Must be called from a running event loop."""
        if self._refresh_task is None or self._refresh_task.done():
            try:
                self._refresh_task = asyncio.create_task(self._refresh_loop())
            except RuntimeError:
                pass

    async def fetch_once(self):
        """Fetch rules immediately (used on first async event if start() couldn't run)."""
        await self._fetch_rules()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _refresh_loop(self):
        await self._fetch_rules()
        while True:
            await asyncio.sleep(self.REFRESH_INTERVAL)
            await self._fetch_rules()

    async def _fetch_rules(self):
        headers = {
            "X-Moesif-Application-Id": self._application_id,
            "User-Agent": self._user_agent,
        }
        if self._etag:
            headers["If-None-Match"] = self._etag

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            ) as client:
                response = await client.get(
                    f"{self._base_url}/v1/rules",
                    headers=headers,
                )

            if response.status_code == 304:
                verbose_logger.debug("Moesif governance: rules unchanged (304)")
                return

            if response.status_code != 200:
                verbose_logger.warning(
                    "Moesif governance: unexpected HTTP %s fetching rules",
                    response.status_code,
                )
                return

            self._rules = response.json()
            self._etag = response.headers.get("x-moesif-rules-tag") or response.headers.get("etag")
            self._last_fetch = time.time()
            verbose_logger.debug(
                "Moesif governance: loaded %d rules (etag=%s)",
                len(self._rules), self._etag,
            )

        except Exception:
            verbose_logger.exception("Moesif governance: error fetching rules")

    def rules_by_type(self, rule_type: str) -> List[dict]:
        return [r for r in self._rules if r.get("type") == rule_type]

    def get_rule(self, rule_id: str) -> Optional[dict]:
        for r in self._rules:
            if r.get("_id") == rule_id:
                return r
        return None