from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentMode = Literal["auto", "fixture", "gemini"]
ResolvedAgentMode = Literal["fixture", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    agent_mode: AgentMode = "auto"
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "google_api_key"),
    )
    gemini_model: str = "gemini-3.7-flash"
    gemini_fallback_model: str = "gemini-3.6-flash"
    database_path: Path = Path("data/generated/customer_signal.duckdb")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:3000"

    @computed_field
    @property
    def resolved_agent_mode(self) -> ResolvedAgentMode:
        if self.agent_mode == "auto":
            has_api_key = bool(
                self.gemini_api_key and self.gemini_api_key.get_secret_value().strip()
            )
            return "gemini" if has_api_key else "fixture"

        return self.agent_mode
