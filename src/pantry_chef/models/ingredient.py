"""Ingredient labels produced by the LLM labeler."""

from enum import StrEnum

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen


class Category(StrEnum):
    PROTEIN = "protein"
    DAIRY = "dairy"
    GRAIN = "grain"
    PRODUCE = "produce"
    SPICE = "spice"
    CONDIMENT = "condiment"
    FAT = "fat"
    SWEETENER = "sweetener"
    LIQUID = "liquid"
    OTHER = "other"


class IngredientLabel(BaseModel):
    name: str = Field(description="The ingredient name exactly as given in the input.")
    category: Category
    quantity_matters: bool = Field(
        description="True if the amount on hand decides whether the dish can be made."
    )
    allergens: list[Allergen] = Field(
        description="EU allergens the ingredient typically contains, including hidden ones."
    )
    contains_meat: bool = Field(description="Meat or poultry, or made from it (broth, gelatin).")
    contains_fish: bool = Field(description="Fish or seafood, or made from it (fish sauce).")
    animal_product: bool = Field(description="Any animal-derived ingredient, incl. dairy, egg.")


class IngredientLabelBatch(BaseModel):
    labels: list[IngredientLabel]
