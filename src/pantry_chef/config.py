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
    db_path: Path = Path("data/processed/pantry.db")  # recipes: rebuilt, read-only at runtime
    state_db_path: Path = Path("data/processed/state.db")  # runtime: profiles, caches, chats
    chroma_path: Path = Path("data/processed/chroma")

    # LLM
    llm_provider: Literal["openai"] = "openai"
    llm_model: str = "gpt-5.4-mini"
    # Model for the final allergy review (one call per turn, ~10 recipes); can be stronger.
    allergy_review_model: str = "gpt-5.4-mini"
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    llm_timeout_seconds: float = 120
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # External services
    youtube_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # Search
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    semantic_weight: float = 0.3  # final = (1 - w) * ingredient + w * semantic
    coverage_pool: int = 1000  # top recipes by coverage that get a semantic score
    semantic_neighbors: int = 2000  # nearest recipes to the wish, added to the pool
    usage_weight: float = 0.5  # ingredient score = (1 - w) * coverage + w * pantry usage

    # Conversation
    # The quantity question is built but off until recipes have amounts (Phase 8).
    ask_quantities: bool = False

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
