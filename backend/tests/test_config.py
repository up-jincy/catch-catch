from pathlib import Path

import pytest
from pydantic import ValidationError

from customer_signal.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name in (
        "AGENT_MODE",
        "GOOGLE_API_KEY",
        "GEMINI_MODEL",
        "DATABASE_PATH",
        "API_HOST",
        "API_PORT",
        "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(variable_name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.agent_mode == "auto"
    assert settings.google_api_key is None
    assert settings.gemini_model == "gemini-3.6-flash"
    assert settings.database_path == Path("data/generated/customer_signal.duckdb")
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.frontend_origin == "http://127.0.0.1:3000"


@pytest.mark.parametrize(
    ("google_api_key", "expected_mode"),
    [(None, "fixture"), ("test-key", "gemini")],
)
def test_auto_mode_is_resolved_from_api_key(
    google_api_key: str | None,
    expected_mode: str,
) -> None:
    settings = Settings(
        agent_mode="auto",
        google_api_key=google_api_key,
        _env_file=None,
    )

    assert settings.resolved_agent_mode == expected_mode


@pytest.mark.parametrize("agent_mode", ["fixture", "gemini"])
def test_explicit_agent_mode_is_preserved(agent_mode: str) -> None:
    settings = Settings(agent_mode=agent_mode, _env_file=None)

    assert settings.resolved_agent_mode == agent_mode


def test_unknown_agent_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(agent_mode="unknown", _env_file=None)
