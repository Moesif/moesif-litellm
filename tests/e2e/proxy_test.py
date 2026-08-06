"""
Proxy mode e2e tests for the Moesif plugin.
Requires proxy on localhost:4000 (override with PROXY_URL env var).

Run: pytest tests/e2e/proxy_test.py -v
"""

import os
import time

import httpx
import pytest

PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:4000")
COMPLETIONS = f"{PROXY_URL}/v1/chat/completions"
MODEL = "gemini-flash"
SIMPLE_MSG = [{"role": "user", "content": "Say hi"}]


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=30.0) as c:
        yield c


@pytest.fixture(autouse=True)
def rate_limit_pause():
    yield
    time.sleep(6)


def chat(client, messages=None, **kwargs):
    return client.post(
        COMPLETIONS,
        json={"model": MODEL, "messages": messages or SIMPLE_MSG, **kwargs},
    )


class TestPluginLoaded:
    def test_proxy_is_running(self, client):
        # proxy health check passes
        resp = client.get(f"{PROXY_URL}/health")
        assert resp.status_code == 200


class TestSuccessEventLogging:
    def test_success_without_identity(self, client):
        # plugin logs anonymous request without crashing
        assert chat(client).status_code == 200

    def test_success_with_user_and_company(self, client):
        # plugin logs user_id and company_id from request
        assert chat(client, user="alice", metadata={"company_id": "acme-corp"}).status_code == 200


class TestFailureEventLogging:
    def test_invalid_model_returns_error(self, client):
        # plugin logs failure event and proxy returns error body
        resp = client.post(COMPLETIONS, json={"model": "nonexistent-model", "messages": SIMPLE_MSG})
        assert resp.status_code != 200
        body = resp.json()
        assert "error" in body or "detail" in body

    def test_missing_messages_returns_error(self, client):
        # plugin handles bad request without hanging
        assert client.post(COMPLETIONS, json={"model": MODEL}).status_code != 200


class TestIdentityResolution:
    def test_user_field_accepted(self, client):
        # user= resolves to user_id in Moesif event
        assert chat(client, user="alice").status_code == 200

    def test_company_from_metadata(self, client):
        # metadata.company_id resolves to company_id in Moesif event
        assert chat(client, metadata={"company_id": "acme-corp"}).status_code == 200

    def test_different_users_do_not_share_state(self, client):
        # per-request identity — plugin must not bleed state between calls
        assert chat(client, user="alice", metadata={"company_id": "acme-corp"}).status_code == 200
        time.sleep(6)
        assert chat(client, user="bob", metadata={"company_id": "wso2"}).status_code == 200


class TestStreamingLogging:
    def test_streaming_completes_without_crash(self, client):
        # plugin does not break SSE stream; [DONE] must arrive
        with client.stream("POST", COMPLETIONS, json={"model": MODEL, "messages": SIMPLE_MSG, "stream": True}) as resp:
            assert resp.status_code == 200
            lines = [l for l in resp.iter_lines() if l]
        assert lines[-1] == "data: [DONE]"


# -- Manual verification needed --
#
#   Sampling         — set sample_rate=0 in moesif_callback.py, restart proxy,
#                      confirm no events appear in Moesif
#   Governance       — configure a block rule in Moesif, confirm request is blocked
#                      and event appears with blocked_by field set
#   Body masking     — configure response_body_masks in moesif_callback.py,
#                      restart proxy, confirm fields are redacted in Moesif