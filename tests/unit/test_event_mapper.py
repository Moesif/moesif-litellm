import time
import pytest
from moesif_litellm.config import MoesifConfig
from moesif_litellm.event_mapper import build_moesif_event


def _build(kwargs, config, *, is_error=False):
    return build_moesif_event(kwargs, None, time.time(), time.time() + 0.5, config, is_error=is_error)


class TestSuccessEvent:
    def test_returns_dict(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        assert isinstance(event, dict)

    def test_request_fields(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        req = event["request"]
        assert req["verb"] == "POST"
        assert req["uri"] == "https://api.openai.com/v1/chat/completions"
        assert req["headers"]["content-type"] == "application/json"
        assert req["body"]["model"] == "gpt-4o"
        assert req["body"]["messages"] == [{"role": "user", "content": "Hello"}]

    def test_response_fields(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        resp = event["response"]
        assert resp["status"] == 200
        assert "choices" in resp["body"]

    def test_ip_address_captured(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        assert event["request"]["ip_address"] == "1.2.3.4"

    def test_direction_outgoing(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        assert event["direction"] == "Outgoing"

    def test_weight_is_1_at_full_sample(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        assert event["weight"] == 1

    def test_transaction_id(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        assert event["transaction_id"] == "call-abc123"

    def test_metadata_contains_cost_and_tokens(self, base_kwargs, config):
        event = _build(base_kwargs, config)
        meta = event["metadata"]["litellm"]
        assert meta["response_cost"] == 0.0002
        assert meta["total_tokens"] == 20

    def test_uri_uses_api_base(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["api_base"] = "https://my-proxy.example.com"
        event = _build(base_kwargs, config)
        assert event["request"]["uri"] == "https://my-proxy.example.com/v1/chat/completions"

    def test_uri_fallback_when_no_api_base(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["api_base"] = ""
        event = _build(base_kwargs, config)
        assert "api.openai.com" in event["request"]["uri"]

    def test_embedding_call_type(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["call_type"] = "embedding"
        event = _build(base_kwargs, config)
        assert event["request"]["uri"].endswith("/v1/embeddings")


class TestFailureEvent:
    def test_status_500_for_generic_error(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["error_information"] = {"error_class": "UnknownError"}
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["status"] == 500

    def test_status_429_for_rate_limit(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["error_information"] = {"error_class": "RateLimitError"}
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["status"] == 429

    def test_status_401_for_auth_error(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["error_information"] = {"error_class": "AuthenticationError"}
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["status"] == 401

    def test_error_body_contains_error_str(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["error_str"] = "Something went wrong"
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["body"]["error"] == "Something went wrong"


class TestBodyCapture:
    def test_request_body_omitted_when_disabled(self, base_kwargs, config):
        config.capture_request_body = False
        event = _build(base_kwargs, config)
        assert event["request"]["body"] is None

    def test_response_body_omitted_when_disabled(self, base_kwargs, config):
        config.capture_response_body = False
        event = _build(base_kwargs, config)
        assert event["response"]["body"] is None

    def test_request_body_masking(self, base_kwargs, config):
        config.request_body_masks = ["messages"]
        event = _build(base_kwargs, config)
        assert event["request"]["body"]["messages"] is None

    def test_response_body_masking(self, base_kwargs, config):
        config.response_body_masks = ["choices"]
        event = _build(base_kwargs, config)
        assert event["response"]["body"]["choices"] is None

    def test_request_body_truncated_when_over_limit(self, base_kwargs, config):
        config.request_max_body_size = 10
        event = _build(base_kwargs, config)
        assert event["request"]["body"] is None

    def test_response_body_truncated_when_over_limit(self, base_kwargs, config):
        config.response_max_body_size = 10
        event = _build(base_kwargs, config)
        assert event["response"]["body"] is None

    def test_request_header_masking(self, base_kwargs, config):
        config.request_header_masks = ["x-secret"]
        base_kwargs["standard_logging_object"]["metadata"]["requester_custom_headers"] = {
            "x-secret": "hidden", "x-ok": "visible"
        }
        event = _build(base_kwargs, config)
        assert "x-secret" not in event["request"]["headers"]
        assert event["request"]["headers"]["x-ok"] == "visible"


class TestSampling:
    def test_sample_rate_100_always_passes(self, base_kwargs, config):
        config.sample_rate = 100
        results = [_build(base_kwargs, config) for _ in range(20)]
        assert all(r is not None for r in results)

    def test_sample_rate_0_always_drops(self, base_kwargs, config):
        config.sample_rate = 0
        results = [_build(base_kwargs, config) for _ in range(20)]
        assert all(r is None for r in results)

    def test_weight_reflects_sample_rate(self, base_kwargs, config):
        config.sample_rate = 50
        # Run many times; when an event is returned its weight must be 2
        for _ in range(100):
            event = _build(base_kwargs, config)
            if event is not None:
                assert event["weight"] == 2
                return
        pytest.skip("Statistically unlikely: no events sampled in 100 tries at 50%")


class TestHooks:
    def test_skip_event_drops_event(self, base_kwargs, config):
        config.skip_event = lambda k, e: True
        assert _build(base_kwargs, config) is None

    def test_skip_event_keeps_event_when_false(self, base_kwargs, config):
        config.skip_event = lambda k, e: False
        assert _build(base_kwargs, config) is not None

    def test_mask_event_model_transforms_event(self, base_kwargs, config):
        def mask(k, e):
            e["user_id"] = "masked"
            return e
        config.mask_event_model = mask
        event = _build(base_kwargs, config)
        assert event["user_id"] == "masked"

    def test_skip_event_exception_keeps_event(self, base_kwargs, config):
        def bad_skip(k, e):
            raise RuntimeError("oops")
        config.skip_event = bad_skip
        assert _build(base_kwargs, config) is not None

    def test_mask_event_model_exception_keeps_original(self, base_kwargs, config):
        def bad_mask(k, e):
            raise RuntimeError("oops")
        config.mask_event_model = bad_mask
        event = _build(base_kwargs, config)
        assert event is not None

    def test_mask_event_model_returning_none_keeps_original(self, base_kwargs, config):
        config.mask_event_model = lambda k, e: None
        event = _build(base_kwargs, config)
        assert event is not None
        assert event["direction"] == "Outgoing"


class TestErrorBodyMasking:
    def test_response_body_masks_applied_to_error_body(self, base_kwargs, config):
        config.response_body_masks = ["error"]
        base_kwargs["standard_logging_object"]["error_str"] = "sensitive error message"
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["body"]["error"] is None

    def test_error_body_captured_without_masks(self, base_kwargs, config):
        base_kwargs["standard_logging_object"]["error_str"] = "rate limit exceeded"
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["body"]["error"] == "rate limit exceeded"

    def test_error_body_captured_even_when_capture_response_body_false(self, base_kwargs, config):
        config.capture_response_body = False
        base_kwargs["standard_logging_object"]["error_str"] = "some error"
        event = _build(base_kwargs, config, is_error=True)
        assert event["response"]["body"]["error"] == "some error"
