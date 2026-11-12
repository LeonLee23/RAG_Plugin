from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql://rag:rag_local_demo@localhost:55432/rag_demo"
    data_dir: Path = Path("data")
    agent_provider_1: Literal["codex", "claude"] = Field(
        default="codex", validation_alias=AliasChoices("agent_provider_1", "agent_provider"))
    agent_provider_2: Literal["codex", "claude", ""] = "claude"
    agent_model: str = ""
    agent_timeout_seconds: int = Field(default=180, ge=1, le=600)
    embedding_base_url: AnyHttpUrl | None = None
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "Kingsoft-LLM/QZhou-Embedding-Zh"
    embedding_timeout_seconds: int = Field(default=30, ge=1, le=600)
    embedding_revision: str = "a26c27aff5756ca78402258440ae20f09e9219bd"

    @property
    def agent_providers(self) -> tuple[str, ...]:
        primary, backup = self.agent_provider_1, self.agent_provider_2
        return (primary, backup) if backup and backup != primary else (primary,)

    @field_validator("embedding_base_url", mode="before")
    @classmethod
    def blank_embedding_url_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


MAX_UPLOAD = 20 * 1024 * 1024
MAX_PAGES = 200
MAX_IMAGES = 100
MAX_CHUNKS = 1000
MAX_KB_CHUNKS = 10000
PIPELINE_VERSION = "1"
WORKER_LOCK = 726401
MIGRATION_LOCK = 726402
