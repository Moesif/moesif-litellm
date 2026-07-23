from typing import Optional

from moesif_litellm.config import MoesifConfig
from moesif_litellm.utils import decode_bearer_jwt


def resolve_user_id(kwargs: dict, payload: dict, config: MoesifConfig) -> Optional[str]:
    # 1. Custom callback
    if config.identify_user:
        try:
            result = config.identify_user(kwargs, payload)
            if result is not None:
                return str(result)
        except Exception:
            pass

    metadata = payload.get("metadata") or {}

    # 2. End user set by proxy (multi-tenant)
    val = metadata.get("user_api_key_end_user_id")
    if val:
        return str(val)

    # 3. user= arg passed to litellm.completion()
    val = kwargs.get("user")
    if val:
        return str(val)

    # 4. StandardLoggingPayload.end_user
    val = payload.get("end_user")
    if val:
        return str(val)

    # 5. API key owner
    val = metadata.get("user_api_key_user_id")
    if val:
        return str(val)

    # 6. user_id from raw kwargs metadata (proxy passes metadata dict through)
    val = (kwargs.get("metadata") or {}).get("user_id")
    if val:
        return str(val)

    # 7. JWT decode
    auth = _extract_auth_header(kwargs, payload)
    if auth and config.authorization_user_id_field:
        claims = decode_bearer_jwt(auth)
        val = claims.get(config.authorization_user_id_field.lower())
        if val:
            return str(val)

    return None


def resolve_company_id(kwargs: dict, payload: dict, config: MoesifConfig) -> Optional[str]:
    # 1. Custom callback
    if config.identify_company:
        try:
            result = config.identify_company(kwargs, payload)
            if result is not None:
                return str(result)
        except Exception:
            pass

    metadata = payload.get("metadata") or {}

    # 2. Team ID from proxy key (team = company in Moesif)
    val = metadata.get("user_api_key_team_id")
    if val:
        return str(val)

    # 3. company_id from requester_metadata (set by LiteLLM proxy from request body metadata)
    val = (metadata.get("requester_metadata") or {}).get("company_id")
    if val:
        return str(val)

    # 4. company_id from raw kwargs metadata (most direct proxy path)
    val = (kwargs.get("metadata") or {}).get("company_id")
    if val:
        return str(val)

    # 5. JWT claim
    if config.authorization_company_id_field:
        auth = _extract_auth_header(kwargs, payload)
        if auth:
            claims = decode_bearer_jwt(auth)
            val = claims.get(config.authorization_company_id_field.lower())
            if val:
                return str(val)

    return None


def _extract_auth_header(kwargs: dict, payload: dict) -> Optional[str]:
    """Best-effort extraction of the Authorization header from available sources."""
    # From requester custom headers (proxy mode)
    custom_hdrs = (payload.get("metadata") or {}).get("requester_custom_headers") or {}
    auth = custom_hdrs.get("authorization") or custom_hdrs.get("Authorization")
    if auth:
        return auth

    # From litellm_params metadata (SDK mode)
    litellm_meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    auth = litellm_meta.get("authorization") or litellm_meta.get("Authorization")
    if auth:
        return auth

    return None
