"""
E2E verification test — sends real LLM events and queries Moesif Management API
to assert they landed with correct fields.

Required env vars:
  GEMINI_API_KEY              — Gemini API key
  MOESIF_APPLICATION_ID       — Moesif collector app ID
  MOESIF_MANAGEMENT_API_KEY   — Moesif Management API token
                                (Moesif dashboard → bottom-left API keys → Management API Keys)

Run:
  cd /path/to/moesif-litellm
  GEMINI_API_KEY=... MOESIF_APPLICATION_ID=... MOESIF_MANAGEMENT_API_KEY=... \
    python tests/e2e/e2e_verify_test.py
"""

import asyncio
import os
import time
import uuid

import httpx
import litellm

from moesif_litellm import MoesifHandler

# ── Env validation ─────────────────────────────────────────────────────────────

for var in ("GEMINI_API_KEY", "MOESIF_APPLICATION_ID", "MOESIF_MANAGEMENT_API_KEY"):
    if not os.environ.get(var):
        raise SystemExit(f"ERROR: {var} is not set.")

MGMT_KEY = os.environ["MOESIF_MANAGEMENT_API_KEY"]
MGMT_URL = "https://api.moesif.com/v1"
MODEL = "gemini/gemini-3.6-flash"
INGESTION_WAIT_SECONDS = 15  # Moesif ingestion delay

# ── Unique run ID so each test run is identifiable ─────────────────────────────

RUN_ID = str(uuid.uuid4())[:8]
print(f"\nTest run ID: {RUN_ID}")

# ── Scenarios ──────────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "SDK success — user from user=, company from metadata",
        "user_id": f"e2e-user-{RUN_ID}",
        "company_id": f"e2e-company-{RUN_ID}",
        "use_callbacks": False,
        "expect_status": 200,
    },
    {
        "name": "SDK success — user and company from identify callbacks",
        "user_id": f"e2e-cb-user-{RUN_ID}",
        "company_id": f"e2e-cb-company-{RUN_ID}",
        "use_callbacks": True,
        "expect_status": 200,
    },
]

# ── Moesif query helper ────────────────────────────────────────────────────────

def query_events(user_id: str) -> list:
    """Query Moesif Management API for events belonging to user_id."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{MGMT_URL}/search/~/events/search",
            headers={
                "Authorization": f"Bearer {MGMT_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "filter": {
                    "bool": {
                        "must": [
                            {"term": {"user_id.raw": user_id}}
                        ]
                    }
                },
                "size": 5,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", {}).get("hits", [])


# ── Send events ────────────────────────────────────────────────────────────────

async def _flush(logger: MoesifHandler):
    await asyncio.sleep(3)
    async with logger.flush_lock:
        await logger.async_send_batch()


results = []

for scenario in SCENARIOS:
    print(f"\n{'─' * 60}")
    print(f"Scenario: {scenario['name']}")

    if scenario["use_callbacks"]:
        uid, cid = scenario["user_id"], scenario["company_id"]
        logger = MoesifHandler(
            identify_user=lambda k, p, u=uid: u,
            identify_company=lambda k, p, c=cid: c,
            debug=True,
        )
    else:
        logger = MoesifHandler(debug=True)

    litellm.callbacks = [logger]

    try:
        litellm.completion(
            model=MODEL,
            messages=[{"role": "user", "content": "say hi"}],
            user=scenario["user_id"] if not scenario["use_callbacks"] else None,
            metadata={"company_id": scenario["company_id"]} if not scenario["use_callbacks"] else {},
        )
        print(f"  LLM call: OK")
        actual_status = 200
    except Exception as e:
        print(f"  LLM call: FAILED ({type(e).__name__})")
        actual_status = 500

    asyncio.run(_flush(logger))
    results.append({
        "scenario": scenario,
        "actual_status": actual_status,
    })
    time.sleep(60)  # avoid Gemini free tier per-minute rate limit

# ── Wait for Moesif ingestion ──────────────────────────────────────────────────

print(f"\n{'─' * 60}")
print(f"Waiting {INGESTION_WAIT_SECONDS}s for Moesif ingestion...")
time.sleep(INGESTION_WAIT_SECONDS)

# ── Verify ─────────────────────────────────────────────────────────────────────

print(f"\n{'─' * 60}")
print("Verifying events in Moesif...\n")

passed = 0
failed = 0

for result in results:
    scenario = result["scenario"]
    name = scenario["name"]
    user_id = scenario["user_id"]
    company_id = scenario["company_id"]
    expect_status = scenario["expect_status"]

    try:
        hits = query_events(user_id)
    except Exception as e:
        print(f"  FAIL [{name}]: query failed — {e}")
        failed += 1
        continue

    if not hits:
        print(f"  FAIL [{name}]: no events found for user_id={user_id!r}")
        failed += 1
        continue

    event = hits[0]["_source"]
    errors = []

    # Assert user_id
    if event.get("user_id") != user_id:
        errors.append(f"user_id: expected {user_id!r}, got {event.get('user_id')!r}")

    # Assert company_id
    if event.get("company_id") != company_id:
        errors.append(f"company_id: expected {company_id!r}, got {event.get('company_id')!r}")

    # Assert status
    actual = event.get("response", {}).get("status")
    if actual != expect_status:
        errors.append(f"status: expected {expect_status}, got {actual}")

    # Assert direction
    if event.get("direction") != "Outgoing":
        errors.append(f"direction: expected 'Outgoing', got {event.get('direction')!r}")

    # Assert metadata has litellm fields
    litellm_meta = event.get("metadata", {}).get("litellm", {})
    if not litellm_meta.get("model"):
        errors.append("metadata.litellm.model is missing")

    if errors:
        print(f"  FAIL [{name}]:")
        for err in errors:
            print(f"       {err}")
        failed += 1
    else:
        print(f"  PASS [{name}]")
        print(f"       user_id={event['user_id']!r} company_id={event['company_id']!r} "
              f"status={event['response']['status']} model={litellm_meta.get('model')!r}")
        passed += 1

# ── Summary ────────────────────────────────────────────────────────────────────

print(f"\n{'═' * 60}")
print(f"Results: {passed} passed, {failed} failed out of {len(results)} scenarios")
if failed:
    raise SystemExit(1)
