"""Approximate weights per ingredient: grams in one item and in one cup.

Recipes measure the same ingredient in different ways ("2 chicken breasts", "1 lb",
"1 cup chopped"), and the quantity question asks for grams or a number of items. These
weights let the verifier compare across kinds (ingredients/quantities.py,
approximate_ratio). They are LLM labels (scripts/label_weights.py) saved as a seed file
in the repository, so they can be reviewed, and measured in eval/weights_eval.py.
"""

import json
from functools import lru_cache
from pathlib import Path

from pantry_chef.ingredients.quantities import Weights
from pantry_chef.models.ingredient import WeightLabel

SEED_PATH = Path(__file__).parent / "seed" / "weights.json"


def save_seed(labels: dict[str, WeightLabel], path: Path = SEED_PATH) -> None:
    """One ingredient per line: {"egg": [50, null], ...} as [piece_g, cup_g]."""
    lines = [
        f"  {json.dumps(name, ensure_ascii=False)}: {json.dumps([label.piece_g, label.cup_g])}"
        for name, label in sorted(labels.items())
        if label.piece_g or label.cup_g
    ]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n")


def read_seed(path: Path) -> dict[str, Weights]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {name: Weights(piece_g=piece, cup_g=cup) for name, (piece, cup) in data.items()}


@lru_cache
def load_weights() -> dict[str, Weights]:
    """The seed's weights by canonical ingredient name (read once per process)."""
    return read_seed(SEED_PATH)
