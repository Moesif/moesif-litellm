# moesif-litellm

Moesif observability plugin for [LiteLLM](https://github.com/BerriAI/litellm). Captures every LLM request and response passing through LiteLLM and forwards it to [Moesif](https://www.moesif.com) for API analytics and monetization.

## Installation

```bash
pip install moesif-litellm
```

## Quick start

```python
import litellm
from moesif_litellm import MoesifLogger

litellm.callbacks = [MoesifLogger(application_id="your-moesif-app-id")]

response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

Or set the application ID via environment variable:

```bash
export MOESIF_APPLICATION_ID=your-moesif-app-id
```

```python
litellm.callbacks = [MoesifLogger()]
```

## User and company identification

Pass callbacks to resolve who made each request:

```python
litellm.callbacks = [
    MoesifLogger(
        application_id="your-moesif-app-id",
        identify_user=lambda kwargs, payload: payload.get("metadata", {}).get("user_api_key_end_user_id"),
        identify_company=lambda kwargs, payload: kwargs.get("metadata", {}).get("company_id"),
    )
]
```

Both callbacks receive `(kwargs, standard_logging_payload)` and must return a string or `None`.

When running as a LiteLLM proxy, `user_id` is resolved automatically from the following sources (first non-None wins):

1. `identify_user` callback
2. `metadata.user_api_key_end_user_id` (end user set on the proxy API key)
3. `user=` argument passed to `litellm.completion()`
4. `end_user` field in the logging payload
5. `metadata.user_api_key_user_id` (API key owner)
6. JWT `sub` claim from the `Authorization` header

## Configuration

All options are passed as keyword arguments to `MoesifLogger`:

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
| `skip_event` | `None` | `(kwargs, event) -> bool`; return `True` to drop |
| `mask_event_model` | `None` | `(kwargs, event) -> event`; mutate before send |
| `sample_rate` | `100` | Integer 0–100; events below threshold are dropped |
| `moesif_base_url` | `https://api.moesif.net` | Override for testing |
| `debug` | `False` | Enable verbose logging |

## Masking sensitive data

```python
MoesifLogger(
    application_id="...",
    request_body_masks=["messages"],        # null out request messages
    response_body_masks=["choices"],        # null out response choices
    request_header_masks=["authorization"], # strip auth headers
)
```

## Filtering events

```python
MoesifLogger(
    application_id="...",
    skip_event=lambda kwargs, event: event["response"]["status"] == 200,  # only log errors
)
```

## LiteLLM proxy (`config.yaml`)

Instantiate the logger in a startup hook and register it:

```python
# startup.py
import litellm
from moesif_litellm import MoesifLogger

litellm.callbacks = [MoesifLogger(application_id="your-moesif-app-id")]
```

```yaml
# config.yaml
general_settings:
  startup_script: startup.py
```

## How it works

`MoesifLogger` extends LiteLLM's `CustomBatchLogger`. On each request it builds a Moesif event from `StandardLoggingPayload` and appends it to an in-memory queue. A background `asyncio` task flushes the queue to `POST /v1/events/batch` every `flush_interval` seconds (or immediately when `batch_size` is reached). The proxied request is never blocked. Failed batches are re-queued automatically.
