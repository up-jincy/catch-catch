from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from customer_signal.config import Settings


def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name in (
        "AGENT_MODE",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FALLBACK_MODEL",
        "DATABASE_PATH",
        "API_HOST",
        "API_PORT",
        "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(variable_name, raising=False)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.agent_mode == "auto"
    assert settings.gemini_api_key is None
    assert settings.gemini_model == "gemini-3.7-flash"
    assert settings.gemini_fallback_model == "gemini-3.6-flash"
    assert settings.database_path == Path("data/generated/customer_signal.duckdb")
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.frontend_origin == "http://127.0.0.1:3000"


@pytest.mark.parametrize("gemini_api_key", [None, ""])
def test_auto_mode_stays_fixture_without_usable_gemini_key(
    monkeypatch: pytest.MonkeyPatch,
    gemini_api_key: str | None,
) -> None:
    clear_settings_environment(monkeypatch)
    if gemini_api_key is not None:
        monkeypatch.setenv("GEMINI_API_KEY", gemini_api_key)

    settings = Settings(_env_file=None)

    assert settings.resolved_agent_mode == "fixture"


def test_gemini_api_key_from_environment_activates_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

    settings = Settings(_env_file=None)

    assert isinstance(settings.gemini_api_key, SecretStr)
    assert settings.gemini_api_key.get_secret_value() == "gemini-test-key"
    assert settings.resolved_agent_mode == "gemini"


def test_gemini_api_key_is_masked_in_model_output() -> None:
    raw_key = "gemini-key-that-must-not-leak"

    settings = Settings(gemini_api_key=raw_key, _env_file=None)

    assert isinstance(settings.gemini_api_key, SecretStr)
    assert raw_key not in repr(settings)
    assert raw_key not in repr(settings.model_dump())
    assert raw_key not in str(settings.model_dump(mode="json"))


def test_explicit_fixture_stays_fixture_with_gemini_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

    settings = Settings(agent_mode="fixture", _env_file=None)

    assert settings.resolved_agent_mode == "fixture"


def test_google_api_key_remains_a_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-key")

    settings = Settings(_env_file=None)

    assert settings.gemini_api_key.get_secret_value() == "legacy-key"
    assert settings.resolved_agent_mode == "gemini"


def test_gemini_api_key_takes_priority_over_legacy_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "preferred-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-key")

    settings = Settings(_env_file=None)

    assert settings.gemini_api_key.get_secret_value() == "preferred-key"


def test_unknown_agent_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(agent_mode="unknown", _env_file=None)
