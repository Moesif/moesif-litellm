import pytest
from moesif_litellm.governance import GovernanceRulesManager


def _make_mgr_with_rule(rule):
    mgr = GovernanceRulesManager(
        application_id="test-app-id",
        base_url="https://api.moesif.net",
        user_agent="test",
    )
    mgr._rules = [rule]
    mgr._fetched_once = True
    return mgr


REGEX_RULE = {
    "_id": "rule-regex-1",
    "type": "regex",
    "block": True,
    "response": {"status": 403, "headers": {}, "body": {"error": "blocked"}},
    "regex_config": [
        {
            "conditions": [
                {"path": "request.route", "value": "/v1/chat/completions/gemini-flash"}
            ]
        }
    ],
}


class TestRegexRuleMatching:
    def test_blocks_matching_route(self):
        mgr = _make_mgr_with_rule(REGEX_RULE)
        exc = mgr.check_request_blocked(
            user_id=None,
            company_id=None,
            request_verb="POST",
            request_route="/v1/chat/completions/gemini-flash",
        )
        assert exc is not None
        assert exc.rule_id == "rule-regex-1"
        assert exc.status == 403

    def test_does_not_block_different_route(self):
        mgr = _make_mgr_with_rule(REGEX_RULE)
        exc = mgr.check_request_blocked(
            user_id=None,
            company_id=None,
            request_verb="POST",
            request_route="/v1/chat/completions/gpt-4o",
        )
        assert exc is None

    def test_regex_pattern_matches_partial(self):
        rule = {**REGEX_RULE, "regex_config": [{"conditions": [{"path": "request.route", "value": "gemini"}]}]}
        mgr = _make_mgr_with_rule(rule)
        exc = mgr.check_request_blocked(
            user_id=None, company_id=None,
            request_verb="POST",
            request_route="/v1/chat/completions/gemini-flash",
        )
        assert exc is not None

    def test_verb_condition_matches(self):
        rule = {**REGEX_RULE, "regex_config": [{"conditions": [{"path": "request.verb", "value": "POST"}]}]}
        mgr = _make_mgr_with_rule(rule)
        exc = mgr.check_request_blocked(
            user_id=None, company_id=None,
            request_verb="POST",
            request_route="/v1/chat/completions/gemini-flash",
        )
        assert exc is not None

    def test_verb_condition_no_match(self):
        rule = {**REGEX_RULE, "regex_config": [{"conditions": [{"path": "request.verb", "value": "GET"}]}]}
        mgr = _make_mgr_with_rule(rule)
        exc = mgr.check_request_blocked(
            user_id=None, company_id=None,
            request_verb="POST",
            request_route="/v1/chat/completions/gemini-flash",
        )
        assert exc is None

    def test_multiple_conditions_all_must_match(self):
        rule = {
            **REGEX_RULE,
            "regex_config": [{"conditions": [
                {"path": "request.verb", "value": "POST"},
                {"path": "request.route", "value": "gemini"},
            ]}]
        }
        mgr = _make_mgr_with_rule(rule)
        # Both match
        assert mgr.check_request_blocked(None, None, "POST", "/v1/chat/completions/gemini-flash") is not None
        # Only route matches, verb doesn't
        assert mgr.check_request_blocked(None, None, "GET", "/v1/chat/completions/gemini-flash") is None

    def test_no_conditions_always_matches(self):
        rule = {**REGEX_RULE, "regex_config": [{"conditions": []}]}
        mgr = _make_mgr_with_rule(rule)
        exc = mgr.check_request_blocked(None, None, "POST", "/anything")
        assert exc is not None

    def test_non_blocking_regex_rule_ignored(self):
        rule = {**REGEX_RULE, "block": False}
        mgr = _make_mgr_with_rule(rule)
        exc = mgr.check_request_blocked(None, None, "POST", "/v1/chat/completions/gemini-flash")
        assert exc is None