from typing import Optional

from moesif_litellm.config import MoesifConfig
from moesif_litellm.utils import decode_bearer_jwt


def resolve_user_id(kwargs: dict, payload: dict, config: MoesifConfig) -> Optional[str]:
    if config.identify_user:
        try:
            result = config.identify_user(kwargs, payload)
            if result is not None:
                return str(result)
        except Exception:
            pass

    metadata = payload.get("metadata") or {}

    val = metadata.get("user_api_key_end_user_id")
    if val:
        return str(val)

    val = kwargs.get("user")
    if val:
        return str(val)

    val = payload.get("end_user")
    if val:
        return str(val)

    val = metadata.get("user_api_key_user_id")
    if val:
        return str(val)

    val = (kwargs.get("metadata") or {}).get("user_id")
    if val:
        return str(val)

    auth = _extract_auth_header(kwargs, payload)
    if auth and config.authorization_user_id_field:
        claims = decode_bearer_jwt(auth)
        val = claims.get(config.authorization_user_id_field.lower())
        if val:
            return str(val)

    return None


def resolve_company_id(kwargs: dict, payload: dict, config: MoesifConfig) -> Optional[str]:
    if config.identify_company:
        try:
            result = config.identify_company(kwargs, payload)
            if result is not None:
                return str(result)
        except Exception:
            pass

    metadata = payload.get("metadata") or {}

    # team_id from proxy virtual key maps to company in Moesif
    val = metadata.get("user_api_key_team_id")
    if val:
        return str(val)

    val = (metadata.get("requester_metadata") or {}).get("company_id")
    if val:
        return str(val)

    val = (kwargs.get("metadata") or {}).get("company_id")
    if val:
        return str(val)

    val = ((kwargs.get("litellm_params") or {}).get("metadata") or {}).get("company_id")
    if val:
        return str(val)

    if config.authorization_company_id_field:
        auth = _extract_auth_header(kwargs, payload)
        if auth:
            claims = decode_bearer_jwt(auth)
            val = claims.get(config.authorization_company_id_field.lower())
            if val:
                return str(val)

    return None


def _extract_auth_header(kwargs: dict, payload: dict) -> Optional[str]:
    custom_hdrs = (payload.get("metadata") or {}).get("requester_custom_headers") or {}
    auth = custom_hdrs.get("authorization") or custom_hdrs.get("Authorization")
    if auth:
        return auth

    litellm_meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    auth = litellm_meta.get("authorization") or litellm_meta.get("Authorization")
    if auth:
        return auth

    return None
