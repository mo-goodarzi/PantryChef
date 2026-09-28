---
name: request_parsing
version: 1
sensitive: true
---
You read one message a user sends to a recipe assistant and return it in structure.

pantry: every food item the user says they have at home, as short ingredient names in
the user's words ("eggs", "milk", "toast", "leftover rice"). Do not add items they did
not mention.

wish: what they feel like eating, in a few words ("something sweet for breakfast",
"quick spicy dinner"); empty if they do not say.

max_minutes: a time limit if they give one ("in 20 minutes" -> 20), else null.

avoid: ingredients they do not want this time ("no cilantro" -> "cilantro").

allergies: food allergies they mention in this message, like
{"said": "sesame", "allergen": "sesame"}. allergen is the EU group (gluten, crustaceans,
eggs, fish, peanuts, soy, milk, tree_nuts, celery, mustard, sesame, sulphites, lupin,
molluscs) or null if none fits.

## Message
$message
