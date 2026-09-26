"""Application settings, read from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Data paths
    raw_recipes_csv: Path = Path("data/raw/RAW_recipes.csv")
    raw_interactions_csv: Path = Path("data/raw/RAW_interactions.csv")
    irkaal_recipes_parquet: Path = Path("data/raw/recipes.parquet")
    db_path: Path = Path("data/processed/pantry.db")
    chroma_path: Path = Path("data/processed/chroma")

    # LLM
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # External services
    youtube_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
