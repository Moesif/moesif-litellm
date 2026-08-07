"""
SDK mode e2e tests for the Moesif plugin.
Makes real LiteLLM calls, waits for Moesif ingestion, then queries the
Management API to assert events landed with correct fields.

Required env vars:
  GEMINI_API_KEY             - Gemini API key
  MOESIF_APPLICATION_ID      - Moesif collector app ID
  MOESIF_MANAGEMENT_API_KEY  - Moesif Management API key

Run: pytest tests/e2e/sdk_test.py -v
"""

import asyncio
import datetime
import os
import time
import uuid

import httpx
import litellm
import pytest

from moesif_litellm import MoesifHandler

MGMT_URL = "https://api.moesif.com"
MODEL = "gemini/gemini-3.6-flash"
EMBEDDING_MODEL = "gemini/gemini-embedding-001"
INGESTION_WAIT = 15


def _flush_sync(logger):
    async def _do():
        await asyncio.sleep(3)
        async with logger.flush_lock:
            await logger.async_send_batch()
    asyncio.run(_do())


def _reset_litellm_callbacks(logger):
    # LiteLLM deduplicates CustomBatchLogger by class — remove stale instances before registering a new one
    from moesif_litellm import MoesifHandler
    for attr in ("success_callback", "_async_success_callback", "failure_callback", "_async_failure_callback"):
        old = getattr(litellm, attr, [])
        setattr(litellm, attr, [cb for cb in old if not isinstance(cb, MoesifHandler)])
    litellm.callbacks = [logger]


def _call(logger, user=None, company=None, model=MODEL, stream=False):
    # make a real LiteLLM call and flush the event to Moesif
    _reset_litellm_callbacks(logger)
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "say hi"}],
            user=user,
            metadata={"company_id": company} if company else {},
            stream=stream,
        )
        if stream:
            for _ in resp:  # consume stream so callbacks fire
                pass
    except Exception:
        pass
    finally:
        _flush_sync(logger)


def _query(mgmt_key, user_id):
    # fetch latest event for user_id from Moesif Management API
    from_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{MGMT_URL}/v1/search/~/search/events",
            params={"from": from_dt, "to": to_dt},
            headers={"Authorization": f"Bearer {mgmt_key}", "Content-Type": "application/json"},
            json={"query": {"term": {"user_id.raw": user_id}}, "size": 5},
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        return hits[0]["_source"] if hits else None


@pytest.fixture(scope="module")
def events():
    for var in ("GEMINI_API_KEY", "MOESIF_APPLICATION_ID", "MOESIF_MANAGEMENT_API_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} is not set")

    mgmt_key = os.environ["MOESIF_MANAGEMENT_API_KEY"]
    run = str(uuid.uuid4())[:8]

    sent = {
        "user_kwarg": {"user_id": f"sdk-user-{run}",     "company_id": f"sdk-co-{run}"},
        "callbacks":  {"user_id": f"sdk-cb-{run}",       "company_id": f"sdk-cb-co-{run}"},
        "failure":    {"user_id": f"sdk-fail-{run}",     "company_id": None},
        "streaming":  {"user_id": f"sdk-stream-{run}",   "company_id": f"sdk-stream-co-{run}"},
    }

    # success: user from user=, company from metadata.company_id
    _call(MoesifHandler(), user=sent["user_kwarg"]["user_id"], company=sent["user_kwarg"]["company_id"])
    time.sleep(6)

    # success: user and company from identify_user / identify_company callbacks
    uid, cid = sent["callbacks"]["user_id"], sent["callbacks"]["company_id"]
    _call(MoesifHandler(identify_user=lambda k, p: uid, identify_company=lambda k, p: cid))
    time.sleep(6)

    # failure: invalid model — no LLM quota consumed, must still log a failure event
    _call(MoesifHandler(identify_user=lambda k, p: sent["failure"]["user_id"]), model="nonexistent/model")
    time.sleep(6)

    # streaming: stream=True — plugin assembles full response and logs one event
    _call(MoesifHandler(), user=sent["streaming"]["user_id"], company=sent["streaming"]["company_id"], stream=True)

    # wait for Moesif ingestion before querying
    time.sleep(INGESTION_WAIT)

    for key, data in sent.items():
        data["event"] = _query(mgmt_key, data["user_id"])

    return sent


class TestSuccessEventLogging:
    def test_event_landed_for_user_kwarg(self, events):
        # user= call must produce an event in Moesif
        assert events["user_kwarg"]["event"] is not None

    def test_user_id_from_user_kwarg(self, events):
        # user= resolves to user_id in logged event
        e = events["user_kwarg"]["event"]
        assert e["user_id"] == events["user_kwarg"]["user_id"]

    def test_company_id_from_metadata(self, events):
        # metadata.company_id resolves to company_id in logged event
        e = events["user_kwarg"]["event"]
        assert e["company_id"] == events["user_kwarg"]["company_id"]

    def test_event_landed_for_identify_callbacks(self, events):
        # identify callback call must produce an event in Moesif
        assert events["callbacks"]["event"] is not None

    def test_user_id_from_identify_callback(self, events):
        # identify_user callback resolves to user_id in logged event
        e = events["callbacks"]["event"]
        assert e["user_id"] == events["callbacks"]["user_id"]

    def test_company_id_from_identify_callback(self, events):
        # identify_company callback resolves to company_id in logged event
        e = events["callbacks"]["event"]
        assert e["company_id"] == events["callbacks"]["company_id"]


class TestEventFields:
    def test_response_status_200(self, events):
        # successful call must have status 200 in Moesif event
        assert events["user_kwarg"]["event"]["response"]["status"] == 200

    def test_direction_is_outgoing(self, events):
        # SDK events must be direction=Outgoing
        assert events["user_kwarg"]["event"]["direction"] == "Outgoing"

    def test_litellm_metadata_has_model(self, events):
        # metadata.litellm.model must be populated
        meta = events["user_kwarg"]["event"].get("metadata", {}).get("litellm", {})
        assert meta.get("model")


class TestFailureEventLogging:
    def test_failure_event_landed(self, events):
        # error calls must still produce a logged event
        assert events["failure"]["event"] is not None

    def test_failure_event_status_not_200(self, events):
        # failure event response status must be non-200
        assert events["failure"]["event"]["response"]["status"] != 200


class TestStreamingEventLogging:
    def test_streaming_event_landed(self, events):
        # streaming call must produce a single assembled event
        assert events["streaming"]["event"] is not None

    def test_streaming_event_status_200(self, events):
        # assembled streaming event must have status 200
        assert events["streaming"]["event"]["response"]["status"] == 200

    def test_streaming_user_id_correct(self, events):
        # user_id must be captured correctly from streaming call
        e = events["streaming"]["event"]
        assert e["user_id"] == events["streaming"]["user_id"]


@pytest.fixture(scope="module")
def embedding_event():
    # Run: pytest tests/e2e/sdk_test.py::TestEmbeddingEventLogging -v
    for var in ("GEMINI_API_KEY", "MOESIF_APPLICATION_ID", "MOESIF_MANAGEMENT_API_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} is not set")

    mgmt_key = os.environ["MOESIF_MANAGEMENT_API_KEY"]
    user_id = f"sdk-embed-{str(uuid.uuid4())[:8]}"

    h = MoesifHandler(identify_user=lambda k, p: user_id)
    _reset_litellm_callbacks(h)
    try:
        litellm.embedding(model=EMBEDDING_MODEL, input=["hello world"])
    except Exception:
        pass
    finally:
        _flush_sync(h)

    time.sleep(INGESTION_WAIT)
    return {"user_id": user_id, "event": _query(mgmt_key, user_id)}


class TestEmbeddingEventLogging:
    def test_embedding_event_landed(self, embedding_event):
        # embedding call must produce a logged event in Moesif
        assert embedding_event["event"] is not None

    def test_embedding_uri_is_litellmsdk(self, embedding_event):
        # SDK mode embedding must use litellmsdk/embedding URI (Moesif normalises with leading slash)
        assert embedding_event["event"]["request"]["uri"].endswith("litellmsdk/embedding")

    def test_embedding_user_id_correct(self, embedding_event):
        # user= kwarg must resolve to user_id in the embedding event
        assert embedding_event["event"]["user_id"] == embedding_event["user_id"]

    def test_embedding_status_200(self, embedding_event):
        # successful embedding call must log status 200
        assert embedding_event["event"]["response"]["status"] == 200


# -- Manual verification needed --
#
#   Sampling         — set sample_rate=0 and confirm no events appear in Moesif
#   Anonymous calls  — send request without user/company, confirm event lands
#   Governance       — configure a block rule in Moesif, confirm request is blocked
#                      and event appears with blocked_by field set
#   Body masking     — configure response_body_masks, confirm fields are redacted