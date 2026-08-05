import time
import pytest
from moesif_litellm.config import MoesifConfig
from moesif_litellm.event_mapper import build_moesif_event
from moesif_litellm.governance import GovernanceRulesManager


def _make_governance(user_rates=None, company_rates=None):
    mgr = GovernanceRulesManager(
        application_id="test-app-id",
        base_url="https://api.moesif.net",
        user_agent="test",
    )
    mgr._user_sample_rates = user_rates or {}
    mgr._company_sample_rates = company_rates or {}
    return mgr


def _build(kwargs, config, effective_sample_rate=None):
    return build_moesif_event(
        kwargs, None, time.time(), time.time() + 0.5,
        config, effective_sample_rate=effective_sample_rate,
    )


class TestGetEffectiveSampleRate:
    def test_returns_global_when_no_entity_rates(self):
        mgr = _make_governance()
        assert mgr.get_effective_sample_rate("alice", "acme", 80) == 80

    def test_user_rate_lower_than_global(self):
        mgr = _make_governance(user_rates={"alice": 10})
        assert mgr.get_effective_sample_rate("alice", None, 80) == 10

    def test_company_rate_lower_than_global(self):
        mgr = _make_governance(company_rates={"acme": 20})
        assert mgr.get_effective_sample_rate(None, "acme", 80) == 20

    def test_user_rate_higher_than_global_uses_global(self):
        mgr = _make_governance(user_rates={"alice": 100})
        assert mgr.get_effective_sample_rate("alice", None, 50) == 50

    def test_minimum_of_user_and_company(self):
        mgr = _make_governance(user_rates={"alice": 30}, company_rates={"acme": 10})
        assert mgr.get_effective_sample_rate("alice", "acme", 80) == 10

    def test_unknown_user_uses_global(self):
        mgr = _make_governance(user_rates={"bob": 10})
        assert mgr.get_effective_sample_rate("alice", None, 80) == 80

    def test_none_user_and_company_uses_global(self):
        mgr = _make_governance(user_rates={"alice": 10})
        assert mgr.get_effective_sample_rate(None, None, 80) == 80


class TestEffectiveSampleRateApplied:
    def test_rate_0_always_drops(self, base_kwargs, config):
        results = [_build(base_kwargs, config, effective_sample_rate=0) for _ in range(20)]
        assert all(r is None for r in results)

    def test_rate_100_always_keeps(self, base_kwargs, config):
        results = [_build(base_kwargs, config, effective_sample_rate=100) for _ in range(20)]
        assert all(r is not None for r in results)

    def test_weight_reflects_effective_rate(self, base_kwargs, config):
        for _ in range(100):
            event = _build(base_kwargs, config, effective_sample_rate=50)
            if event is not None:
                assert event["weight"] == 2
                return
        pytest.skip("No events sampled in 100 tries at 50%")

    def test_user_rate_0_drops_all_events(self, base_kwargs, config):
        mgr = _make_governance(user_rates={"alice": 0})
        base_kwargs["user"] = "alice"
        rate = mgr.get_effective_sample_rate("alice", None, config.sample_rate)
        results = [_build(base_kwargs, config, effective_sample_rate=rate) for _ in range(20)]
        assert all(r is None for r in results)