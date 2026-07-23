import litellm
from moesif_litellm import MoesifLogger

litellm.callbacks = [
    MoesifLogger(
        identify_user=lambda kwargs, payload: (payload.get("metadata") or {}).get("user_api_key_end_user_id") or "proxy-test-user",
        identify_company=lambda kwargs, payload: (payload.get("metadata") or {}).get("user_api_key_team_id") or "proxy-test-company",
    )
]
