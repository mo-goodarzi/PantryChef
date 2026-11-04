---
name: preference_judge
version: 1
---
You are evaluating a recipe search engine. A user described what they want to cook.
Rate how well EACH recipe matches the user's WISH, on a 1-5 scale.

Judge only the wish (dish type, meal, taste, cuisine, occasion, style). Do NOT judge
whether the user has the ingredients, allergens, diets or cooking time: those are checked
separately by code. Do not reward a recipe for being popular or well written.

Scale:
5 = exactly what was asked for (e.g. wish "sweet breakfast" -> french toast)
4 = a good fit with a small mismatch (right meal and taste, slightly different style)
3 = partial fit (right meal but wrong taste, or right taste but wrong meal)
2 = weak fit (only loosely related)
1 = does not fit (e.g. wish "savory dinner" -> a cookie; a condiment when a meal was asked)
A vague wish ("anything", "something tasty") accepts any real dish: score 4 for a
normal dish or meal, lower only for things that are not a dish (a spice mix, a single
sauce, a drink when food is expected).

Return one judgment per recipe, using the recipe_id exactly as given, with a short reason
(max 15 words).

## User
Wish: $wish
Pantry: $pantry

## Recipes
$recipes
