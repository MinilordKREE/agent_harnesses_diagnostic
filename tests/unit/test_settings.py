from __future__ import annotations

import pytest

from ahd.errors import ConfigError
from ahd.settings import Settings, load_settings, mask_secret


def test_loads_from_environment_without_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-value-1234567890")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    settings = load_settings(env_file=None)
    assert settings.deepseek_api_key.get_secret_value() == "sk-test-value-1234567890"
    assert settings.hf_token is None
    assert "sk-test" not in repr(settings)
    assert "sk-test" not in str(settings.deepseek_api_key)


def test_missing_key_names_variable_not_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        load_settings(env_file=None)


def test_explicit_construction() -> None:
    settings = Settings.model_validate({"deepseek_api_key": "abc"})
    assert settings.deepseek_api_key.get_secret_value() == "abc"


def test_mask_secret() -> None:
    assert mask_secret("sk-1234567890abcdef") == "sk-1***cdef"
    assert mask_secret("short") == "***"


def test_serper_key_accepts_owner_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setenv("Serper_key", "serper-secret-value-xyz")
    settings = load_settings(env_file=None)
    assert settings.serper_api_key is not None
    assert settings.serper_api_key.get_secret_value() == "serper-secret-value-xyz"
    assert "serper-secret" not in repr(settings)
    monkeypatch.delenv("Serper_key")
    monkeypatch.setenv("SERPER_API_KEY", "other")
    assert load_settings(env_file=None).serper_api_key is not None
