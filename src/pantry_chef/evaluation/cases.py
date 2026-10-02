"""Search evaluation cases (eval/cases/search.json)."""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen, parse_user_allergy
from pantry_chef.models.query import Diet, NutritionGoal, RecipeQuery, goals_text


class SearchCase(BaseModel):
    id: str
    group: str
    pantry: list[str]
    preferences: str  # the wish as the finder writes it: nutrition goals go in `goals`
    max_minutes: int | None = None
    allergies: list[str] = Field(default_factory=list)
    diets: list[Diet] = Field(default_factory=list)
    goals: list[NutritionGoal] = Field(default_factory=list)

    @property
    def judged_wish(self) -> str:
        """What the user asked for, goals included: the judge rates the whole request,
        while the search only gets `preferences` (as from the finder) plus the goals."""
        goals = goals_text(self.goals)
        if not goals:
            return self.preferences
        return f"{self.preferences} ({goals})" if self.preferences else goals

    def to_query(self) -> RecipeQuery:
        allergens: set[Allergen] = set()
        for allergy in self.allergies:
            allergens |= parse_user_allergy(allergy)
        return RecipeQuery(
            ingredients=self.pantry,
            preferences_text=self.preferences,
            max_minutes=self.max_minutes,
            required_allergen_free=sorted(allergens),
            diets=self.diets,
            nutrition_goals=self.goals,
        )


def load_cases(path: Path) -> list[SearchCase]:
    cases = [SearchCase.model_validate(item) for item in json.loads(path.read_text())]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases
