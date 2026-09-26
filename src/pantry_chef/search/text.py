"""The text that represents a recipe for embeddings and for the LLM judge/reranker."""

# Tags that describe Food.com's taxonomy, not the dish.
GENERIC_TAGS = frozenset(
    {
        "time-to-make",
        "course",
        "preparation",
        "main-ingredient",
        "cuisine",
        "occasion",
        "dietary",
        "equipment",
        "number-of-servings",
        "taste-mood",
        "low-in-something",
        "high-in-something",
        "free-of-something",
        "3-steps-or-less",
        "5-ingredients-or-less",
    }
)


def useful_tags(tags: list[str]) -> list[str]:
    return [tag for tag in tags if tag not in GENERIC_TAGS]


def recipe_text(name: str, description: str | None, tags: list[str]) -> str:
    """name. description (first 300 chars). tags: a, b, c"""
    parts = [name.strip() + "."]
    if description:
        parts.append(description.strip()[:300])
    kept = useful_tags(tags)
    if kept:
        parts.append("tags: " + ", ".join(kept))
    return " ".join(parts)
