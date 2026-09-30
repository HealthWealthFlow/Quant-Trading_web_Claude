from pathlib import Path

import pytest
from pydantic import ValidationError

from qsd.config import DEFAULT_CONFIG, get_secret, load_settings

MISSING = Path("/nonexistent/local.yaml")


def test_default_config_loads():
    s = load_settings(DEFAULT_CONFIG, MISSING, environ={})
    assert s.ai.default_provider == "deepseek"
    assert set(s.asset_classes.enabled) == {"STOCK", "ETF", "OPTIONS", "FOREX", "CRYPTO"}
    assert s.budgets.max_ai_cost_usd_per_campaign > 0


def test_env_override_nested():
    s = load_settings(DEFAULT_CONFIG, MISSING, environ={"QSD_BUDGETS__MAX_AI_CALLS_PER_CAMPAIGN": "7"})
    assert s.budgets.max_ai_calls_per_campaign == 7


def test_local_yaml_overrides_default(tmp_path):
    local = tmp_path / "local.yaml"
    local.write_text("crawling:\n  requests_per_minute_per_domain: 3\n", encoding="utf-8")
    s = load_settings(DEFAULT_CONFIG, local, environ={})
    assert s.crawling.requests_per_minute_per_domain == 3
    assert s.crawling.max_retries == 3  # untouched keys keep defaults


def test_robots_cannot_be_disabled():
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, MISSING, environ={"QSD_CRAWLING__RESPECT_ROBOTS_TXT": "false"})


def test_unknown_asset_class_rejected(tmp_path):
    local = tmp_path / "local.yaml"
    local.write_text("asset_classes:\n  enabled: [STOCK, BEANIE_BABIES]\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, local, environ={})


def test_exploration_must_sum_to_one():
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, MISSING, environ={"QSD_EXPLORATION__EXPERIMENTAL": "0.5"})


def test_negative_budget_rejected():
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, MISSING, environ={"QSD_BUDGETS__MAX_AI_COST_USD_PER_DAY": "-1"})


def test_secrets_only_from_env():
    assert get_secret("DEEPSEEK_API_KEY", environ={}) is None
    assert get_secret("DEEPSEEK_API_KEY", environ={"DEEPSEEK_API_KEY": "  abc "}) == "abc"
