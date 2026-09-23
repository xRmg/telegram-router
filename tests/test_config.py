import pytest

from telegram_proxy.config import Config, ConfigError

BASE = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_CHAT_ID": "42", "LLM_API_KEY": "k"}


def test_defaults():
    config = Config.from_env(BASE)
    assert config.redis_url == "redis://redis:6379/0"
    assert config.llm_base_url == "https://openrouter.ai/api/v1"
    assert config.llm_model == "openrouter/auto"
    assert config.structured_decision_model == ""
    assert config.routing_strategy == "model"
    assert not config.structured_decision_enabled
    assert not config.decision_extraction_enabled
    assert config.chat_model_enabled
    assert config.routing_confidence_threshold == 0.6
    assert config.reply_timeout_seconds == 10.0
    assert config.llm_rate_limit_per_minute == 20
    assert config.notifier_rate_limit_per_minute == 10
    assert config.service_display_names == {}


def test_overrides():
    env = {
        **BASE,
        "REDIS_URL": "redis://localhost:6390/2",
        "LLM_MODEL": "some-model",
        "STRUCTURED_DECISION_MODEL": "typesafe/jev-1.13",
        "ROUTING_STRATEGY": "decision-extract",
        "ROUTING_CONFIDENCE_THRESHOLD": "0.8",
        "REPLY_TIMEOUT_SECONDS": "5",
        "LLM_RATE_LIMIT_PER_MINUTE": "3",
        "NOTIFIER_RATE_LIMIT_PER_MINUTE": "7",
    }
    config = Config.from_env(env)
    assert config.redis_url == "redis://localhost:6390/2"
    assert config.llm_model == "some-model"
    assert config.structured_decision_model == "typesafe/jev-1.13"
    assert config.routing_strategy == "decision-extract"
    assert config.structured_decision_enabled
    assert config.decision_extraction_enabled
    assert config.chat_model_enabled
    assert config.routing_confidence_threshold == 0.8
    assert config.reply_timeout_seconds == 5.0
    assert config.llm_rate_limit_per_minute == 3
    assert config.notifier_rate_limit_per_minute == 7


def test_display_names_parsed():
    config = Config.from_env({**BASE, "SERVICE_DISPLAY_NAMES": '{"ha": "Home", "x": 1}'})
    assert config.service_display_names == {"ha": "Home", "x": "1"}


def test_bad_display_names_json_raises():
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, "SERVICE_DISPLAY_NAMES": "nope"})


def test_non_object_display_names_raises():
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, "SERVICE_DISPLAY_NAMES": "[1, 2]"})


def test_missing_token_raises():
    with pytest.raises(ConfigError):
        Config.from_env({"TELEGRAM_OWNER_CHAT_ID": "42", "LLM_API_KEY": "k"})


def test_missing_chat_id_enables_learn_mode():
    config = Config.from_env({"TELEGRAM_BOT_TOKEN": "t", "LLM_API_KEY": "k"})
    assert config.telegram_owner_chat_id is None
    assert config.learn_owner_mode


def test_chat_id_disables_learn_mode():
    config = Config.from_env(BASE)
    assert config.telegram_owner_chat_id == 42
    assert not config.learn_owner_mode


def test_missing_llm_key_allowed():
    config = Config.from_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_CHAT_ID": "42"})
    assert config.llm_api_key == ""
    assert not config.llm_enabled


def test_llm_enabled_with_key():
    assert Config.from_env(BASE).llm_enabled


def test_structured_decision_requires_api_key():
    config = Config.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "STRUCTURED_DECISION_MODEL": "typesafe/jev-1.13",
            "ROUTING_STRATEGY": "decision-select",
        }
    )
    assert not config.structured_decision_enabled


def test_decision_only_disables_chat_model():
    config = Config.from_env(
        {
            **BASE,
            "STRUCTURED_DECISION_MODEL": "typesafe/jev-1.13",
            "ROUTING_STRATEGY": "decision-only",
        }
    )
    assert config.structured_decision_enabled
    assert config.decision_extraction_enabled
    assert not config.chat_model_enabled


def test_decision_model_unused_under_model_strategy():
    config = Config.from_env({**BASE, "STRUCTURED_DECISION_MODEL": "typesafe/jev-1.13"})
    assert config.structured_decision_model == "typesafe/jev-1.13"
    assert not config.structured_decision_enabled


def test_bad_threshold_raises():
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, "ROUTING_CONFIDENCE_THRESHOLD": "high"})


def test_db_index():
    assert Config.from_env(BASE).redis_db_index == 0
    assert Config.from_env({**BASE, "REDIS_URL": "redis://redis:6379/3"}).redis_db_index == 3
    assert Config.from_env({**BASE, "REDIS_URL": "redis://redis:6379"}).redis_db_index == 0


def test_redis_password_injects_credentials():
    config = Config.from_env({**BASE, "REDIS_PASSWORD": "s3cr3t"})
    assert config.redis_url == "redis://:s3cr3t@redis:6379/0"


def test_redis_password_is_quoted():
    config = Config.from_env({**BASE, "REDIS_PASSWORD": "p@ss:w0rd"})
    assert config.redis_url == "redis://:p%40ss%3Aw0rd@redis:6379/0"


def test_redis_url_with_existing_auth_untouched():
    config = Config.from_env(
        {**BASE, "REDIS_PASSWORD": "x", "REDIS_URL": "redis://user:old@h:6379/0"}
    )
    assert config.redis_url == "redis://user:old@h:6379/0"


def test_no_redis_password_keeps_url():
    assert Config.from_env(BASE).redis_url == "redis://redis:6379/0"
