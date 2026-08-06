# moesif-litellm

Moesif plugin for [LiteLLM](https://github.com/BerriAI/litellm). Works in both **SDK mode** and **proxy mode**.

## What it does

- **Logging**: captures every LLM request and response and sends to Moesif for analytics, cost tracking, and user insights
- **Governance**: enforces block rules configured in the Moesif dashboard; requests are stopped before they reach the LLM
- **Sampling**: controls what percentage of traffic is logged; sampled events are weighted so Moesif can extrapolate totals correctly
- **Identity resolution**: automatically resolves `user_id` and `company_id` from multiple sources (kwargs, virtual keys, JWT, callbacks)
- **Body masking**: redacts sensitive fields in request/response bodies before logging
- **Event filtering**: `skip_event` callback to drop specific events from being logged
- **Event mutation**: `mask_event_model` callback to transform the event before it is sent

## How it works

Events are queued in memory and flushed to `POST /v1/events/batch` in the background. Failed batches are re-queued automatically.

| | Default | Notes |
|---|---|---|
| Batch size | 100 events | Flush triggered early if queue hits this |
| Flush interval | 2s | Background timer |
| Governance rules refresh | 60s | Fetched from Moesif `/v1/rules` with ETag caching |
| Sampling | 100% | Set `sample_rate=10` to capture 10%; events get weight `100/rate` for extrapolation |

In **sync mode** (`litellm.completion`) each event is sent immediately via a blocking HTTP call. In **async mode** (`litellm.acompletion` / proxy) events are batched and flushed on the background timer.

---

## Installation

```bash
pip install moesif-litellm
```

---

## SDK Mode

Attach the handler directly to LiteLLM callbacks in your Python code.

```python
import litellm
from moesif_litellm import MoesifHandler

litellm.callbacks = [MoesifHandler()]

litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    user="alice",                          # → user_id in Moesif
    metadata={"company_id": "acme-corp"},  # → company_id in Moesif
)
```

Set your Moesif application ID:

```bash
export MOESIF_APPLICATION_ID=your-moesif-app-id
```

Or pass it directly:

```python
MoesifHandler(application_id="your-moesif-app-id")
```

For custom identity resolution:

```python
MoesifHandler(
    identify_user=lambda kwargs, payload: current_user.id,
    identify_company=lambda kwargs, payload: current_user.company_id,
)
```

See full example: [`examples/sdk/basic_usage.py`](examples/sdk/basic_usage.py)

---

## Proxy Mode

Run LiteLLM as a proxy server and load the plugin via a callback shim.

**1. Create `moesif_callback.py`** in the same directory as your proxy config:

```python
from moesif_litellm import MoesifHandler
moesif_handler = MoesifHandler()
```

**2. Reference it in `proxy_config.yaml`:**

```yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

litellm_settings:
  callbacks: ["moesif_callback.moesif_handler"]
```

**3. Start the proxy:**

```bash
export MOESIF_APPLICATION_ID=your-moesif-app-id
litellm --config proxy_config.yaml --port 4000
```

**4. Make requests:**

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello!"}], "user": "alice", "metadata": {"company_id": "acme-corp"}}'
```

### Virtual keys and teams (recommended for proxy)

Assign LiteLLM virtual keys to teams — the team ID maps to `company_id` in Moesif automatically, no `metadata` needed in every request.

```bash
# Create a team
curl http://localhost:4000/team/new \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_id": "acme-corp"}'

# Generate a virtual key for that team
curl http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_id": "acme-corp"}'
# → returns "key": "sk-xxxx"
```

Requests using `sk-xxxx` automatically get `company_id="acme-corp"` in Moesif.

See full examples: [`examples/proxy/`](examples/proxy/)

---

## Identity resolution

First non-`None` value wins.

### user_id

| Priority | Source |
|---|---|
| 1 | `identify_user` callback |
| 2 | `user_api_key_end_user_id` (proxy virtual key) |
| 3 | `user=` kwarg |
| 4 | `end_user` in logging payload |
| 5 | `user_api_key_user_id` (proxy key owner) |
| 6 | `metadata.user_id` |
| 7 | JWT claim (`authorization_user_id_field`) |

### company_id

| Priority | Source |
|---|---|
| 1 | `identify_company` callback |
| 2 | `user_api_key_team_id` (proxy virtual key team) |
| 3 | `requester_metadata.company_id` |
| 4 | `metadata.company_id` |
| 5 | JWT claim (`authorization_company_id_field`) |

### JWT-based identity

```python
MoesifHandler(
    authorization_user_id_field="sub",       # default
    authorization_company_id_field="org_id",
)
```

Decodes `Authorization: Bearer <token>` automatically — no secret key needed.

---

## Configuration reference

| Parameter | Default | Description |
|---|---|---|
| `application_id` | `$MOESIF_APPLICATION_ID` | Moesif Application ID (required) |
| `batch_size` | `100` | Events per flush |
| `flush_interval` | `2` | Seconds between flushes |
| `max_queue_size` | `50000` | Max in-memory events |
| `capture_request_body` | `True` | Log request body |
| `capture_response_body` | `True` | Log response body |
| `request_max_body_size` | `100000` | Max request body bytes |
| `response_max_body_size` | `100000` | Max response body bytes |
| `request_body_masks` | `[]` | Request body keys to null out |
| `response_body_masks` | `[]` | Response body keys to null out |
| `request_header_masks` | `[]` | Request headers to remove |
| `response_header_masks` | `[]` | Response headers to remove |
| `identify_user` | `None` | `(kwargs, payload) -> str` |
| `identify_company` | `None` | `(kwargs, payload) -> str` |
| `authorization_user_id_field` | `"sub"` | JWT claim for user ID |
| `authorization_company_id_field` | `None` | JWT claim for company ID |
| `skip_event` | `None` | `(kwargs, event) -> bool` — return `True` to drop |
| `mask_event_model` | `None` | `(kwargs, event) -> event` — mutate before send |
| `sample_rate` | `100` | 0–100 percentage of events to capture |
| `moesif_base_url` | `https://api.moesif.net` | Override Moesif endpoint |
| `debug` | `False` | Print identity resolution debug info |

---

## Masking sensitive data

```python
MoesifHandler(
    request_body_masks=["messages"],
    response_body_masks=["choices"],
    request_header_masks=["authorization"],
)
```

---

## Filtering events

```python
# Only log errors
MoesifHandler(skip_event=lambda kwargs, event: event["response"]["status"] == 200)
```

---

## Sampling

```python
MoesifHandler(sample_rate=10)  # capture 10% of traffic
```
