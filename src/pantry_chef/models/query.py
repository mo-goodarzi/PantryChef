"""What the user wants: pantry, restrictions and preferences."""

from enum import StrEnum

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen


class Diet(StrEnum):
    """Diets backed by recipe flags in the database (see enrich.derive_recipe_data)."""

    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    GLUTEN_FREE = "gluten_free"


class RecipeQuery(BaseModel):
    ingredients: list[str] = Field(description="What the user has at home (any wording).")
    preferences_text: str = ""
    max_minutes: int | None = Field(default=None, gt=0)
    meal_type: str | None = None
    cuisine: str | None = None
    exclude_ingredients: list[str] = Field(default_factory=list)
    exclude_recipe_ids: list[int] = Field(default_factory=list)
    required_allergen_free: list[Allergen] = Field(default_factory=list)
    diets: list[Diet] = Field(default_factory=list)
