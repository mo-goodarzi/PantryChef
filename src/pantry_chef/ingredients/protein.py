"""When does a recipe count as high protein? Computed once per recipe at enrich time
(recipes.is_high_protein) and used by the "high protein" goal in the ranking.

Food.com's nutrition misses meat it cannot count ("4 steaks" -> 0%), so a key meat or fish
ingredient is enough. Other protein sources (eggs, tofu, beans, lentils, nuts) need the
numbers: >= 20% of the daily value per serving (the US "high in" level, 10 g) and >= 20% of
calories from protein, which keeps out egg desserts (cookies 5%, cheesecake 7%) whatever
their serving size.
"""

from dataclasses import dataclass

HIGH_PROTEIN_PDV = 20.0
HIGH_PROTEIN_CALORIE_SHARE = 0.20
PROTEIN_KCAL_PER_PDV = 2.0  # 1% of the 50 g daily value = 0.5 g = 2 kcal


@dataclass(frozen=True)
class ProteinFacts:
    """What the high-protein goal looks at for one recipe."""

    protein_pdv: float | None = None
    calories: float | None = None
    meat_or_fish_key: bool = False  # a key ingredient is meat or fish
    protein_source_key: bool = False  # a key ingredient has category protein (any kind)


def protein_calorie_share(protein_pdv: float | None, calories: float | None) -> float | None:
    if protein_pdv is None or not calories or calories <= 0:
        return None
    return protein_pdv * PROTEIN_KCAL_PER_PDV / calories


def is_high_protein(facts: ProteinFacts) -> bool:
    """Built around meat or fish, or around another protein source with the numbers to
    show it (an egg in a cake is a protein source, but the cake fails the calorie share)."""
    if facts.meat_or_fish_key:
        return True
    share = protein_calorie_share(facts.protein_pdv, facts.calories)
    return (
        facts.protein_source_key
        and facts.protein_pdv is not None
        and facts.protein_pdv >= HIGH_PROTEIN_PDV
        and share is not None
        and share >= HIGH_PROTEIN_CALORIE_SHARE
    )
