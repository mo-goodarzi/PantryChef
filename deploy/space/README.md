---
title: PantryChef
emoji: 🍳
colorFrom: red
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Safe recipes from what you have at home, measured end to end
---

# PantryChef (demo)

Tell it what you have at home; it finds recipes you can actually make, checks them
against your allergies and diet in code, asks only the amounts that matter, and can find a
YouTube video it has checked against the recipe.

Code, design decisions and every evaluation: https://github.com/mo-goodarzi/PantryChef

Demo notes:
- Recipes, photos and amounts come from Food.com datasets on Kaggle; this is not medical
  advice, and you should always check ingredients for your allergies yourself.
- The demo has a daily budget for AI calls; when it is used up, try again tomorrow.
- "Remember me" lasts only until the Space restarts (its disk is not kept).
- The first visit after a quiet period wakes the Space up, which takes a minute or two.
