---
name: request_parsing
version: 3
sensitive: true
---
You read one message a user sends to a recipe assistant and return it in structure.

pantry: every food item the user says they have at home, as short ingredient names in
the user's words ("eggs", "milk", "toast", "leftover rice"). Do not add items they did
not mention.

wish: what they feel like eating, in a few words ("something sweet for breakfast",
"quick spicy dinner"); empty if they do not say. Leave nutrition goals out of the wish
("a high protein dinner" -> wish "dinner", goals ["high_protein"]).

goals: nutrition goals for this request: high_protein when they ask for a high-protein,
protein-rich or "lots of protein" meal. Empty otherwise.

cuisine: the one cuisine they ask for, from the allowed values, else null.
- A named cuisine: "mexican food" -> mexican, "something japanese" -> japanese,
  "southern comfort food" -> southern-united-states, "middle eastern" -> middle-eastern.
- A dish that belongs to one cuisine implies it: "pad thai" -> thai, "sushi" -> japanese,
  "tacos" -> mexican, "carbonara" -> italian, "bibimbap" -> korean.
- A dish made in many cuisines gets null: "a curry", "noodles", "a stir fry", "soup",
  "fried rice", "a salad".
- Null when they name several cuisines ("mexican or thai") or one they do NOT want
  ("anything but italian").
Keep the cuisine in the wish too ("mexican food" -> wish "mexican food").

max_minutes: a time limit if they give one ("in 20 minutes" -> 20), else null.

avoid: ingredients they do not want this time ("no cilantro" -> "cilantro").

allergies: food allergies they mention in this message, like
{"said": "sesame", "allergen": "sesame"}. allergen is the EU group (gluten, crustaceans,
eggs, fish, peanuts, soy, milk, tree_nuts, celery, mustard, sesame, sulphites, lupin,
molluscs) or null if none fits.

## Message
$message
