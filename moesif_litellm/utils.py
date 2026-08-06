import base64
import datetime
import json
from typing import Any, Dict, List, Optional

_ERROR_STATUS_MAP = {
    "RateLimitError": 429,
    "AuthenticationError": 401,
    "PermissionDeniedError": 403,
    "NotFoundError": 404,
    "BadRequestError": 400,
    "ContextWindowExceededError": 400,
    "ContentPolicyViolationError": 400,
    "ServiceUnavailableError": 503,
    "APIConnectionError": 503,
    "Timeout": 504,
}


def format_iso8601(ts: float) -> str:
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def decode_bearer_jwt(header_value: str) -> Dict:
    """
    Decode JWT payload from a Bearer token.
    Returns {} on any parse failure — callers must tolerate an empty dict.
    """
    try:
        token = header_value.strip()
        if token.lower().startswith("bearer "):
            token = token[7:]
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padding = 4 - len(parts[1]) % 4
        payload_b64 = parts[1] + ("=" * (padding % 4))
        decoded = base64.urlsafe_b64decode(payload_b64)
        claims = json.loads(decoded)
        # normalise keys to lowercase for case-insensitive field lookup
        return {k.lower(): v for k, v in claims.items()} if isinstance(claims, dict) else {}
    except Exception:
        return {}


def mask_body(body: Dict, masks: List[str]) -> Dict:
    for key in masks:
        if key in body:
            body[key] = None
    return body


def truncate_body(body: Any, max_bytes: int) -> Optional[Any]:
    """Return body if it fits within max_bytes when JSON-encoded, else None."""
    try:
        if len(json.dumps(body)) <= max_bytes:
            return body
    except (TypeError, ValueError):
        pass
    return None


def map_error_to_http_status(error_class: str) -> int:
    return _ERROR_STATUS_MAP.get(error_class, 500)
