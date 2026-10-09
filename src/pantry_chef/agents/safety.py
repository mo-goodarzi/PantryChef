"""Safety intake: turn the user's answer about allergies, diets and health into a
profile they confirm.

The LLM only reads the text. Code decides which allergen codes apply: the user's words go
through the same alias table as the CLI, and the LLM's group can only ADD codes. Health
conditions come back as supported restrictions only (e.g. low_sugar), so the condition
itself is never stored, logged or traced. Nothing is saved until the user confirms and
consents (see graph).
"""

from pydantic import BaseModel, Field

from pantry_chef.ingredients.allergens import Allergen, parse_user_allergy
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import Diet

DISCLAIMER = "This is not medical advice; please follow your doctor's guidance."


class AllergyMention(BaseModel):
    said: str = Field(description="The allergy in the user's own words, e.g. 'almonds'.")
    allergen: Allergen | None = Field(
        default=None, description="EU allergen group it belongs to, or null if none fits."
    )


class SafetyAnswer(BaseModel):
    """What the LLM reads from the user's answer (never the health condition itself)."""

    allergies: list[AllergyMention] = Field(default_factory=list)
    diets: list[Diet] = Field(default_factory=list)
    health_diets: list[Diet] = Field(default_factory=list)
    health_not_covered: bool = False
    dislikes: list[str] = Field(default_factory=list)


class SafetyIntake(BaseModel):
    """The proposed profile, shown to the user for confirmation."""

    profile: UserProfile
    allergy_groups: dict[str, list[Allergen]] = Field(default_factory=dict)  # word -> codes
    health_not_covered: bool = False


def allergen_codes(mention: AllergyMention) -> set[Allergen]:
    """Codes for one mentioned allergy: the alias table, plus the LLM's group (stricter).
    Empty when neither knows the word (e.g. "kiwi")."""
    try:
        codes = parse_user_allergy(mention.said)
    except ValueError:
        codes = set()
    if mention.allergen is not None:
        codes = codes | {mention.allergen}
    return codes


def split_allergies(
    mentions: list[AllergyMention],
) -> tuple[dict[str, list[Allergen]], list[str]]:
    """(word -> EU codes, words outside the EU list)."""
    groups, other = {}, []
    for mention in mentions:
        word = " ".join(mention.said.lower().split())
        codes = allergen_codes(mention)
        if codes:
            groups[word] = sorted(codes)
        elif word:
            other.append(word)
    return groups, other


def is_allergen_word(word: str) -> bool:
    """True if the alias table maps the word to allergen codes ("dairy", "eggs")."""
    try:
        parse_user_allergy(word)
    except ValueError:
        return False
    return True


def interpret_safety_answer(llm: StructuredLLM, answer: str) -> SafetyIntake:
    result = llm.generate(load_prompt("safety_intake"), SafetyAnswer, answer=answer)
    # A disliked allergen word ("dairy free please" read as a dislike of "dairy") becomes
    # an allergy: a dislike only drops recipes naming the word, so alfredo sauce would pass.
    dislikes = {" ".join(d.lower().split()) for d in result.dislikes if d.strip()}
    disliked_allergens = {d for d in dislikes if is_allergen_word(d)}
    mentions = result.allergies + [AllergyMention(said=d) for d in sorted(disliked_allergens)]
    groups, other = split_allergies(mentions)
    # A health reason for gluten free (e.g. coeliac) also makes gluten an allergen, so the
    # hidden-allergen check and the allergy review run, not only the diet filter.
    if Diet.GLUTEN_FREE in result.health_diets and not any(
        Allergen.GLUTEN in codes for codes in groups.values()
    ):
        groups["gluten"] = [Allergen.GLUTEN]
    profile = UserProfile(
        allergens=sorted({code for codes in groups.values() for code in codes}),
        other_allergies=sorted(set(other)),
        diets=list(dict.fromkeys(result.diets + result.health_diets)),
        health_diets=list(dict.fromkeys(result.health_diets)),
        dislikes=sorted(dislikes - disliked_allergens),
    )
    return SafetyIntake(
        profile=profile, allergy_groups=groups, health_not_covered=result.health_not_covered
    )


def readable(diet: Diet) -> str:
    return diet.value.replace("_", " ")


def confirmation_message(intake: SafetyIntake) -> str:
    """What the user is asked to confirm, in plain words."""
    profile = intake.profile
    lines = []
    if intake.allergy_groups:
        described = [
            word if [a.value for a in codes] == [word] else f"{word} ({', '.join(codes)})"
            for word, codes in intake.allergy_groups.items()
        ]
        lines.append(f"I'll avoid: {'; '.join(described)}.")
    if profile.other_allergies:
        lines.append(
            f"I'll skip recipes that list {', '.join(profile.other_allergies)}, but I can "
            "only check the 14 EU allergens reliably, so please check ingredients yourself."
        )
    choice_diets = [d for d in profile.diets if d not in profile.health_diets]
    if choice_diets:
        lines.append(f"Diets: {', '.join(readable(d) for d in choice_diets)}.")
    if profile.health_diets:
        lines.append(
            "For what you told me about your health: "
            f"{', '.join(readable(d) for d in profile.health_diets)}. {DISCLAIMER}"
        )
    if intake.health_not_covered:
        lines.append(
            "Some of what you mentioned is something I cannot adjust recipes for; "
            "please check recipes against your own dietary advice."
        )
    if profile.dislikes:
        lines.append(f"I'll leave out: {', '.join(profile.dislikes)}.")
    if not lines:
        lines.append("No allergies, diets or restrictions to consider.")
    return "\n".join(lines)
