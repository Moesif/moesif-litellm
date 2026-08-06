"""
LiteLLM SDK mode — Moesif integration example.

Setup:
    pip install moesif-litellm
    export MOESIF_APPLICATION_ID=your-moesif-app-id
    export OPENAI_API_KEY=sk-...         # or any provider key
"""

import asyncio
import litellm
from moesif_litellm import MoesifHandler

litellm.callbacks = [MoesifHandler()]


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_example():
    response = litellm.completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
        user="alice",                           # maps to user_id in Moesif
        metadata={"company_id": "acme-corp"},   # maps to company_id in Moesif
    )
    print(response.choices[0].message.content)


# ── Async ─────────────────────────────────────────────────────────────────────

async def async_example():
    response = await litellm.acompletion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
        user="alice",                           # maps to user_id in Moesif
        metadata={"company_id": "acme-corp"},   # maps to company_id in Moesif
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    sync_example()
    asyncio.run(async_example())