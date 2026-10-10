# Deployment plan — public demo of PantryChef

This plan covers Phase 8 task 4 ("Deploy an MVP") and Phase 11 tasks 1–2 (hosting, rate
limiting, spending cap) of `docs/IMPLEMENTATION_PLAN.md`. Decisions marked **(owner)** are
not covered by the implementation plan and need the owner's choice before that step starts.

## 1. Starting point (what exists today)

| Piece | State | What it means for deployment |
|---|---|---|
| Docker image | `Dockerfile`, CPU torch, ~2 GB, runs as uid 1000 | Reusable as is. |
| Compose | `api` (uvicorn :8000) + `ui` (Streamlit :8501), `./data` mounted | Works on any VM; platforms with one public port need one container (§3). |
| Recipe data | `pantry.db` (read-only) + `chroma/` (~430 MB, 231,635 vectors) | Not in git and not in the image. Must reach the server another way (§4). |
| Runtime state | `state.db`: profiles, LangGraph checkpoints, matcher cache | Needs a writable disk; SQLite allows **one instance only**. |
| Embedding model | `BAAI/bge-small-en-v1.5`, downloaded to `data/hf-cache` on first start | Slow cold start (healthcheck allows 120 s); pre-download it in the image. |
| Concurrency | Graph calls run one at a time under a lock | Fine for a demo; visitors queue. |
| Auth | None; profile keyed by a typed user name | Anyone who types the same name sees/deletes that profile. **Must change for a public URL** (§5.3). |
| Cost control | `llm/usage.py` counts tokens per process; `--max-cost` only in the eval | **No cap in the app yet** (§5.2). |
| Video agent | Not built (rest of Phase 8) | Deploy without it; `ask_video` stays skipped. Add `YOUTUBE_API_KEY` later. |
| CI | ruff, mypy, pytest on PRs | No image build or deploy job yet (§6). |

Cost reference: a cold conversation costs about **$0.02** with gpt-5.4-mini
(`docs/decisions.md`, Phase 7).

## 2. Hosting choice **(owner)**

| Option | Cost | Fits | Watch out for |
|---|---|---|---|
| **A. Hugging Face Spaces (Docker)** — recommended | Free CPU tier (check current limits) | Well-known for ML portfolios; plenty of RAM for torch + Chroma | One public port → API + UI in one container; disk is ephemeral (state lost on restart); Space sleeps when idle (cold start ~1–2 min) |
| B. Small VPS (e.g. Hetzner/DigitalOcean, 4 GB RAM) + `docker compose` + Caddy (HTTPS) | ~$5–8/month | Same compose as local; persistent disk; full control | You run updates, firewall, backups |
| C. Fly.io / Railway / Render with a volume | ~$10–30/month at 2–4 GB RAM | Managed HTTPS, volumes, deploy from image | Small plans (512 MB) are too small for torch + Chroma |

**Recommendation:** A for the public demo (free, recognisable, matches the plan). Ephemeral
state is acceptable — even privacy-friendly — for a demo, as long as the matcher cache is
seeded (§4). Choose B if stored profiles must survive restarts.

Memory budget to verify on the target (step 1 of §7): torch + sentence-transformers
~1 GB, Chroma index ~0.5 GB, `pantry.db` page cache, two Python processes. Plan for
**≥ 4 GB RAM**.

## 3. Container layout

- **Option A (one port):** a small entrypoint script starts uvicorn on `127.0.0.1:8000`
  (not public) and Streamlit on the public port (`7860` on Spaces), with
  `PANTRY_CHEF_API_URL=http://127.0.0.1:8000`. If either process exits, the container
  exits so the platform restarts it. Only the UI is public; the API is not reachable from
  outside, which removes most of the abuse surface.
- **Options B/C:** keep `docker-compose.yml`; put Caddy in front of `ui` only and do not
  publish port 8000.
- Bake the embedding model into the image at build time (`HF_HOME` inside the image, not
  in `/app/data`) to shorten cold starts and avoid a runtime download.
- Set Streamlit's `--server.maxUploadSize` low (no uploads until Phase 10).

## 4. Getting the data to the server **(owner: licence check)**

The data is never committed to git (CLAUDE.md). Options:

1. **Private Hugging Face dataset repo** (recommended with A): upload `pantry.db`, `chroma/`
   and a *seed* `state.db` that contains only the matcher cache (no profiles, no
   checkpoints). At container start, `scripts/fetch_data.py` downloads them with an
   `HF_TOKEN` secret into `/app/data/processed` if missing, and verifies a checksum
   manifest.
2. **Bake into the image** (simplest, image grows to ~3 GB+). The image must then stay
   private, because it contains the dataset.
3. **Copy once to the VPS volume** (option B): `rsync` the `data/processed` folder.

Before any of these: check the Food.com dataset licence on Kaggle. If redistribution is
not allowed, the data repo/image must stay private (the demo only *serves results*, it
does not offer the data for download). If the full DB is too large or slow, deploy the
filtered subset from Phase 11 (≤ 120 min, ≤ 15 ingredients) and document it in the README.

Seeding the matcher cache matters: it holds paid LLM answers, so a fresh `state.db` on
every restart would raise cost and latency.

## 5. Changes needed in the code before going public

Each item is a small PR with tests (LLM calls mocked), following the usual branch rules.

### 5.1 Demo settings (`config.py`)
- `DEMO_MODE` (default off): turns on the guards below; local use and tests are unchanged.
- `DAILY_BUDGET_USD` (e.g. 2.00), `MAX_TURNS_PER_SESSION` (e.g. 20),
  `MAX_MESSAGE_CHARS` (e.g. 500), `RATE_LIMIT_PER_MINUTE` (e.g. 6 per session).
- Add them to `.env.example` with empty/safe values (`tests/test_config.py` already checks
  that no key there has a value).

### 5.2 Spending cap (Phase 11 task 2)
- A `BudgetGuard` behind the LLM provider factory (`llm/factory.py`): before each call,
  read today's spend; after each call, add the priced cost from `llm/usage.py` to a
  `daily_spend` table in `state.db` (survives restarts on B/C; on A it resets with the
  container, so the provider-side limit below is the real backstop).
- When the budget is reached, the API returns `503` with a friendly message ("the demo's
  daily budget is used up, try tomorrow or run it locally") and the UI shows it.
- A model without a price counts as over budget (same rule as `--max-cost` in the eval).
- **Provider backstop:** a separate OpenAI project + key for the demo with a hard monthly
  budget set in the OpenAI dashboard. Never reuse the development key.

### 5.3 Identity and privacy for strangers
- In demo mode, the UI no longer asks for a user name: it creates a random id per browser
  session (`st.session_state`) and sends that. No visitor can read or delete another
  visitor's profile.
- Keep the consent step; without consent nothing is stored (already the rule).
- **Cleanup job** (open item in `docs/decisions.md`): on API start and then hourly, delete
  checkpoints and profiles older than N days (e.g. 7) and orphaned checkpoints from
  crashed sessions.
- Show a short banner: demo, not medical advice, what is stored and for how long.

### 5.4 Rate limiting and input limits
- Per-session and per-IP limits in the API (a small in-memory token bucket is enough for
  one instance; no new service). Return `429`; the UI shows "slow down".
- Reject messages over `MAX_MESSAGE_CHARS` (`422`) and sessions over
  `MAX_TURNS_PER_SESSION`.
- Because graph calls run one at a time, add a queue timeout: if the lock is not free
  within ~60 s, return `503 busy` instead of hanging the browser.

### 5.5 Readiness and observability
- `/health` stays a liveness check; add `/ready` that confirms the databases open, Chroma
  has vectors and the embedding model loaded. The platform healthcheck uses `/ready`.
- Langfuse keys as platform secrets; tracing stays a no-op without them. Tag traces with
  `environment=demo` so demo traffic is separate from eval runs.
- JSON logs go to the platform log view; confirm no allergy text appears (privacy rule).

## 6. CI/CD

1. **CI (existing):** lint, format, mypy, pytest on every PR.
2. **New job `docker`:** on PRs, build the image (with BuildKit cache) and run a smoke
   test: start the container with the test fixture DB, call `/health`, then stop. Uses
   `TORCH_INDEX=""` where embeddings are not needed, as the Dockerfile already allows.
3. **New workflow `deploy.yml`:** on a tag `v*` (milestones: `v1.0` per the plan), build
   and push the image to GHCR tagged with the version and the commit SHA, then deploy:
   - A: push the deploy files to the Space repo (or let the Space build from the image).
   - B: SSH to the VPS, `docker compose pull && docker compose up -d`.
   - C: the platform's deploy action.
4. **Post-deploy smoke test:** a script drives one scripted conversation through the live
   URL via `api/client.py` (or the Streamlit UI with Playwright on option A) and checks a
   `final` answer arrives. Cost ≈ $0.02 per deploy.
5. **Rollback:** redeploy the previous image tag; data and `state.db` are not touched by
   a deploy.

Secrets live only in GitHub Actions secrets and the platform's secret store:
`OPENAI_API_KEY` (demo project), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`HF_TOKEN` (data download / Space push), later `YOUTUBE_API_KEY`.

## 7. Step-by-step order

| # | Step | Done when |
|---|---|---|
| 0 | Owner decisions: host (§2), data delivery + licence (§4), budget numbers (§5.1) | Recorded in `docs/decisions.md` |
| 1 | Measure: `pantry.db` size, peak RAM and cold-start time of the compose stack locally | Numbers in `docs/decisions.md`; host size confirmed |
| 2 | Demo settings + spending cap (§5.1–5.2) | Unit tests: cap blocks the next call, unpriced model blocked, 503 surfaced in UI |
| 3 | Random visitor id, cleanup job, demo banner (§5.3) | Tests: two sessions cannot see each other's profile; old checkpoints deleted |
| 4 | Rate/input limits, busy timeout, `/ready` (§5.4–5.5) | API tests for 429, 422, 503 busy, `/ready` |
| 5 | Single-container entrypoint + model baked in + data fetch script (§3–4) | Container starts from an empty `/app/data`, downloads data, passes `/ready` |
| 6 | CI `docker` job and `deploy.yml` (§6) | Image in GHCR; manual deploy to the host works |
| 7 | First deploy, smoke test, check Langfuse traces and logs | Public URL answers a full conversation |
| 8 | Load check: 3–5 parallel scripted conversations | Queueing works, no errors, spend as expected |
| 9 | README: live link, demo GIF, limits ("demo, one request at a time, daily budget") | PR merged; tag `v1.0` once the video agent (rest of Phase 8) is in |

## 8. Risks

- **Cost abuse** → app cap + provider hard limit + rate limits; only the UI is public.
- **Cold starts on a sleeping Space** → model baked in, data cached on disk while the
  container lives; README says the first request can take a minute.
- **Allergy data from strangers** → random ids, consent, TTL cleanup, no health text in
  logs/traces (existing rule).
- **SQLite + one lock** → one instance only; document it. Scaling out later would need a
  server database for checkpoints and per-request connections (`docs/decisions.md`,
  Phase 6).
- **Dataset licence** → keep the data private until checked.

## 9. Open questions for the owner

1. Host: Hugging Face Spaces (free, ephemeral state) or a VPS / Fly.io (paid, persistent)?
2. Data: private HF dataset repo, private image, or copy to a volume? Full DB or the
   filtered subset?
3. Daily budget for the demo (suggested $2/day) and a monthly hard limit at OpenAI
   (suggested $20)?
4. Deploy before the video agent is finished (MVP now, `v1.0` later) or after?
5. Keep the "stored profile" feature in the demo (with a 7-day TTL), or disable storage in
   demo mode entirely?
