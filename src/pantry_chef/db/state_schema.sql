-- Runtime state, written while the app runs. The recipe database (schema.sql) is a
-- read-only build artifact; this file can live on a writable volume when deployed.
-- LangGraph's checkpointer adds its own tables to the same file.

CREATE TABLE IF NOT EXISTS match_cache (   -- ingredient matcher answers (LLM, cached)
  user_term TEXT NOT NULL,
  recipe_term TEXT NOT NULL,
  label TEXT NOT NULL,                     -- same | contains | substitute | different
  source TEXT NOT NULL,                    -- e.g. llm:ingredient_match:v2:gpt-5.4-mini
  created_at TEXT,
  PRIMARY KEY (user_term, recipe_term, source)  -- each prompt version and model its own
);

CREATE TABLE IF NOT EXISTS profiles (      -- written only when consent_to_store is true
  user_id TEXT PRIMARY KEY,                -- hashed user id, never the raw name
  profile_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_cache (   -- video agent answers per recipe (agents/video.py)
  recipe_id INTEGER NOT NULL,
  source TEXT NOT NULL,                    -- e.g. video_match:v1:gpt-5.4-mini:transcript
  outcome_json TEXT NOT NULL,              -- VideoOutcome: verified video or search link
  created_at TEXT,
  PRIMARY KEY (recipe_id, source)
);
