"""The cuisine rule: a named cuisine boosts matching recipes and removes other cuisines.

A recipe's cuisine comes from its Food.com tags (recipe_tags), and every tag counts, not
only the single recipes.cuisine value. For a requested cuisine each recipe is one of:
- match:   tagged with the cuisine or a member of its family (tex-mex for "mexican",
           thai for "asian") -> boosted in the ranking;
- other:   tagged only with other cuisines (bbq "southern-united-states" for "mexican")
           -> excluded;
- neutral: no cuisine tag (61% of recipes), or only tags that contain the requested one
           ("asian" on a recipe for "thai") -> kept; semantic search and wish fit decide.

The most specific tags decide: when a recipe has specific cuisine tags, its broad region
tags are only read if none of the specific ones is a match or another cuisine. Food.com
adds "asian" to most Middle Eastern recipes and "american" to many Mexican ones, so a
Lebanese recipe tagged "asian" is "other" for "something asian".

A cuisine is a preference, not safety: when fewer than FALLBACK_MIN candidates survive
the exclusion, it is dropped for the request (the boost stays), so a cuisine never leaves
the user without a recipe.
"""

import json
from dataclasses import dataclass, replace
from enum import IntEnum
from functools import cache
from typing import TYPE_CHECKING

from pantry_chef.db.tag_mapping import BROAD_CUISINES, CUISINE_TAGS, SPECIFIC_CUISINES

if TYPE_CHECKING:  # coverage.py imports this module for FIT_SQL
    from pantry_chef.search.coverage import CoverageRow

# Fewer candidates than this after the exclusion -> keep the other cuisines after all.
FALLBACK_MIN = 5
# Added to the final score of a recipe that matches the cuisine (about two missing key
# ingredients). Settings.cuisine_boost overrides it.
DEFAULT_BOOST = 0.10

# Cuisine -> its direct members, following Food.com's own tagging (a member's recipes
# nearly always carry the parent tag too). A request accepts the whole family, members of
# members included: "american" accepts "cajun" through "southern-united-states".
CUISINE_FAMILIES: dict[str, list[str]] = {
    # North America
    "north-american": ["american", "canadian", "mexican", "central-american"],
    "american": [
        "southern-united-states",
        "southwestern-united-states",
        "northeastern-united-states",
        "midwestern",
        "pacific-northwest",
        "californian",
        "hawaiian",
        "native-american",
    ],
    "southern-united-states": ["cajun", "creole", "soul"],
    "southwestern-united-states": ["tex-mex"],
    "northeastern-united-states": ["amish-mennonite", "pennsylvania-dutch"],
    "canadian": ["quebec", "ontario", "british-columbian"],
    "mexican": ["tex-mex", "baja", "oaxacan"],
    # Central/South America and Caribbean
    "central-american": ["costa-rican", "guatemalan", "honduran", "caribbean"],
    "caribbean": ["cuban", "puerto-rican"],
    "south-american": [
        "brazilian",
        "argentine",
        "chilean",
        "colombian",
        "ecuadorean",
        "peruvian",
        "venezuelan",
    ],
    # Europe
    "european": [
        "italian",
        "french",
        "greek",
        "spanish",
        "portuguese",
        "german",
        "austrian",
        "swiss",
        "belgian",
        "dutch",
        "english",
        "irish",
        "scottish",
        "welsh",
        "polish",
        "hungarian",
        "czech",
        "russian",
        "scandinavian",
        "jewish-ashkenazi",
    ],
    "scandinavian": ["danish", "swedish", "norwegian", "finnish", "icelandic"],
    # Asia: East, Southeast and South Asia; the Middle East is its own region
    "asian": [
        "chinese",
        "japanese",
        "korean",
        "thai",
        "vietnamese",
        "cambodian",
        "laotian",
        "malaysian",
        "indonesian",
        "filipino",
        "indian",
        "pakistani",
        "nepalese",
    ],
    "chinese": ["cantonese", "szechuan", "hunan", "beijing", "mongolian"],
    # Middle East and Africa
    "middle-eastern": [
        "lebanese",
        "turkish",
        "iranian-persian",
        "iraqi",
        "palestinian",
        "saudi-arabian",
        "georgian",
        "jewish-sephardi",
    ],
    "african": [
        "egyptian",
        "moroccan",
        "libyan",
        "ethiopian",
        "nigerian",
        "south-african",
        "sudanese",
        "somalian",
        "congolese",
        "angolan",
        "namibian",
    ],
    # Oceania
    "south-west-pacific": [
        "australian",
        "new-zealand",
        "polynesian",
        "micro-melanesia",
        "hawaiian",
    ],
}


class CuisineFit(IntEnum):
    """How a recipe's tags relate to the requested cuisine (the SQL returns the value)."""

    OTHER = -1
    NEUTRAL = 0
    MATCH = 1


@cache
def family(cuisine: str) -> frozenset[str]:
    """The cuisine and every cuisine inside it."""
    members = {cuisine}
    for member in CUISINE_FAMILIES.get(cuisine, []):
        members |= family(member)
    return frozenset(members)


@cache
def containers(cuisine: str) -> frozenset[str]:
    """Cuisines whose family holds this one ("asian" for "thai"): neutral, never other."""
    return frozenset(c for c in CUISINE_FAMILIES if c != cuisine and cuisine in family(c))


def has_tag_sql(param: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM recipe_tags ct JOIN tags ctn ON ctn.id = ct.tag_id "
        f"WHERE ct.recipe_id = r.id AND ctn.name IN (SELECT value FROM json_each(:{param})))"
    )


# Specific tags first, then broad ones: the first test that holds decides.
FIT_SQL = (
    f"CASE WHEN {has_tag_sql('cuisine_specific_match')} THEN {CuisineFit.MATCH} "
    f"WHEN {has_tag_sql('cuisine_specific_other')} THEN {CuisineFit.OTHER} "
    f"WHEN {has_tag_sql('cuisine_broad_match')} THEN {CuisineFit.MATCH} "
    f"WHEN {has_tag_sql('cuisine_broad_other')} THEN {CuisineFit.OTHER} "
    f"ELSE {CuisineFit.NEUTRAL} END"
)


def fit_params(cuisine: str) -> dict[str, object]:
    """The tag lists FIT_SQL reads, for one requested cuisine."""
    match = family(cuisine)
    other = set(CUISINE_TAGS) - match - containers(cuisine)
    specific, broad = set(SPECIFIC_CUISINES), set(BROAD_CUISINES)
    return {
        "cuisine_specific_match": json.dumps(sorted(match & specific)),
        "cuisine_specific_other": json.dumps(sorted(other & specific)),
        "cuisine_broad_match": json.dumps(sorted(match & broad)),
        "cuisine_broad_other": json.dumps(sorted(other & broad)),
    }


@dataclass(frozen=True)
class CuisineOutcome:
    """What the rule did in one search (span metadata, logs and the eval)."""

    cuisine: str
    matched: int  # candidates tagged with the cuisine (boosted)
    excluded: int  # candidates of other cuisines removed (0 when the fallback fired)
    fallback: bool  # too few were left, so the other cuisines were kept


def exclude_other_cuisines(
    rows: list["CoverageRow"], cuisine: str
) -> tuple[list["CoverageRow"], CuisineOutcome]:
    """Drop rows of other cuisines, unless fewer than FALLBACK_MIN rows would be left."""
    kept = [r for r in rows if r.cuisine_fit is not CuisineFit.OTHER]
    matched = sum(r.cuisine_fit is CuisineFit.MATCH for r in rows)
    if len(kept) < FALLBACK_MIN:
        return rows, CuisineOutcome(cuisine, matched, excluded=0, fallback=True)
    return kept, CuisineOutcome(cuisine, matched, excluded=len(rows) - len(kept), fallback=False)


def boost_matches(rows: list["CoverageRow"], boost: float) -> list["CoverageRow"]:
    """Add `boost` to the final score of every match and sort again."""
    boosted = [
        replace(r, final_score=r.final + boost) if r.cuisine_fit is CuisineFit.MATCH else r
        for r in rows
    ]
    return sorted(boosted, key=lambda r: r.sort_key())
