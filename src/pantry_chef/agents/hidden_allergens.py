"""Second look at compound ingredients (sauces, stocks, mixes...) for hidden allergens.

On top of the Phase 2 labels; the answer can only ADD allergens. Answers are cached per
ingredient name (all 14 allergens), so each name is asked about once.
"""

import json
import os
from pathlib import Path

from pydantic import BaseModel

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.recipe import RecipeIngredient

COMPOUND_WORDS = frozenset(
    {
        "sauce",
        "stock",
        "broth",
        "bouillon",
        "paste",
        "mix",
        "dressing",
        "seasoning",
        "soup",
        "pesto",
        "gravy",
        "marinade",
        "spread",
        "crust",
        "dough",
        "batter",
        "pickle",
        "relish",
        "chutney",
        "curry",
        "dip",
        "cereal",
        "candy",
        "chocolate",
        "cookie",
        "cracker",
        "sausage",
        "filling",
        "topping",
        "glaze",
        "creamer",
        "cordial",
        "powder",
        "base",
    }
)


def is_compound(ingredient: RecipeIngredient) -> bool:
    """Prepared or multi-ingredient products, where hidden allergens are likely."""
    if ingredient.is_staple:
        return False
    words = {w.rstrip("s") for w in ingredient.name.lower().replace("-", " ").split()}
    return ingredient.category == "condiment" or bool(words & COMPOUND_WORDS)


class HiddenAllergenItem(BaseModel):
    ingredient: str
    allergens: list[Allergen]


class HiddenAllergenBatch(BaseModel):
    items: list[HiddenAllergenItem]


class HiddenAllergenChecker:
    def __init__(self, llm: StructuredLLM, cache_path: Path, batch_size: int = 40):
        self.llm = llm
        self.cache_path = cache_path
        self.batch_size = batch_size
        self.prompt = load_prompt("hidden_allergens")
        self.cache: dict[str, list[str]] = (
            json.loads(cache_path.read_text()) if cache_path.exists() else {}
        )

    def check(self, names: list[str]) -> dict[str, set[Allergen]]:
        """Possible allergens for each ingredient name (cached)."""
        todo = [n for n in dict.fromkeys(names) if n not in self.cache]
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            result = self.llm.generate(
                self.prompt, HiddenAllergenBatch, ingredients=json.dumps(batch)
            )
            answered = {item.ingredient: item for item in result.items if item.ingredient in batch}
            for name in batch:
                item = answered.get(name)
                if item is not None:  # unanswered names are asked again next time
                    self.cache[name] = sorted(a.value for a in item.allergens)
            self.save()
        return {n: {Allergen(a) for a in self.cache[n]} for n in names if n in self.cache}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        os.replace(tmp, self.cache_path)
