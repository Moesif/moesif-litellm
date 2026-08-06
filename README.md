# moesif-litellm

Moesif observability plugin for [LiteLLM](https://github.com/BerriAI/litellm). Captures every LLM request and response and forwards it to [Moesif](https://www.moesif.com) for API analytics, cost tracking, and monetization.

Works in both **SDK mode** (direct `litellm.completion()` calls) and **proxy mode** (LiteLLM proxy server).

---

## Installation

```bash
pip install moesif-litellm
```

---

## SDK Mode

Call LiteLLM directly in your Python code and attach the logger as a callback.

### Basic setup

```python
import litellm
from moesif_litellm import MoesifHandler

litellm.callbacks = [MoesifHandler()]

response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    user="alice",                          # user_id in Moesif
    metadata={"company_id": "acme-corp"},  # company_id in Moesif
)
```

Set your Moesif application ID via environment variable:

```bash
export MOESIF_APPLICATION_ID=your-moesif-app-id
```

Or pass it directly:

```python
litellm.callbacks = [MoesifHandler(application_id="your-moesif-app-id")]
```

### User and company identity

`user` is a standard LiteLLM/OpenAI field — LiteLLM carries it natively. `metadata.company_id` is LiteLLM's escape hatch for custom fields. Both are picked up automatically by the plugin with no extra config.

```python
litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    user="alice",                          # → user_id in Moesif
    metadata={"company_id": "acme-corp"},  # → company_id in Moesif
)
```

For full control, use the `identify_user` and `identify_company` callbacks. Useful when identity lives in your app's auth context rather than the LLM call itself:

```python
litellm.callbacks = [
    MoesifHandler(
        identify_user=lambda kwargs, payload: current_user.id,
        identify_company=lambda kwargs, payload: current_user.company_id,
    )
]
```

See the full example: [`examples/sdk/basic_usage.py`](examples/sdk/basic_usage.py)

---

## Proxy Mode

Run LiteLLM as a proxy server and route traffic through it. The plugin hooks in via a callback shim loaded by the proxy.

### 1. Create the callback shim

Create `moesif_callback.py` in the same directory as your proxy config:

```python
# moesif_callback.py
from moesif_litellm import MoesifHandler

moesif_logger = MoesifHandler()
```

### 2. Create the proxy config

```yaml
# proxy_config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

litellm_settings:
  callbacks: ["moesif_callback.moesif_logger"]
```

### 3. Start the proxy

```bash
export MOESIF_APPLICATION_ID=your-moesif-app-id
cd examples/proxy
litellm --config proxy_config.yaml --port 4000
```

### 4. Make requests

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "user": "alice",
    "metadata": {"company_id": "acme-corp"}
  }'
```

See the full example: [`examples/proxy/`](examples/proxy/)

### Identity in proxy mode — virtual keys and teams (recommended)

The cleanest way to identify users and companies in proxy mode is via **LiteLLM virtual keys assigned to teams**. Each team maps to a company in Moesif — no `metadata` field needed in the request body.

**Setup (requires a database):**

```yaml
# proxy_config.yaml
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

```bash
# Create a team (= company in Moesif)
curl http://localhost:4000/team/new \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id": "acme-corp", "team_alias": "Acme Corporation"}'

# Generate a virtual key for that team
curl http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id": "acme-corp"}'
# → returns "key": "sk-xxxx"
```

Now requests using `sk-xxxx` automatically get `company_id="acme-corp"` in Moesif — no `metadata` needed:

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-xxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "user": "alice"
  }'
```

---

## Identity resolution

The plugin resolves `user_id` and `company_id` automatically from multiple sources. First non-`None` value wins.

### user_id priority

| Priority | Source | When available |
|---|---|---|
| 1 | `identify_user` callback | Always (if configured) |
| 2 | `user_api_key_end_user_id` | Proxy: from virtual key auth |
| 3 | `user=` kwarg | SDK and proxy: passed in the request |
| 4 | `end_user` in logging payload | Proxy: resolved by LiteLLM |
| 5 | `user_api_key_user_id` | Proxy: API key owner |
| 6 | `metadata.user_id` | SDK and proxy: passed in metadata |
| 7 | JWT `authorization_user_id_field` claim | When auth header present |

### company_id priority

| Priority | Source | When available |
|---|---|---|
| 1 | `identify_company` callback | Always (if configured) |
| 2 | `user_api_key_team_id` | Proxy: from virtual key team |
| 3 | `requester_metadata.company_id` | Proxy: from request body metadata |
| 4 | `metadata.company_id` | SDK and proxy: passed in metadata |
| 5 | JWT `authorization_company_id_field` claim | When auth header present |

### JWT-based identity

If your users authenticate with a JWT (e.g. Auth0, Okta), configure which claims to use:

```python
MoesifHandler(
    authorization_user_id_field="sub",      # default
    authorization_company_id_field="org_id",
)
```

The plugin decodes the `Authorization: Bearer <token>` header automatically — no secret key needed (claims only, no signature verification).

---

## Configuration reference

All options are passed as keyword arguments to `MoesifHandler`:

| Parameter | Default | Description |
|---|---|---|
| `application_id` | `$MOESIF_APPLICATION_ID` | Moesif Application ID (required) |
| `batch_size` | `100` | Events per flush |
| `flush_interval` | `2` | Seconds between periodic flushes |
| `max_queue_size` | `50000` | Max in-memory events before dropping oldest |
| `capture_request_body` | `True` | Log the LLM request body |
| `capture_response_body` | `True` | Log the LLM response body |
| `request_max_body_size` | `100000` | Max request body bytes (omit if exceeded) |
| `response_max_body_size` | `100000` | Max response body bytes (omit if exceeded) |
| `request_body_masks` | `[]` | Top-level request body keys to null out |
| `response_body_masks` | `[]` | Top-level response body keys to null out |
| `request_header_masks` | `[]` | Request header names to remove |
| `response_header_masks` | `[]` | Response header names to remove |
| `identify_user` | `None` | `(kwargs, payload) -> str` callback |
| `identify_company` | `None` | `(kwargs, payload) -> str` callback |
| `authorization_user_id_field` | `"sub"` | JWT claim to use as user ID |
| `authorization_company_id_field` | `None` | JWT claim to use as company ID |
| `skip_event` | `None` | `(kwargs, event) -> bool` — return `True` to drop |
| `mask_event_model` | `None` | `(kwargs, event) -> event` — mutate before send |
| `sample_rate` | `100` | Integer 0–100; percentage of events to capture |
| `moesif_base_url` | `https://api.moesif.net` | Override for testing |
| `debug` | `False` | Print identity and routing debug info |

---

## Masking sensitive data

```python
MoesifHandler(
    request_body_masks=["messages"],         # null out request messages
    response_body_masks=["choices"],         # null out response choices
    request_header_masks=["authorization"],  # strip auth header
)
```

---

## Filtering events

```python
# Only log errors, skip successful requests
MoesifHandler(
    skip_event=lambda kwargs, event: event["response"]["status"] == 200,
)
```

---

## Sampling

```python
# Capture 10% of traffic
MoesifHandler(sample_rate=10)
```

Events that pass sampling get a `weight` of `100 / sample_rate` so Moesif can extrapolate totals correctly.

---

## How it works

`MoesifHandler` extends LiteLLM's `CustomBatchLogger`. On each request it builds a Moesif event from `StandardLoggingPayload` and appends it to an in-memory queue. A background `asyncio` task flushes the queue to `POST /v1/events/batch` every `flush_interval` seconds, or immediately when `batch_size` is reached. The proxied request is never blocked. Failed batches are re-queued automatically.

### Sync vs async flush behaviour

| Mode | How triggered | Flush timing |
|---|---|---|
| Sync (`litellm.completion`) | `log_success_event` | Immediately per event via blocking `httpx` |
| Async (`litellm.acompletion` / proxy) | `async_log_success_event` | Every `flush_interval` seconds or when `batch_size` reached |

In sync mode every event is sent to Moesif immediately after the LLM call returns. In async mode events are batched and flushed on a background timer — no blocking of the calling coroutine.


---

## Running tests

```bash
# Unit tests (no API keys needed)
pytest

# End-to-end smoke test (requires real API keys)
export GEMINI_API_KEY=...
export MOESIF_APPLICATION_ID=...
python tests/e2e/e2e_test.py
```
