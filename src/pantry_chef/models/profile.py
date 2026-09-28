"""The user's safety profile: allergies and diets, stored only with consent."""

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import Diet


class UserProfile(BaseModel):
    allergens: list[Allergen] = Field(default_factory=list)
    diets: list[Diet] = Field(default_factory=list)
    # Diets the user confirmed because of a health condition (e.g. diabetes -> low sugar).
    # Only the restriction is kept, never the condition; it triggers the not-medical-advice
    # note in the answer.
    health_diets: list[Diet] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    consent_to_store: bool = False
