"""Finder: turn the user's message into a RecipeQuery.

The LLM reads the message (pantry, wish, time, things to avoid, allergies mentioned);
build_query() combines it with the profile in plain code. Restrictions from the message
can only make the query stricter.
"""

from pydantic import BaseModel, Field

from pantry_chef.agents.safety import AllergyMention, split_allergies
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import RecipeQuery


class RequestAnswer(BaseModel):
    pantry: list[str] = Field(default_factory=list)
    wish: str = ""
    max_minutes: int | None = Field(default=None, gt=0)
    avoid: list[str] = Field(default_factory=list)
    allergies: list[AllergyMention] = Field(default_factory=list)


def interpret_request(llm: StructuredLLM, message: str) -> RequestAnswer:
    return llm.generate(load_prompt("request_parsing"), RequestAnswer, message=message)


def build_query(profile: UserProfile, request: RequestAnswer) -> RecipeQuery:
    groups, other = split_allergies(request.allergies)
    allergens = set(profile.allergens) | {code for codes in groups.values() for code in codes}
    exclude = set(request.avoid) | set(profile.dislikes) | set(profile.other_allergies)
    exclude |= set(other)
    return RecipeQuery(
        ingredients=[item for item in request.pantry if item.strip()],
        preferences_text=request.wish,
        max_minutes=request.max_minutes,
        exclude_ingredients=sorted(" ".join(e.lower().split()) for e in exclude if e.strip()),
        required_allergen_free=sorted(allergens),
        diets=list(profile.diets),
    )
