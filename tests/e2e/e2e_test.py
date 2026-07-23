"""
End-to-end smoke test: one real Anthropic call → Moesif event appears in dashboard.

Before running:
  export OPENAI_API_KEY=sk-...
  export MOESIF_APPLICATION_ID=your-app-id

Run with:
  /tmp/moesif-litellm-venv/bin/python e2e_test.py
"""

import asyncio
import os
import time

import litellm
from moesif_litellm import MoesifLogger

# ── Validate env vars ──────────────────────────────────────────────────────────
for var in ("GEMINI_API_KEY", "MOESIF_APPLICATION_ID"):
    if not os.environ.get(var):
        raise SystemExit(f"ERROR: {var} is not set.")

# ── Wire up the logger ─────────────────────────────────────────────────────────
logger = MoesifLogger(
    identify_user=lambda kwargs, payload: "e2e-test-user",
    identify_company=lambda kwargs, payload: "e2e-test-company",
    debug=True,
)
litellm.callbacks = [logger]

# ── Make a real LLM call ───────────────────────────────────────────────────────
print("Making Gemini call via LiteLLM...")
try:
    response = litellm.completion(
        model="gemini/gemini-3.6-flash",
        messages=[{"role": "user", "content": "WHo are u"}],
        user="e2e-test-user",
    )
    print(f"Response: {response.choices[0].message.content}")
    print(f"Tokens:   prompt={response.usage.prompt_tokens}  completion={response.usage.completion_tokens}")
except Exception as e:
    print(f"LLM call failed ({type(e).__name__}: {e})")
    print("Continuing to flush the failure event to Moesif...")

# ── Give the background flush task time to send ────────────────────────────────
print("\nWaiting for background flush (3 s)...")


async def _flush():
    await asyncio.sleep(3)
    # Force an immediate flush in case the interval hasn't fired yet
    async with logger.flush_lock:
        await logger.async_send_batch()

asyncio.run(_flush())

print("\nDone. Check your Moesif dashboard — you should see one event with:")
print("  user_id    = e2e-test-user")
print("  company_id = e2e-test-company")
print("  model      = gemini/gemini-2.0-flash")
print("  status     = 200")
