"""Recipes as returned by search."""

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen


class RecipeIngredient(BaseModel):
    name: str
    canonical_name: str
    category: str | None
    is_key: bool
    is_optional: bool = False
    is_staple: bool = False
    quantity_matters: bool = False
    quantity: float | None = None
    unit: str | None = None
    # Own allergens plus those of parent and contained ingredients (pesto -> pine nut).
    allergens: list[Allergen] = Field(default_factory=list)
    contains_meat: bool = False
    contains_fish: bool = False
    animal_product: bool = False


class Candidate(BaseModel):
    recipe_id: int
    name: str
    minutes: int
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    have_key: int
    total_key: int
    missing_key: list[str] = Field(default_factory=list)
    coverage: float = Field(description="have_key / total_key (1.0 when nothing is key)")
    avg_rating: float | None = None
    n_ratings: int = 0
    ingredient_score: float
    semantic_score: float | None = None
    final_score: float
    rerank_reason: str | None = None
    # Nutrition per serving as % of daily value (for low-sugar / low-salt checks).
    sugar_pdv: float | None = None
    sodium_pdv: float | None = None
