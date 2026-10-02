# Dream Pet

An open-source, self-hosted AI companion that **gets bored**, **explores the web to learn**,
**chats** with you, and at night **consolidates its memories into a dream** — plus a short dream
video. Single user, bring your own keys, runs on your machine.

- **An inner life you can measure.** Boredom, curiosity and energy are real numbers driven by
  published ideas (kNN novelty, learning progress, habituation), not vibes. You can tune all of them.
- **Grounded dreams.** Every scene in a dream cites the memories it came from. A critic rejects
  ungrounded or copied dreams.
- **Bring your own keys.** Each AI role (explorer, chat, dreamer, screenwriter, embeddings, search,
  fetch, video, image) has its own provider, key and daily budget. There's also a fully local, $0 profile.
- **Simulation mode.** The same code runs on a simulated clock with fake or replayed providers:
  a week of pet life in ~10 seconds, deterministic, free.

## Quick start (no keys needed)

```bash
cd backend && uv sync                              # Python 3.12–3.14
uv run dreampet sim run ../scenarios/week-one-physicist.yaml   # a simulated week in seconds
uv run dreampet demo --speed 600                   # a live pet on a fast clock, fake providers
# in another terminal
cd frontend && npm install && npm run dev          # http://localhost:3000
```

For your own pet:

```bash
cd backend
uv run dreampet init --preset naturalist --timezone Australia/Sydney   # writes dreampet.yaml, drives.yaml, .secrets
# edit dreampet.yaml: point roles at real providers (keys go in .secrets as KEY=value)
uv sync --extra cloud --extra fetch                # LangChain provider packages, trafilatura
uv run dreampet up                                 # scheduler + API on 127.0.0.1:8000
uv run dreampet chat
```

Paste the admin token from `.secrets` into the web UI's Settings screen.

Data lives in SQLite by default. To use Postgres + pgvector instead, set `DREAMPET_DATABASE_URL`
(e.g. `postgresql://user:pass@localhost:5432/dreampet`).

## How it works

```
 web UI (Next.js) ─┐                    ┌─ scheduler: lifecycle state machine (never an LLM)
 CLI (dreampet) ───┼── FastAPI /api/v1 ─┤    idle ⇄ exploring, napping, asleep → dreaming
                   │     SSE stream     └─ LangGraph graphs (checkpointed every node)
                   │                         awake_tick · explore · chat · sleep · video
                   │                                  │
                   │                  provider roles (key, budget, fake/record/replay/live)
                   └──── MemoryRepo: Postgres+pgvector │ SQLite+sqlite-vec (dev/sim)
```

| Piece | Where |
| --- | --- |
| Clock (`RealClock`, `SimClock`; no `datetime.now()` anywhere else) | `backend/dreampet/clock` |
| Drives: `dB/dt = g(1−B) − Σ s(e)`, `s = w·novelty·learnability·habituation`; LP topic choice | `backend/dreampet/drives` |
| Memory: tables, kNN, retrieval score, clustering, consolidation | `backend/dreampet/memory` |
| Roles, adapters, fakes, record/replay fixtures, budget guard | `backend/dreampet/providers` |
| Graphs | `backend/dreampet/graphs` |
| Dream weaving, critic, ffmpeg stitching | `backend/dreampet/dreams` |
| Persona traits → parameters, presets, nightly drift | `backend/dreampet/persona`, `presets/` |
| Scheduler / runtime, proactive messages, crash-resume | `backend/dreampet/scheduler` |
| Scenario runner, assertions, comparison | `backend/dreampet/sim`, `scenarios/` |
| API and CLI | `backend/dreampet/api`, `backend/dreampet/cli` |
| Web UI (Home, Chat, Journal, Dreams, Mind map, Personality, Settings, Simulation) | `frontend/` |

## CLI

```
dreampet init | up [-d] | down | demo | worker | status | chat
dreampet sim run <scenario.yaml> [--days N --seed S -p dreamer_llm=live]
dreampet sim compare <run_a> <run_b>     dreampet sim list
dreampet dream show [--night YYYY-MM-DD]  dreampet dream render <id>
dreampet fixtures record <scenario> | prune <scenarios...>
dreampet persona export|import FILE      dreampet export | wipe --yes
```

## Tuning loop

1. Change traits or drive parameters (web UI → Personality, or `drives.yaml`, hot-reloaded).
2. Run a scenario (`dreampet sim run`, or the Simulation screen).
3. Compare runs: drive curves, explorations, topic diversity, estimated real cost, dreams.
4. Promote the scenario's settings to the live pet (Simulation screen).

## Safety, cost, privacy

- Fetched pages are sanitized, length-capped and passed to models only as quoted, untrusted data;
  no node lets an LLM call tools. robots.txt, per-domain rate limits and a domain blocklist apply.
- Spending is capped in code: per-role daily budgets + a daily hard cap, checked before every
  live call; videos above a threshold wait for your approval; over-budget shots fall back to stills.
- Keys come from env vars or `.secrets`, never the DB, logs or API responses.
- Memories stay local. View, edit or forget any memory; `dreampet export` / `dreampet wipe`.

License: AGPL-3.0 (see `LICENSE`, and `CONTRIBUTING.md` for the CLA). The offline corpus is
Wikipedia text under CC BY-SA 4.0 (`corpus/README.md`).
