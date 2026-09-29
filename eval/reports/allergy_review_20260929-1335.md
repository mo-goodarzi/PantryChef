# Allergy review evaluation — 20260929-1334

38 hand-labeled real recipes (`eval/cases/allergy_review.json`): required = must be removed, optional = must be kept with a warning, none = should be kept without warning.

| Variant | required removed | required shown silently | optional warned | optional removed | optional silent | none clean | none false warnings | none false removals |
|---|---|---|---|---|---|---|---|---|
| keyword scan (code only) | 0% | 0 | 100% | 0 | 0 | 36% | 7 | 0 |
| LLM review (gpt-5.4-mini) | 80% | 0 | 95% | 1 | 0 | 100% | 0 | 0 |
| LLM review (gpt-5.4) | 100% | 0 | 95% | 1 | 0 | 100% | 0 | 0 |

## Mistakes per variant

### keyword scan (code only)
- 320074 (required -> warned): The steps mention tree_nuts: "garnish pie as a whole , or individual pieces , top with slivered almonds". Leave it out or check.
- 280731 (required -> warned): The steps mention tree_nuts: "in a medium bowl , whisk together pudding mix , evaporated milk , almond extract , and pumpkin pie spice". Leave it out or check.
- 297215 (required -> warned): The steps mention tree_nuts: "now , gently fold in vanilla , almond and lemon juice". Leave it out or check.
- 43930 (required -> warned): The steps mention tree_nuts: "after about 1 hour remove from refrigerator and roll in chopped pecans". Leave it out or check.
- 400191 (none -> warned): The steps mention tree_nuts: "shape into walnut sized balls and lightly coat the meatballs in the flour and fry in hot olive oil until golden". Leave it out or check.
- 69625 (none -> warned): The steps mention tree_nuts: "form the mixture into walnut sized balls and roll in breadcrumbs". Leave it out or check.
- 457631 (required -> warned): The steps mention sesame: "garnish with toasted sesame seeds and chopped parsley". Leave it out or check.
- 411612 (none -> warned): The steps mention peanuts: "room temp and peanut butter consistency is used as a filling or icing". Leave it out or check.
- 363573 (none -> warned): The steps mention peanuts: "add canola oil , and blend 2 to 3 minutes more , or until mixture has the consistency of natural peanut butter , scrapin". Leave it out or check.
- 241210 (none -> warned): The steps mention peanuts: "cook this mixture until it is the color of the inverted peanut butter jar". Leave it out or check.
- 422938 (none -> warned): The steps mention peanuts: "with food processor running , drizzle in oila tablespoon at a timeuntil mixture is to desired consistency and texture is". Leave it out or check.
- 465562 (none -> warned): The steps mention peanuts: "when the butter has melted , stir in the flour to make a roux and continue to cook , stirring , until the roux turns a d". Leave it out or check.

### LLM review (gpt-5.4-mini)
- 320074 (required -> warned): Leave out the tree_nuts: ""garnish pie as a whole , or individual pieces , top with slivered almonds"".
- 20476 (optional -> removed): allergy review: ""sprinkle with lots of poppy seeds or toasted sesame seeds"" (sesame)

### LLM review (gpt-5.4)
- 265160 (optional -> removed): allergy review: "stir in cashews or peanuts into pasta mixture" (peanuts)
