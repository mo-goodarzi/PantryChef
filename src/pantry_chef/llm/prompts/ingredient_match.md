---
name: ingredient_match
version: 2
---
A home cook lists what they have. For EACH recipe ingredient below, decide which of the
cook's items (if any) can be used for it, and how. Copy names exactly as given.

Labels describe the COOK's item relative to the RECIPE ingredient (direction matters):
- same: the same ingredient, or a specific kind of it.
  cook "cheddar cheese" / recipe "cheese"; cook "large egg" / recipe "egg"
- contains: the cook's item BECOMES the recipe ingredient with one simple home step
  (separating, juicing, zesting, cooking, crumbling, toasting, grating).
  cook "egg" / recipe "egg white" (separate the egg); cook "rice" / recipe "cooked rice";
  cook "lemon" / recipe "lemon juice"; cook "bread" / recipe "breadcrumb"
  NOT contains: products that are merely MADE WITH the cook's item. Having eggs does not
  give you "egg noodles", "egg bread" or "egg roll wrappers"; having milk does not give you
  "cheese". Those are "different".
- substitute: a different ingredient that works in most recipes with a similar result.
  cook "pasta" / recipe "spaghetti"; cook "butter" / recipe "margarine";
  cook "bread" / recipe "toast"
- different: cannot be used for it.
  cook "egg white" / recipe "egg" (a white is not a whole egg);
  cook "eggplant" / recipe "egg"; cook "chicken broth" / recipe "chicken";
  cook "egg" / recipe "egg noodle"; cook "egg" / recipe "egg tomato" (a tomato variety)
Read recipe names literally and carefully: a shared word does not mean a match.

If several cook items fit, choose the best one (same > contains > substitute).
If none fits, return user_term null and label "different".

## Cook has
$user_terms

## Recipe ingredients
$recipe_terms
