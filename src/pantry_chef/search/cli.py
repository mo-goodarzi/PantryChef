"""Find recipes from the terminal (Phase 3: filters + coverage + verifier, no LLM).

Usage:
    uv run python -m pantry_chef.search.cli --have "egg,milk,flour,butter"
    uv run python -m pantry_chef.search.cli --have "egg,milk,bread" --allergy peanuts \\
        --diet vegetarian --max-minutes 30 --show-failed
"""

import argparse
import sys
import time
from pathlib import Path

from pantry_chef.agents.verifier import verify
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.allergens import Allergen, parse_user_allergy
from pantry_chef.models.query import Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.verification import VerificationResult, VerificationStatus
from pantry_chef.observability import configure_logging
from pantry_chef.search.engine import pantry_names, search


def split_list(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def build_query(args: argparse.Namespace) -> RecipeQuery:
    allergens: set[Allergen] = set()
    for allergy in split_list(args.allergy):
        allergens |= parse_user_allergy(allergy)
    return RecipeQuery(
        ingredients=split_list(args.have),
        max_minutes=args.max_minutes,
        meal_type=args.meal_type,
        cuisine=args.cuisine,
        exclude_ingredients=split_list(args.exclude),
        required_allergen_free=sorted(allergens),
        diets=[Diet(d.replace("-", "_")) for d in split_list(args.diet)],
    )


def describe(candidate: Candidate, pantry: set[str]) -> list[str]:
    uses = [i.name for i in candidate.ingredients if i.canonical_name in pantry]
    staples = [i.name for i in candidate.ingredients if i.is_staple]
    extra = [
        i.name
        for i in candidate.ingredients
        if not i.is_key and not i.is_staple and i.canonical_name not in pantry
    ]
    rating = (
        f"{candidate.avg_rating:.1f}* ({candidate.n_ratings})" if candidate.n_ratings else "unrated"
    )
    lines = [f"{candidate.name}  | {candidate.minutes} min | {rating} | id {candidate.recipe_id}"]
    lines.append(f"     uses: {', '.join(uses) or '-'}")
    if staples:
        lines.append(f"     staples: {', '.join(staples)}")
    if extra:
        lines.append(f"     also needs (non-key): {', '.join(extra)}")
    return lines


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Find recipes you can make.")
    parser.add_argument("--have", required=True, help="comma-separated pantry ingredients")
    parser.add_argument("--allergy", help="comma-separated, e.g. peanuts,milk,shellfish")
    parser.add_argument("--diet", help="comma-separated: vegetarian, vegan, gluten-free")
    parser.add_argument("--exclude", help="comma-separated ingredients to avoid")
    parser.add_argument("--max-minutes", type=int)
    parser.add_argument("--meal-type", help="e.g. breakfast, main-dish, dessert")
    parser.add_argument("--cuisine", help="e.g. italian, mexican")
    parser.add_argument("--top", type=int, default=5, help="verified recipes to show")
    parser.add_argument("--candidates", type=int, default=50, help="candidates to verify")
    parser.add_argument("--show-failed", action="store_true")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    try:
        query = build_query(args)
    except ValueError as error:
        sys.exit(f"error: {error}")

    conn = connect(args.db)
    start = time.perf_counter()
    result = search(conn, query, limit=args.candidates)
    verified: list[tuple[Candidate, VerificationResult]] = [
        (c, verify(c, query)) for c in result.candidates
    ]
    elapsed = time.perf_counter() - start

    pantry = set(pantry_names(query))
    passed = [c for c, v in verified if v.status is VerificationStatus.PASS]
    print(
        f"Pantry: {', '.join(sorted(pantry))}\n"
        f"{result.matched_recipes:,} recipes use your ingredients and pass the filters; "
        f"{len(passed)} of the top {len(verified)} pass verification ({elapsed:.2f}s)\n"
    )
    for number, candidate in enumerate(passed[: args.top], start=1):
        lines = describe(candidate, pantry)
        print(f"{number:>2}. " + "\n".join(lines) + "\n")
    if not passed:
        print("No recipe passed verification. Try --show-failed to see what is missing.\n")

    if args.show_failed:
        print("Failed verification:")
        for candidate, verification in verified:
            if verification.status is VerificationStatus.FAIL:
                reasons = "; ".join(str(r) for r in verification.reasons)
                print(f"  - {candidate.name} (id {candidate.recipe_id}): {reasons}")


if __name__ == "__main__":
    main()
