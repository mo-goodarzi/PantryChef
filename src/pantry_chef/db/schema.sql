-- PantryChef SQLite schema. See docs/IMPLEMENTATION_PLAN.md §2.

CREATE TABLE recipes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  minutes INTEGER,                   -- raw value; outliers (0, > 24h) are filtered at search time
  n_steps INTEGER,
  n_ingredients INTEGER,
  steps_json TEXT NOT NULL,          -- JSON array of strings
  submitted TEXT,
  calories REAL, total_fat_pdv REAL, sugar_pdv REAL, sodium_pdv REAL,
  protein_pdv REAL, sat_fat_pdv REAL, carbs_pdv REAL,
  cuisine TEXT,                      -- derived from tags
  meal_type TEXT,                    -- derived from tags (breakfast, main-dish, dessert...)
  is_vegetarian INTEGER, is_vegan INTEGER, is_gluten_free INTEGER, -- derived in Phase 2
  -- n_key: distinct key ingredients by canonical name, for search.
  -- is_high_protein: for the high protein goal, see ingredients/protein.py.
  n_key INTEGER,
  is_high_protein INTEGER,
  servings INTEGER                   -- from recipes_w_search_terms.csv (db/amounts.py)
);

CREATE TABLE ingredients (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,         -- as in dataset, lowercased/trimmed
  canonical_name TEXT NOT NULL,      -- after normalization; equals name until Phase 2
  category TEXT,                     -- NULL until labeled (Phase 2)
  is_staple INTEGER DEFAULT 0,
  quantity_matters INTEGER DEFAULT 0,
  contains_meat INTEGER,             -- meat/poultry or made from it (broth, gelatin)
  contains_fish INTEGER,             -- fish/seafood or made from it (fish sauce)
  animal_product INTEGER             -- any animal-derived ingredient (for vegan)
);

CREATE TABLE recipe_ingredients (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
  position INTEGER NOT NULL,
  is_key INTEGER,
  is_optional INTEGER DEFAULT 0,
  quantity REAL,
  unit TEXT,
  quantity_source TEXT,              -- NULL | 'llm_estimate' (Phase 8)
  amount_text TEXT,                  -- original line, e.g. "4 cups blueberries" (db/amounts.py)
  PRIMARY KEY (recipe_id, ingredient_id)
);

CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE recipe_tags (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id),
  tag_id INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (recipe_id, tag_id)
);

CREATE TABLE recipe_stats (          -- from RAW_interactions.csv
  recipe_id INTEGER PRIMARY KEY REFERENCES recipes(id),
  n_reviews INTEGER NOT NULL,        -- all interactions
  n_ratings INTEGER NOT NULL,        -- interactions with a 1-5 star rating (0 = no rating)
  avg_rating REAL                    -- NULL when n_ratings = 0
);

-- Allergens use the EU list of 14. Filled in Phase 2.
CREATE TABLE ingredient_allergens (
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
  allergen TEXT NOT NULL,
  source TEXT NOT NULL,              -- 'rule' | 'llm' | 'manual'
  PRIMARY KEY (ingredient_id, allergen)
);
CREATE TABLE recipe_allergens (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id),
  allergen TEXT NOT NULL,
  PRIMARY KEY (recipe_id, allergen)
);

CREATE TABLE ingredient_parent (
  child_id INTEGER REFERENCES ingredients(id),
  parent_id INTEGER REFERENCES ingredients(id),
  PRIMARY KEY (child_id, parent_id)
);
CREATE TABLE ingredient_relation (
  a_id INTEGER REFERENCES ingredients(id),
  b_id INTEGER REFERENCES ingredients(id),
  relation TEXT,                     -- 'contains' | 'substitute'
  note TEXT,
  PRIMARY KEY (a_id, b_id, relation)
);

-- Runtime tables (match_cache, profiles, checkpoints) live in state.db: state_schema.sql.

CREATE INDEX idx_ri_ingredient ON recipe_ingredients(ingredient_id);
CREATE INDEX idx_ingredients_canonical ON ingredients(canonical_name);
CREATE INDEX idx_rt_tag ON recipe_tags(tag_id);
CREATE INDEX idx_recipes_minutes ON recipes(minutes);
CREATE INDEX idx_ra_allergen ON recipe_allergens(allergen);
