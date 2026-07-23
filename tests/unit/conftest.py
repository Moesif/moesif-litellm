import time
import pytest
from moesif_litellm.config import MoesifConfig


@pytest.fixture
def config():
    return MoesifConfig(application_id="test-app-id")


@pytest.fixture
def base_payload():
    """Minimal StandardLoggingPayload-shaped dict."""
    now = time.time()
    return {
        "id": "req-abc123",
        "litellm_call_id": "call-abc123",
        "call_type": "completion",
        "model": "gpt-4o",
        "custom_llm_provider": "openai",
        "api_base": "https://api.openai.com",
        "status": "success",
        "messages": [{"role": "user", "content": "Hello"}],
        "response": {
            "id": "chatcmpl-xyz",
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}, "finish_reason": "stop"}],
        },
        "model_parameters": {"temperature": 0.7, "max_tokens": 100},
        "total_tokens": 20,
        "prompt_tokens": 10,
        "completion_tokens": 10,
        "response_cost": 0.0002,
        "startTime": now,
        "endTime": now + 0.5,
        "metadata": {
            "user_api_key_end_user_id": None,
            "user_api_key_user_id": None,
            "user_api_key_team_id": None,
            "requester_ip_address": "1.2.3.4",
            "requester_custom_headers": {},
        },
        "end_user": None,
        "error_str": None,
        "error_information": None,
        "cache_hit": False,
        "model_id": None,
        "model_group": None,
    }


@pytest.fixture
def base_kwargs(base_payload):
    return {
        "standard_logging_object": base_payload,
        "user": None,
        "litellm_params": {"metadata": {}},
    }
