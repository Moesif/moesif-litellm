import base64
import json
import pytest
from moesif_litellm.utils import (
    decode_bearer_jwt,
    format_iso8601,
    map_error_to_http_status,
    mask_body,
    truncate_body,
)


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"Bearer {header}.{body}.fakesig"


class TestFormatIso8601:
    def test_basic(self):
        result = format_iso8601(0.0)
        assert result == "1970-01-01T00:00:00.000Z"

    def test_milliseconds(self):
        result = format_iso8601(1000.123)
        assert result.endswith(".123Z")

    def test_format(self):
        result = format_iso8601(1705312245.456)
        assert result.endswith("Z")
        assert "T" in result


class TestDecodeBeaterJwt:
    def test_valid_jwt(self):
        jwt = _make_jwt({"sub": "user-42", "org": "acme"})
        claims = decode_bearer_jwt(jwt)
        assert claims["sub"] == "user-42"
        assert claims["org"] == "acme"

    def test_keys_lowercased(self):
        jwt = _make_jwt({"Sub": "user-42", "OrgId": "acme"})
        claims = decode_bearer_jwt(jwt)
        assert "sub" in claims
        assert "orgid" in claims

    def test_missing_bearer_prefix(self):
        jwt = _make_jwt({"sub": "u1"})
        token_only = jwt[len("Bearer "):]
        claims = decode_bearer_jwt(token_only)
        assert claims["sub"] == "u1"

    def test_invalid_token_returns_empty(self):
        assert decode_bearer_jwt("not-a-jwt") == {}

    def test_malformed_base64_returns_empty(self):
        assert decode_bearer_jwt("Bearer a.!!!.c") == {}

    def test_empty_string_returns_empty(self):
        assert decode_bearer_jwt("") == {}


class TestMaskBody:
    def test_masks_listed_keys(self):
        body = {"password": "secret", "name": "Alice"}
        result = mask_body(body, ["password"])
        assert result["password"] is None
        assert result["name"] == "Alice"

    def test_missing_key_is_noop(self):
        body = {"name": "Alice"}
        result = mask_body(body, ["nonexistent"])
        assert result == {"name": "Alice"}

    def test_mutates_in_place(self):
        body = {"x": 1}
        returned = mask_body(body, ["x"])
        assert returned is body


class TestTruncateBody:
    def test_small_body_passes(self):
        body = {"key": "value"}
        assert truncate_body(body, 100_000) == body

    def test_oversized_body_returns_none(self):
        body = {"data": "x" * 200}
        assert truncate_body(body, 100) is None

    def test_exactly_at_limit_passes(self):
        body = {"k": "v"}
        encoded = json.dumps(body)
        assert truncate_body(body, len(encoded)) == body

    def test_non_serialisable_returns_none(self):
        assert truncate_body(object(), 1000) is None


class TestMapErrorToHttpStatus:
    @pytest.mark.parametrize("error_class,expected", [
        ("RateLimitError", 429),
        ("AuthenticationError", 401),
        ("PermissionDeniedError", 403),
        ("NotFoundError", 404),
        ("BadRequestError", 400),
        ("ContextWindowExceededError", 400),
        ("ContentPolicyViolationError", 400),
        ("ServiceUnavailableError", 503),
        ("APIConnectionError", 503),
        ("Timeout", 504),
        ("SomeUnknownError", 500),
        ("", 500),
    ])
    def test_mapping(self, error_class, expected):
        assert map_error_to_http_status(error_class) == expected
