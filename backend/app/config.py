"""Runtime configuration loaded from the environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="sqlite:///./data/analytics.db")
    reader_database_url: str | None = Field(default=None)

    llm_provider: str = Field(default="ollama")
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder:7b")
    llm_temperature: float = Field(default=0.0)
    llm_timeout_seconds: int = Field(default=60)

    # NoDecode: read these as the raw string so `_split_csv` below can accept the
    # documented `a,b,c` form instead of pydantic-settings forcing JSON on us.
    allowed_tables: Annotated[list[str], NoDecode] = Field(default_factory=lambda: [
        "customers", "products", "orders", "order_items", "order_revenue",
    ])
    hidden_columns: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["customers.email"])
    max_rows: int = Field(default=500)
    query_timeout_seconds: int = Field(default=10)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])
    history_db_url: str = Field(default="sqlite:///./data/history.db")
    conversation_window: int = Field(default=4)

    # Identity of the database the pipeline is pointed at. The built-in values
    # describe the sample warehouse; an activated upload replaces them.
    datasource_id: str = Field(default="demo")
    datasource_name: str = Field(default="Sample warehouse")
    datasource_kind: str = Field(default="builtin")

    @field_validator("allowed_tables", "hidden_columns", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        """Accept `a,b,c` from env/.env; a JSON list is accepted too."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def effective_reader_url(self) -> str:
        return self.reader_database_url or self.database_url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def base_settings() -> Settings:
    """Configuration exactly as the environment and ``.env`` describe it."""
    return Settings()


# ``app.services.datasource`` swaps the active database at runtime by cloning
# ``base_settings()`` and publishing the copy here. Every module reads settings
# through ``get_settings()``, so one override redirects the whole pipeline.
_active_override: Settings | None = None


@lru_cache
def get_settings() -> Settings:
    return _active_override or base_settings()


def set_active_settings(settings: Settings | None) -> None:
    global _active_override
    _active_override = settings
    # Cached on purpose (see the call sites); clear it so they re-read.
    get_settings.cache_clear()
