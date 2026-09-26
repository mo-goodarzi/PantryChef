"""Draft ingredient relations (parent / contains / substitute) with an LLM.

The draft is saved as a JSON seed file in the repository so a person can review and edit
it. Every related name must exist in the vocabulary (canonical names from the dataset);
anything else the LLM returns is dropped and counted.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pantry_chef.ingredients.labeler import batches
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.ingredient import IngredientRelations, IngredientRelationsBatch

PROMPT_NAME = "ingredient_relations"
SEED_PATH = Path(__file__).parent / "seed" / "relations.json"


@dataclass
class DraftSummary:
    ingredients: int = 0
    relations: int = 0
    dropped_names: list[str] = field(default_factory=list)


def clean_relations(item: IngredientRelations, vocabulary: set[str], summary: DraftSummary) -> dict:
    """Keep only related names from the vocabulary, never the ingredient itself."""

    def keep(name: str) -> bool:
        if name in vocabulary and name != item.name:
            return True
        summary.dropped_names.append(name)
        return False

    return {
        "parents": sorted({n for n in item.parents if keep(n)}),
        "contains": sorted({n for n in item.contains if keep(n)}),
        "substitutes": [{"name": s.name, "note": s.note} for s in item.substitutes if keep(s.name)][
            :3
        ],
    }


def draft_relations(
    names: list[str], vocabulary: list[str], llm: StructuredLLM, batch_size: int = 30
) -> tuple[dict[str, dict], DraftSummary]:
    """Ask the LLM for relations of `names`, validated against `vocabulary`."""
    prompt = load_prompt(PROMPT_NAME)
    vocab_set = set(vocabulary)
    summary = DraftSummary()
    seed: dict[str, dict] = {}

    for batch in batches(names, batch_size):
        result = llm.generate(
            prompt,
            IngredientRelationsBatch,
            ingredients=json.dumps(batch),
            vocabulary=json.dumps(vocabulary),
        )
        wanted = set(batch)
        for item in result.items:
            if item.name in wanted:
                seed[item.name] = clean_relations(item, vocab_set, summary)

    summary.ingredients = len(seed)
    summary.relations = sum(
        len(r["parents"]) + len(r["contains"]) + len(r["substitutes"]) for r in seed.values()
    )
    return seed, summary


def load_seed(path: Path = SEED_PATH) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def save_seed(seed: dict[str, dict], path: Path = SEED_PATH) -> None:
    path.write_text(json.dumps(dict(sorted(seed.items())), indent=2, ensure_ascii=False) + "\n")
