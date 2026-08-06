import asyncio
import re
import time
from typing import List, Optional

import httpx
from litellm._logging import verbose_logger

from moesif_litellm._endpoints import CONFIG, RULES


class GovernanceBlockedException(Exception):
    def __init__(self, status: int, body, headers: dict, rule_id: str):
        self.status = status
        self.body = body
        self.headers = headers
        self.rule_id = rule_id
        super().__init__(f"Request blocked by Moesif governance rule {rule_id} (status={status})")


class GovernanceRulesManager:
    """Fetches /v1/rules and /v1/config from Moesif, caches with ETag, refreshes every 60s."""

    REFRESH_INTERVAL = 60

    def __init__(self, application_id: str, base_url: str, user_agent: str):
        self._application_id = application_id
        self._base_url = base_url
        self._user_agent = user_agent

        self._rules: List[dict] = []
        self._rules_etag: Optional[str] = None
        self._fetched_once: bool = False

        # /v1/config maps entity_id → [{rules: rule_id, values: {...}}]
        self._user_entity_rules: dict = {}
        self._company_entity_rules: dict = {}
        self._user_sample_rates: dict = {}
        self._company_sample_rates: dict = {}
        self._config_etag: Optional[str] = None

        self._refresh_task: Optional[asyncio.Task] = None

    @property
    def rules(self) -> List[dict]:
        return self._rules

    def start(self):
        if self._refresh_task is None or self._refresh_task.done():
            try:
                self._refresh_task = asyncio.create_task(self._refresh_loop())
            except RuntimeError:
                pass

    async def fetch_once(self):
        """Blocking fetch on first async request — ensures rules are loaded before checking."""
        if not self._fetched_once:
            await asyncio.gather(self._fetch_rules(), self._fetch_config())
            self._fetched_once = True

    def get_effective_sample_rate(
        self,
        user_id: Optional[str],
        company_id: Optional[str],
        global_rate: int,
    ) -> int:
        """Return the minimum of global, user-level, and company-level sample rates."""
        rate = global_rate
        if user_id and user_id in self._user_sample_rates:
            rate = min(rate, self._user_sample_rates[user_id])
        if company_id and company_id in self._company_sample_rates:
            rate = min(rate, self._company_sample_rates[company_id])
        return rate

    def check_request_blocked(
        self,
        user_id: Optional[str],
        company_id: Optional[str],
        request_verb: str = "POST",
        request_route: str = "",
    ) -> Optional["GovernanceBlockedException"]:
        """Return a GovernanceBlockedException if any rule blocks this request, else None."""
        if not self._rules:
            return None

        rule_index = {r["_id"]: r for r in self._rules if "_id" in r}

        exc = self._check_entity_rules(
            entity_id=user_id,
            entity_rules_map=self._user_entity_rules,
            rule_type="user",
            rule_index=rule_index,
            request_verb=request_verb,
            request_route=request_route,
        )
        if exc:
            return exc

        exc = self._check_entity_rules(
            entity_id=company_id,
            entity_rules_map=self._company_entity_rules,
            rule_type="company",
            rule_index=rule_index,
            request_verb=request_verb,
            request_route=request_route,
        )
        if exc:
            return exc

        # Regex rules apply regardless of entity identity
        for rule in self._rules:
            if rule.get("type") != "regex" or not rule.get("block"):
                continue
            if self._regex_conditions_match(rule, request_verb, request_route):
                return self._make_exception(rule)

        return None

    def _check_entity_rules(
        self,
        entity_id: Optional[str],
        entity_rules_map: dict,
        rule_type: str,
        rule_index: dict,
        request_verb: str,
        request_route: str,
    ) -> Optional[GovernanceBlockedException]:
        for rule in self._rules:
            if rule.get("type") != rule_type or not rule.get("block"):
                continue

            applied_to_unidentified = rule.get("applied_to_unidentified", False)

            if entity_id is None:
                if not applied_to_unidentified:
                    continue
                if self._regex_conditions_match(rule, request_verb, request_route):
                    return self._make_exception(rule)
            else:
                entity_rule_entries = entity_rules_map.get(entity_id, [])
                rule_ids_for_entity = {e.get("rules") for e in entity_rule_entries}
                rule_id = rule.get("_id")

                in_cohort = rule_id in rule_ids_for_entity
                applied_to = rule.get("applied_to", "matching")

                should_apply = (applied_to == "matching" and in_cohort) or \
                               (applied_to == "not_matching" and not in_cohort)

                if should_apply and self._regex_conditions_match(rule, request_verb, request_route):
                    return self._make_exception(rule, entity_id, entity_rule_entries)

        return None

    def _regex_conditions_match(self, rule: dict, verb: str, route: str) -> bool:
        regex_configs = rule.get("regex_config") or []
        if not regex_configs:
            return True  # no conditions = always matches

        for config in regex_configs:
            conditions = config.get("conditions") or []
            if all(self._condition_matches(c, verb, route) for c in conditions):
                return True  # OR across config blocks

        return False

    def _condition_matches(self, condition: dict, verb: str, route: str) -> bool:
        path = condition.get("path", "")
        pattern = condition.get("value", "")
        if not pattern:
            return True

        if path == "request.verb":
            target = verb
        elif path == "request.route":
            target = route
        else:
            return True  # unknown path — fail open

        try:
            return bool(re.search(pattern, target, re.IGNORECASE))
        except re.error:
            return False

    def _make_exception(
        self,
        rule: dict,
        entity_id: Optional[str] = None,
        entity_entries: Optional[list] = None,
    ) -> GovernanceBlockedException:
        response = rule.get("response") or {}
        status = response.get("status", 429)
        headers = response.get("headers") or {}
        body = response.get("body", {"error": "Request blocked by governance rule"})

        if entity_entries and isinstance(body, str):
            values = {}
            rule_id = rule.get("_id")
            for entry in entity_entries:
                if entry.get("rules") == rule_id:
                    values = entry.get("values") or {}
                    break
            for k, v in values.items():
                body = body.replace(f"{{{{{k}}}}}", str(v))

        return GovernanceBlockedException(
            status=status,
            body=body,
            headers=headers,
            rule_id=rule.get("_id", "unknown"),
        )

    async def _refresh_loop(self):
        await asyncio.gather(self._fetch_rules(), self._fetch_config())
        while True:
            await asyncio.sleep(self.REFRESH_INTERVAL)
            await asyncio.gather(self._fetch_rules(), self._fetch_config())

    async def _fetch_rules(self):
        headers = {
            "X-Moesif-Application-Id": self._application_id,
            "User-Agent": self._user_agent,
        }
        if self._rules_etag:
            headers["If-None-Match"] = self._rules_etag

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            ) as client:
                response = await client.get(f"{self._base_url}{RULES}", headers=headers)

            if response.status_code == 304:
                return
            if response.status_code != 200:
                verbose_logger.warning("Moesif governance: HTTP %s fetching rules", response.status_code)
                return

            self._rules = response.json()
            self._rules_etag = response.headers.get("x-moesif-rules-tag") or response.headers.get("etag")
            verbose_logger.debug("Moesif governance: loaded %d rules", len(self._rules))

        except Exception:
            verbose_logger.exception("Moesif governance: error fetching rules")

    async def _fetch_config(self):
        headers = {
            "X-Moesif-Application-Id": self._application_id,
            "User-Agent": self._user_agent,
        }
        if self._config_etag:
            headers["If-None-Match"] = self._config_etag

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            ) as client:
                response = await client.get(f"{self._base_url}{CONFIG}", headers=headers)

            if response.status_code == 304:
                return
            if response.status_code != 200:
                verbose_logger.warning("Moesif governance: HTTP %s fetching config", response.status_code)
                return

            data = response.json()
            self._user_entity_rules = data.get("user_rules") or {}
            self._company_entity_rules = data.get("company_rules") or {}
            self._user_sample_rates = data.get("user_sample_rate") or {}
            self._company_sample_rates = data.get("company_sample_rate") or {}
            self._config_etag = response.headers.get("x-moesif-config-etag") or response.headers.get("etag")
            verbose_logger.debug(
                "Moesif governance: config loaded (%d user entries, %d company entries)",
                len(self._user_entity_rules), len(self._company_entity_rules),
            )

        except Exception:
            verbose_logger.exception("Moesif governance: error fetching config")

    def rules_by_type(self, rule_type: str) -> List[dict]:
        return [r for r in self._rules if r.get("type") == rule_type]

    def get_rule(self, rule_id: str) -> Optional[dict]:
        for r in self._rules:
            if r.get("_id") == rule_id:
                return r
        return None
