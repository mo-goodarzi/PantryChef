---
name: quantity_matters
version: 1
---
A recipe assistant asks the user ONE short question about amounts before suggesting
recipes, e.g. "How many eggs do you have?". Asking about everything is annoying, so it
only asks about ingredients where running short is a real risk.

For EACH ingredient in the input list, decide quantity_matters. Copy the name exactly.

quantity_matters = true when BOTH are usually true:
1. recipes use a specific, substantial amount of it, and
2. people usually have only a limited amount at home, so they may have too little.
Examples (true): eggs; meat, poultry, fish and seafood; tofu; dry pasta and noodles;
tortillas, wrappers and pastry shells; cheese; sour cream, yogurt and cream cheese;
nuts or chocolate chips used by the cup; canned or packaged goods used as a main
component (canned beans, canned pumpkin, cake mix); fresh fruit and vegetables used as a
main component (apples, potatoes, zucchini, mushrooms, berries).

quantity_matters = false for household basics that people usually have plenty of, and
for anything used in small amounts.
Examples (false): flour of any kind, sugar of any kind, rice, oats, cornmeal, bread,
breadcrumbs, milk, cream, butter, margarine, oil, salt, pepper, spices, dried and fresh
herbs, garlic, lemon juice, condiments, sauces, vinegar, stock and broth, baking
powder and soda, yeast, extracts, water, ice, drinks and alcohol, garnishes.

## Ingredients
$ingredients
