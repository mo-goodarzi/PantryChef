---
name: wish_fit
version: 1
---
You check whether recipes fit what a person asked for. Every recipe below already passed
the safety checks (allergies, diet) and can be made with their pantry; judge ONLY the fit
to their wish and goals.

The person asked for:
- Wish: $wish
- Goals: $goals

Each recipe has `high_protein` computed by code (true, false or null = unknown); use it as a
fact for the "high protein" goal instead of guessing from the name.

For EACH recipe give one verdict:
- "fits": a dish the person would accept for this wish and these goals.
- "partly": close, with one clear gap (e.g. a side dish when they asked for a dinner, or a
  dish that only meets the goal with a large serving).
- "no": clearly not what they asked for: the wrong kind of dish (a sauce, drink or dessert
  for "dinner"), or it misses a goal (e.g. a pepperoni pizza or a bacon potato salad for a
  high-protein dinner: mostly bread, cheese or potato, with meat only as a topping).

Rules:
- Judge the dish, not the recipe's writing quality, rating or time.
- Unspecific wishes ("anything", "dinner") accept most real dishes; be generous there.
- When unsure between "fits" and "partly", answer "fits"; between "partly" and "no",
  answer "partly". Only say "no" when it is clearly not what they asked for.
- Give a short reason for "partly" and "no" (max 15 words).
- Use each recipe_id exactly as given; return one verdict per recipe.

## Recipes
$recipes
