---
name: ingredient_weights
version: 2
---
A recipe assistant asks users how much of an ingredient they have, in grams or as a
number of items. Recipes measure the same ingredient in different ways ("2 chicken
breasts", "1 lb chicken breast", "1 cup chopped chicken breast"), so the assistant needs
approximate weights to compare them.

For EACH ingredient in the input list, give typical average weights in grams. Copy the
name exactly.

## piece_g: grams in ONE item, as recipes count it
The item is what a recipe means by "1 <ingredient>", read literally: one egg, one chicken
breast (boneless, skinless), one onion, one potato, one slice of bacon, one tortilla, one
banana. A bare name is the whole thing: "1 chicken" is a whole chicken, "1 cabbage" a
whole head. Use a medium size and the edible part as bought (a whole apple, an egg
without its shell). Null when recipes do not count the ingredient in items (flour, rice,
ground beef, grated cheese, sour cream), and null when the count is ambiguous because
recipes count it in very different items (spinach in leaves or in bunches, lettuce in
leaves or in heads): a wrong item weight is worse than none.

## cup_g: grams in ONE US cup (240 ml), in the form recipes usually measure it
Cheese shredded or grated, vegetables chopped, nuts chopped or halved, berries whole,
pasta dry, beans cooked or drained. Null when the ingredient is not measured by volume
(a whole chicken, a steak, eggs in the shell).

Examples:
- egg: piece_g 50, cup_g null
- chicken breast: piece_g 200, cup_g 140
- onion: piece_g 150, cup_g 160
- cheddar cheese: piece_g null, cup_g 113
- bacon: piece_g 25, cup_g null
- chicken: piece_g 1600, cup_g 140
- ground beef: piece_g null, cup_g 225
- walnut: piece_g null, cup_g 120

Use round numbers. When you are unsure of the exact figure, give your best typical
value; use null only when the measure does not make sense for the ingredient.

## Ingredients
$ingredients
