import base64
import json
import pytest
from moesif_litellm.config import MoesifConfig
from moesif_litellm.user_resolver import resolve_company_id, resolve_user_id


def _make_bearer(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"Bearer {header}.{body}.sig"


# ── user_id ────────────────────────────────────────────────────────────────────

class TestResolveUserId:
    def test_identify_user_callback_wins(self, config):
        config.identify_user = lambda k, p: "callback-user"
        payload = {"metadata": {"user_api_key_end_user_id": "other-user"}}
        assert resolve_user_id({}, payload, config) == "callback-user"

    def test_identify_user_callback_exception_falls_through(self, config):
        config.identify_user = lambda k, p: (_ for _ in ()).throw(RuntimeError("boom"))
        payload = {"metadata": {"user_api_key_end_user_id": "fallback-user"}}
        assert resolve_user_id({}, payload, config) == "fallback-user"

    def test_end_user_id_from_metadata(self, config):
        payload = {"metadata": {"user_api_key_end_user_id": "end-user-1"}}
        assert resolve_user_id({}, payload, config) == "end-user-1"

    def test_user_kwarg(self, config):
        payload = {"metadata": {}}
        assert resolve_user_id({"user": "kwarg-user"}, payload, config) == "kwarg-user"

    def test_end_user_from_payload(self, config):
        payload = {"metadata": {}, "end_user": "payload-end-user"}
        assert resolve_user_id({}, payload, config) == "payload-end-user"

    def test_api_key_owner(self, config):
        payload = {"metadata": {"user_api_key_user_id": "key-owner"}}
        assert resolve_user_id({}, payload, config) == "key-owner"

    def test_jwt_fallback(self, config):
        config.authorization_user_id_field = "sub"
        token = _make_bearer({"sub": "jwt-user"})
        payload = {"metadata": {"requester_custom_headers": {"authorization": token}}}
        assert resolve_user_id({}, payload, config) == "jwt-user"

    def test_jwt_custom_field(self, config):
        config.authorization_user_id_field = "user_id"
        token = _make_bearer({"user_id": "custom-field-user"})
        payload = {"metadata": {"requester_custom_headers": {"authorization": token}}}
        assert resolve_user_id({}, payload, config) == "custom-field-user"

    def test_returns_none_when_nothing_matches(self, config):
        assert resolve_user_id({}, {"metadata": {}}, config) is None

    def test_priority_order(self, config):
        # end_user_id should win over user kwarg
        payload = {"metadata": {"user_api_key_end_user_id": "end-user"}}
        assert resolve_user_id({"user": "kwarg-user"}, payload, config) == "end-user"


# ── company_id ────────────────────────────────────────────────────────────────

class TestResolveCompanyId:
    def test_identify_company_callback_wins(self, config):
        config.identify_company = lambda k, p: "callback-company"
        payload = {"metadata": {"user_api_key_team_id": "other-team"}}
        assert resolve_company_id({}, payload, config) == "callback-company"

    def test_team_id_from_metadata(self, config):
        payload = {"metadata": {"user_api_key_team_id": "team-123"}}
        assert resolve_company_id({}, payload, config) == "team-123"

    def test_jwt_company_field(self, config):
        config.authorization_company_id_field = "org_id"
        token = _make_bearer({"org_id": "jwt-company"})
        payload = {"metadata": {"requester_custom_headers": {"authorization": token}}}
        assert resolve_company_id({}, payload, config) == "jwt-company"

    def test_returns_none_when_nothing_matches(self, config):
        assert resolve_company_id({}, {"metadata": {}}, config) is None

    def test_no_jwt_lookup_without_field_configured(self, config):
        config.authorization_company_id_field = None
        token = _make_bearer({"org_id": "should-not-appear"})
        payload = {"metadata": {"requester_custom_headers": {"authorization": token}}}
        assert resolve_company_id({}, payload, config) is None


# ── Virtual key + team demo ───────────────────────────────────────────────────
#
# In production you create a LiteLLM virtual key assigned to a team:
#   POST /key/generate  {"team_id": "acme-corp"}  → returns "sk-xxx"
#
# When a request arrives using that key the proxy automatically sets
# user_api_key_team_id in StandardLoggingMetadata — no extra fields in the
# request body needed.  user_api_key_end_user_id is set from the "user" field
# in the request body.
#
# This test simulates exactly that proxy-populated payload.

class TestVirtualKeyTeamMapping:
    def _proxy_payload(self, team_id: str, end_user_id: str) -> dict:
        """Simulate StandardLoggingPayload as the LiteLLM proxy populates it."""
        return {
            "metadata": {
                "user_api_key_team_id": team_id,       # set by proxy from virtual key
                "user_api_key_end_user_id": end_user_id,  # set by proxy from request "user" field
                "user_api_key_user_id": "key-owner-456",
                "requester_custom_headers": {},
            }
        }

    def test_team_id_maps_to_company(self, config):
        payload = self._proxy_payload("acme-corp", "alice")
        assert resolve_company_id({}, payload, config) == "acme-corp"

    def test_end_user_maps_to_user(self, config):
        payload = self._proxy_payload("acme-corp", "alice")
        assert resolve_user_id({}, payload, config) == "alice"

    def test_both_resolved_together(self, config):
        payload = self._proxy_payload("globex-inc", "bob")
        assert resolve_user_id({}, payload, config) == "bob"
        assert resolve_company_id({}, payload, config) == "globex-inc"

    def test_team_wins_over_metadata_company_id(self, config):
        # team_id (from virtual key) has higher priority than metadata.company_id
        payload = self._proxy_payload("acme-corp", "alice")
        payload["metadata"]["requester_metadata"] = {"company_id": "other-company"}
        assert resolve_company_id({}, payload, config) == "acme-corp"

    def test_no_virtual_key_falls_back_to_metadata_company_id(self, config):
        # Without a team-assigned key, caller passes company_id in request metadata
        payload = {
            "metadata": {
                "user_api_key_team_id": None,
                "requester_metadata": {"company_id": "startup-xyz"},
                "requester_custom_headers": {},
            }
        }
        assert resolve_company_id({}, payload, config) == "startup-xyz"
