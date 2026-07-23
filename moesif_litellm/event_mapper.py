import logging
import random
from typing import Optional

from moesif_litellm.config import MoesifConfig
from moesif_litellm.user_resolver import resolve_company_id, resolve_user_id
from moesif_litellm.utils import (
    format_iso8601,
    map_error_to_http_status,
    mask_body,
    truncate_body,
)

_CALL_TYPE_PATH_MAP = {
    "completion": "/v1/chat/completions",
    "acompletion": "/v1/chat/completions",
    "embedding": "/v1/embeddings",
    "aembedding": "/v1/embeddings",
    "text_completion": "/v1/completions",
    "atext_completion": "/v1/completions",
    "image_generation": "/v1/images/generations",
    "aimage_generation": "/v1/images/generations",
    "transcription": "/v1/audio/transcriptions",
    "atranscription": "/v1/audio/transcriptions",
    "speech": "/v1/audio/speech",
    "aspeech": "/v1/audio/speech",
}


def build_moesif_event(
    kwargs: dict,
    response_obj,
    start_time,
    end_time,
    config: MoesifConfig,
    *,
    is_error: bool = False,
) -> Optional[dict]:
    """
    Map a LiteLLM callback invocation to a Moesif event dict.
    Returns None if the event should be dropped (sampling or skip_event hook).
    """
    payload: dict = kwargs.get("standard_logging_object") or {}

    # ── Sampling ──────────────────────────────────────────────────────────────
    if config.sample_rate < 100:
        if random.randint(0, 99) >= config.sample_rate:
            return None
        weight = max(1, round(100 / config.sample_rate))
    else:
        weight = 1

    # ── Request ───────────────────────────────────────────────────────────────
    req_body = _build_request_body(payload, config)
    req_headers = _build_request_headers(payload, config)

    request_event = {
        "uri": _construct_uri(payload),
        "verb": "POST",
        "time": format_iso8601(_to_float(start_time)),
        "headers": req_headers,
        "body": req_body,
        "transfer_encoding": "json" if req_body is not None else None,
    }
    ip = (payload.get("metadata") or {}).get("requester_ip_address")
    if ip:
        request_event["ip_address"] = ip

    # ── Response ──────────────────────────────────────────────────────────────
    resp_body = _build_response_body(payload, config, is_error=is_error)
    http_status = _resolve_http_status(payload, is_error=is_error)

    response_event = {
        "time": format_iso8601(_to_float(end_time)),
        "status": http_status,
        "headers": {"content-type": "application/json"},
        "body": resp_body,
        "transfer_encoding": "json" if resp_body is not None else None,
    }

    # ── Identity ──────────────────────────────────────────────────────────────
    user_id = resolve_user_id(kwargs, payload, config)
    company_id = resolve_company_id(kwargs, payload, config)

    if config.debug:
        meta = payload.get("metadata") or {}
        print(
            f"[Moesif] identity: user_id={user_id!r} company_id={company_id!r} | "
            f"kwargs.user={kwargs.get('user')!r} end_user={payload.get('end_user')!r}",
            flush=True,
        )
        print(
            f"[Moesif] company sources: "
            f"team_id={meta.get('user_api_key_team_id')!r} "
            f"requester_meta_company={( meta.get('requester_metadata') or {}).get('company_id')!r} "
            f"kwargs_meta_company={(kwargs.get('metadata') or {}).get('company_id')!r}",
            flush=True,
        )

    # ── Metadata ──────────────────────────────────────────────────────────────
    metadata = {
        "litellm": {
            k: payload.get(k)
            for k in (
                "call_type",
                "model",
                "custom_llm_provider",
                "response_cost",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "litellm_call_id",
                "cache_hit",
                "model_id",
                "model_group",
            )
        }
    }

    event = {
        "request": request_event,
        "response": response_event,
        "user_id": user_id,
        "company_id": company_id,
        "direction": "Outgoing",
        "weight": weight,
        "session_token": _extract_session_token(kwargs, payload),
        "metadata": metadata,
        "transaction_id": payload.get("litellm_call_id"),
    }

    # ── Hooks ─────────────────────────────────────────────────────────────────
    if config.skip_event:
        try:
            if config.skip_event(kwargs, event):
                return None
        except Exception:
            pass

    if config.mask_event_model:
        try:
            event = config.mask_event_model(kwargs, event)
        except Exception:
            pass

    return event


# ── Helpers ───────────────────────────────────────────────────────────────────

def _construct_uri(payload: dict) -> str:
    api_base = (payload.get("api_base") or "").rstrip("/")
    call_type = payload.get("call_type") or "completion"
    path = _CALL_TYPE_PATH_MAP.get(call_type, "/v1/chat/completions")
    return f"{api_base}{path}" if api_base else f"https://api.openai.com{path}"


def _build_request_body(payload: dict, config: MoesifConfig):
    if not config.capture_request_body:
        return None
    body = {
        "model": payload.get("model"),
        "messages": payload.get("messages"),
        **(payload.get("model_parameters") or {}),
    }
    body = mask_body(body, config.request_body_masks)
    return truncate_body(body, config.request_max_body_size)


def _build_request_headers(payload: dict, config: MoesifConfig) -> dict:
    headers = {"content-type": "application/json"}
    custom = (payload.get("metadata") or {}).get("requester_custom_headers") or {}
    headers.update({k.lower(): v for k, v in custom.items()})
    for h in config.request_header_masks:
        headers.pop(h.lower(), None)
    return headers


def _build_response_body(payload: dict, config: MoesifConfig, *, is_error: bool):
    if is_error:
        return {"error": payload.get("error_str")}
    if not config.capture_response_body:
        return None
    resp = payload.get("response") or {}
    if isinstance(resp, dict):
        resp = mask_body(dict(resp), config.response_body_masks)
    return truncate_body(resp, config.response_max_body_size)


def _resolve_http_status(payload: dict, *, is_error: bool) -> int:
    if not is_error:
        return 200
    error_class = (payload.get("error_information") or {}).get("error_class", "")
    return map_error_to_http_status(error_class)


def _extract_session_token(kwargs: dict, payload: dict):
    custom = (payload.get("metadata") or {}).get("requester_custom_headers") or {}
    auth = custom.get("authorization") or custom.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _to_float(ts) -> float:
    """Accept datetime, float, or int timestamps."""
    if isinstance(ts, float):
        return ts
    if isinstance(ts, int):
        return float(ts)
    try:
        import datetime as dt
        if isinstance(ts, dt.datetime):
            return ts.timestamp()
    except Exception:
        pass
    return float(ts)
