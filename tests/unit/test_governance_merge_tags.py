import pytest
from moesif_litellm.governance import GovernanceRulesManager


def _make_mgr():
    mgr = GovernanceRulesManager(
        application_id="test-app-id",
        base_url="https://api.moesif.net",
        user_agent="test",
    )
    mgr._fetched_once = True
    return mgr


RULE = {
    "_id": "rule-merge-1",
    "type": "user",
    "block": True,
    "applied_to": "matching",
    "regex_config": [],
    "response": {
        "status": 429,
        "headers": {},
        "body": "You have exceeded your quota of {{0}} requests. Limit resets at {{1}}.",
    },
}


class TestMergeTags:
    def test_substitutes_values_in_body(self):
        mgr = _make_mgr()
        mgr._rules = [RULE]
        mgr._user_entity_rules = {
            "alice": [{"rules": "rule-merge-1", "values": {"0": "100", "1": "midnight"}}]
        }
        exc = mgr.check_request_blocked("alice", None, "POST", "/v1/chat/completions")
        assert exc is not None
        assert "100" in exc.body
        assert "midnight" in exc.body
        assert "{{0}}" not in exc.body
        assert "{{1}}" not in exc.body

    def test_no_substitution_when_body_is_dict(self):
        rule = {**RULE, "response": {"status": 429, "headers": {}, "body": {"error": "blocked"}}}
        mgr = _make_mgr()
        mgr._rules = [rule]
        mgr._user_entity_rules = {
            "alice": [{"rules": "rule-merge-1", "values": {"0": "100"}}]
        }
        exc = mgr.check_request_blocked("alice", None, "POST", "/v1/chat/completions")
        assert exc is not None
        assert isinstance(exc.body, dict)
        assert exc.body == {"error": "blocked"}

    def test_no_substitution_when_no_entity_entries(self):
        mgr = _make_mgr()
        mgr._rules = [RULE]
        mgr._user_entity_rules = {"alice": [{"rules": "rule-merge-1"}]}  # no values key
        exc = mgr.check_request_blocked("alice", None, "POST", "/v1/chat/completions")
        assert exc is not None
        assert "{{0}}" in exc.body
        assert "{{1}}" in exc.body

    def test_partial_substitution_when_only_some_keys_present(self):
        mgr = _make_mgr()
        mgr._rules = [RULE]
        mgr._user_entity_rules = {
            "alice": [{"rules": "rule-merge-1", "values": {"0": "50"}}]
        }
        exc = mgr.check_request_blocked("alice", None, "POST", "/v1/chat/completions")
        assert exc is not None
        assert "50" in exc.body
        assert "{{0}}" not in exc.body
        assert "{{1}}" in exc.body  # unreplaced

    def test_correct_entry_used_when_multiple_entries(self):
        # alice has two entries: one for a different rule and one for rule-merge-1
        # only the values from rule-merge-1 entry should be substituted
        mgr = _make_mgr()
        mgr._rules = [RULE]
        mgr._user_entity_rules = {
            "alice": [
                {"rules": "other-rule-id", "values": {"0": "999"}},
                {"rules": "rule-merge-1", "values": {"0": "42", "1": "noon"}},
            ]
        }
        exc = mgr.check_request_blocked("alice", None, "POST", "/v1/chat/completions")
        assert exc is not None
        assert "42" in exc.body
        assert "noon" in exc.body
        assert "999" not in exc.body