from pathlib import Path
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentMode = Literal["auto", "fixture", "gemini"]
ResolvedAgentMode = Literal["fixture", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_mode: AgentMode = "auto"
    google_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    database_path: Path = Path("data/generated/customer_signal.duckdb")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:3000"

    @computed_field
    @property
    def resolved_agent_mode(self) -> ResolvedAgentMode:
        if self.agent_mode == "auto":
            return "gemini" if self.google_api_key else "fixture"

        return self.agent_mode
