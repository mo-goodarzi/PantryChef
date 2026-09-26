---
name: ingredient_labeling
version: 2
---
You label cooking ingredients for a recipe assistant that must keep users with food
allergies safe. For EACH ingredient in the input list, return one label. Copy the
ingredient name exactly as given, including spelling and punctuation.

## category (pick one)
- protein: meat, poultry, fish, seafood, eggs, tofu, tempeh, beans, lentils, nuts as a main ingredient
- dairy: milk, cream, butter, cheese, yogurt and dairy alternatives (almond milk, vegan cheese)
- grain: flour, bread, pasta, rice, oats, cereals, crackers, tortillas, dough, baking mixes
- produce: fresh or frozen fruit and vegetables, fresh herbs used in quantity (a bunch of basil for pesto)
- spice: dried spices, dried herbs, seasoning blends, salt, pepper, extracts (vanilla)
- condiment: sauces, dressings, vinegars, mustard, ketchup, stocks and broths, pastes, jams
- fat: oils, shortening, lard, margarine, cooking spray
- sweetener: sugars, honey, syrups, molasses, sweeteners, chocolate chips
- liquid: water, juices, alcohol, coffee, tea, soda
- other: anything else (baking powder, yeast, gelatin, food coloring, ice)

## allergens (EU list of 14; use only these codes)
gluten, crustaceans, eggs, fish, peanuts, soy, milk, tree_nuts, celery, mustard,
sesame, sulphites, lupin, molluscs

Include an allergen when the ingredient typically contains it in its common homemade
or store-bought form, INCLUDING hidden allergens. Examples:
- worcestershire sauce: fish; margarine: milk; chicken broth: celery;
  milk chocolate chips: milk, soy; pesto: milk, tree_nuts; hoisin sauce: soy, gluten;
  cream of mushroom soup: milk, gluten; mayonnaise: eggs; wine: sulphites;
  oats: gluten (EU counts oats as a gluten cereal)
When unsure, include the allergen: a missed allergen is dangerous, an extra one only
hides a recipe. Respect explicit labels: "gluten-free flour" has no gluten,
"dairy-free margarine" has no milk. Coconut is NOT a tree nut. Nutmeg, butternut squash,
water chestnut and eggplant contain no allergens.

## diet fields
- contains_meat: meat or poultry, or made from it (chicken broth, beef stock, gelatin,
  lard, bacon bits). Vegetarian or meat substitutes ("vegetarian ground beef") are false.
- contains_fish: fish or seafood, or made from it (fish sauce, anchovy paste,
  worcestershire sauce, oyster sauce).
- animal_product: anything animal-derived: meat, fish, seafood, dairy, eggs, honey,
  gelatin, lard.

## Ingredients
$ingredients
