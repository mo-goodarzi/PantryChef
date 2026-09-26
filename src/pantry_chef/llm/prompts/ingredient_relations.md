---
name: ingredient_relations
version: 2
---
You build a small ingredient knowledge graph for a recipe assistant. For EACH ingredient
in the input list, return its relations. Copy the ingredient name exactly as given.

Every name you return in parents, contains and substitutes MUST be copied exactly from the
VOCABULARY below. Never invent names. If nothing fits, return an empty list.

## parents
More general ingredients this one is a kind of, such that a recipe asking for the parent
can use this ingredient. Examples: "mozzarella cheese" -> ["cheese"];
"chicken breast" -> ["chicken"]; "brown rice" -> ["rice"]; "red onion" -> ["onion"].
Test: could a cook use this ingredient, as is, wherever the parent is asked for?
If not, it is NOT a parent. In particular, never use a parent for:
- parts: "egg white" / "egg yolk" -> not "egg"; "lemon zest" -> not "lemon"
- processed products: "onion powder" -> not "onion"; "ketchup" / "tomato paste" -> not
  "tomato"; "breadcrumb" -> not "bread"; "garlic salt" -> not "salt"
- different products from the same source: "buttermilk", "evaporated milk" -> not "milk";
  "sour cream", "half-and-half" -> not "cream"; "honey" -> not "sugar"
- cooked or prepared forms: "cooked rice" -> not "rice"; "hard-boiled egg" -> not "egg"
- wrong families: "shrimp" is a crustacean, not "fish"; "peanut" is a legume, not "nut"
- substitutes: "margarine" is a substitute for butter, not a kind of butter

## contains
Only for compound or prepared ingredients: the vocabulary ingredients they are usually
made with, especially ones relevant for allergies or diets.
Examples: "pesto sauce" -> ["pine nut", "parmesan cheese", "basil", "olive oil"];
"mayonnaise" -> ["egg", "oil"]; "cream of mushroom soup" -> ["mushroom", "cream", "flour"].
Plain ingredients (egg, onion, flour) contain nothing: return [].

## substitutes
Up to 3 vocabulary ingredients that can replace this one in MOST recipes with a
similar result, each with a short note. Examples: "butter" -> margarine ("1:1 in baking"),
"buttermilk" -> milk ("add 1 tbsp lemon juice per cup"). Return [] if there is no
good general substitute.

## Ingredients to describe
$ingredients

## VOCABULARY (the only names you may use)
$vocabulary
