"""What the user wants: pantry, restrictions and preferences."""

from enum import StrEnum

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen


class Diet(StrEnum):
    """Diets the filters and the verifier can enforce.

    Ingredient diets use recipe flags derived from ingredient facts
    (enrich.derive_recipe_data); nutrition diets use the recipe's nutrition per serving.
    """

    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    GLUTEN_FREE = "gluten_free"
    LOW_SUGAR = "low_sugar"
    LOW_SALT = "low_salt"


INGREDIENT_DIETS = frozenset({Diet.VEGETARIAN, Diet.VEGAN, Diet.GLUTEN_FREE})

# Nutrition diets: (recipe column, highest allowed % of daily value per serving).
# Low salt follows the US FDA "low sodium" claim (<= 140 mg = 6% of 2,300 mg). There is
# no official "low sugar" claim; 10% of the 50 g daily value is <= 5 g sugar per serving.
# These are recipe filters, not medical advice (see docs/decisions.md).
NUTRITION_LIMITS: dict[Diet, tuple[str, float]] = {
    Diet.LOW_SUGAR: ("sugar_pdv", 10.0),
    Diet.LOW_SALT: ("sodium_pdv", 6.0),
}


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


class AmountStatus(StrEnum):
    KNOWN = "known"  # the user gave an amount
    UNKNOWN = "unknown"  # "I don't know"
    PLENTY = "plenty"  # "plenty" / "a lot"


class PantryItem(BaseModel):
    """One thing the user has, with an amount when the quantity question asked for it."""

    name: str
    canonical_name: str
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None  # None = a count ("3 eggs")
    amount_status: AmountStatus = AmountStatus.UNKNOWN
