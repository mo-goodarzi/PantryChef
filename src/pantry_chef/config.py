"""Application settings, read from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Data paths
    raw_recipes_csv: Path = Path("data/raw/RAW_recipes.csv")
    raw_interactions_csv: Path = Path("data/raw/RAW_interactions.csv")
    # Same recipes with the original ingredient lines (amounts + units) and servings.
    amounts_csv: Path = Path("data/raw/recipes_w_search_terms.csv")
    # Photo URLs per recipe (Kaggle irkaal "Food.com - Recipes and Reviews").
    images_parquet: Path = Path("data/raw/recipes.parquet")
    db_path: Path = Path("data/processed/pantry.db")  # recipes: rebuilt, read-only at runtime
    state_db_path: Path = Path("data/processed/state.db")  # runtime: profiles, caches, chats
    chroma_path: Path = Path("data/processed/chroma")

    # LLM
    llm_provider: Literal["openai"] = "openai"
    # One model per role, so cheap steps can use a small model while safety checks and the
    # eval judge stay fixed (docs/decisions.md, "Model per role").
    # Request parsing, safety intake, rerank and the ingredient matcher:
    llm_model: str = "gpt-5.4-mini"
    # Safety: the final allergy review, the hidden-allergen check and ingredient labels.
    allergy_review_model: str = "gpt-5.4-mini"
    hidden_allergen_model: str = "gpt-5.4-mini"
    # Ingredient labels (scripts/label_ingredients.py): their allergens feed the SQL filter.
    label_model: str = "gpt-5.4-mini"
    wish_fit_model: str = "gpt-5.4-mini"  # the wish-fit check (agents/wish_fit.py)
    video_model: str = "gpt-5.4-mini"  # the video match check (agents/video.py)
    judge_model: str = "gpt-5.4-mini"  # eval only: the preference judge (keep it fixed)
    wish_fit_enabled: bool = True  # chat: remove recipes that clearly miss the wish
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    llm_timeout_seconds: float = 120
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # External services
    youtube_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    # LANGFUSE_HOST, or LANGFUSE_BASE_URL (the name Langfuse's own docs use)
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("langfuse_host", "langfuse_base_url"),
    )

    # Search
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    semantic_weight: float = 0.3  # final = (1 - w) * ingredient + w * semantic
    coverage_pool: int = 1000  # top recipes by coverage that get a semantic score
    semantic_neighbors: int = 2000  # nearest recipes to the wish, added to the pool
    usage_weight: float = 0.5  # ingredient score = (1 - w) * coverage + w * pantry usage

    # Conversation
    # The quantity question (docs/decisions.md): "off", "when_it_matters" (key items whose
    # amount matters) or "always" (every non-staple pantry item the options use).
    quantity_question: Literal["off", "when_it_matters", "always"] = "when_it_matters"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
