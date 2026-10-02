# Dream Pet

An open-source, self-hosted AI companion that **gets bored**, **explores the web to learn**,
**chats** with you, and at night **consolidates its memories into a dream** — plus a short dream
video. Single user, bring your own keys, runs on your machine.

![Home: drives, the last 24 hours, last night's dream and recent activity](docs/screenshots/home.png)

- **An inner life you can measure.** Boredom, curiosity and energy are real numbers driven by
  published ideas (kNN novelty, learning progress, habituation), not vibes. You can tune all of them.
- **Grounded dreams.** Every scene in a dream cites the memories it came from. A critic rejects
  ungrounded or copied dreams.
- **Bring your own keys.** Each AI role (explorer, chat, dreamer, screenwriter, embeddings, search,
  fetch, video, image) has its own provider, key and daily budget. There's also a fully local, $0 profile.
- **Simulation mode.** The same code runs on a simulated clock with fake or replayed providers:
  a week of pet life in ~10 seconds, deterministic, free.

## Screenshots

| | |
| --- | --- |
| ![Mind map: topics it has read about, sized by memories and coloured by learning progress](docs/screenshots/mindmap.png) | ![Dreams: a nightly dream with its video, shots and grounded story](docs/screenshots/dreams.png) |
| **Mind map:** topics it has read about, sized by memories, coloured by learning progress | **Dreams:** each night's dream with its video and the memories behind every scene |
| ![Journal: what it learned, where it read it and how surprising it was](docs/screenshots/journal.png) | ![Chat: replies cite the memories and dreams they draw on](docs/screenshots/chat.png) |
| **Journal:** what it learned, where, and how surprising it was | **Chat:** replies cite the memories and dreams they draw on |
| ![Personality: trait sliders, presets and identity](docs/screenshots/personality.png) | |
| **Personality:** trait sliders and presets that compile into drive parameters | |

Taken from `dreampet demo`, which uses fake providers, so the text is stitched from the offline
Wikipedia corpus rather than written by a real model.

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

A plain state machine (never an LLM) moves the pet through its day. Everything it *does* is a
LangGraph agent. The long-running ones are checkpointed after every node, so a crash or restart
picks up in the middle of a thought rather than starting over.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> idle
    idle --> exploring: bored
    exploring --> idle: satisfied
    exploring --> napping: tired
    idle --> napping: low energy
    napping --> idle: rested
    idle --> asleep: bedtime
    napping --> asleep: bedtime
    asleep --> dreaming: sleep graph
    dreaming --> asleep: dream over
    asleep --> idle: morning
```

You can chat at any time. A tired pet answers in one sleepy line, a sleeping one talks in its
sleep or wakes up grumpy, and a pet that just read something surprising may message you first.

### By day: curiosity as a control loop

Boredom rises on its own and only real learning brings it down. When it
crosses a threshold, the explore agent goes looking:

- **It chooses topics it's learning fastest in.** It favours topics where its understanding is
  improving, not ones it has already mastered or can't make sense of, with an occasional random
  pick so it doesn't get stuck.
- **It predicts, then reads.** Before reading a page it guesses the gist from the title. Pages that
  surprise it, and that are new to it, satisfy its curiosity most.
- **The drives decide when to stop.** After each read it updates its drives and then keeps reading,
  switches topic, or stops because it is tired, satisfied, out of reads or it's bedtime.

```mermaid
flowchart TD
    S((start)) --> pick_topic
    pick_topic["<b>pick_topic</b><br/>where am I learning fastest?"] --> plan_queries["<b>plan_queries</b><br/>LLM → structured QueryPlan"]
    plan_queries --> search
    search --> select_sources["<b>select_sources</b><br/>blocklist · dedupe · aversion · moderation"]
    select_sources -- sources found --> fetch_and_sanitize
    select_sources -- nothing new --> pick_topic
    select_sources -- out of topics --> finish
    fetch_and_sanitize -- page --> predict_then_read["<b>predict_then_read</b><br/>guess the gist from the title, then read"]
    fetch_and_sanitize -- fetch failed --> update_drives
    predict_then_read -- notes --> memorize["<b>memorize</b><br/>how surprising and new was it?"]
    predict_then_read -- unsafe / provider error --> update_drives
    memorize --> update_drives["<b>update_drives</b><br/>update boredom and energy, decide what's next"]
    update_drives -- more in queue --> fetch_and_sanitize
    update_drives -- new topic --> pick_topic
    update_drives -- tired · satisfied · cap · bedtime --> finish
    finish --> E((end))
```

<sub>[`graphs/explore.py`](backend/dreampet/graphs/explore.py)</sub>

### By night: consolidation and grounded dreams

At bedtime the day's memories are reflected into insights, deduplicated, decayed and reclustered.
Then the pet dreams:

- **Associative walk.** Salient memories seed a walk along memory links and loosely related
  embedding neighbours, which strings together fragments that never happened together.
- **Weaver and critic loop.** The dreamer LLM writes 3–6 scenes, each citing the memory IDs it
  draws on. A critic rejects drafts with uncited scenes or text copied from sources, and its issues
  go back to the weaver for another try (up to 2 retries).
- **The dream changes the pet.** The day's dominant topics nudge its interests, so its personality
  drifts slowly over the nights.

```mermaid
flowchart TD
    S((start)) --> sample_day --> consolidate["<b>consolidate</b><br/>reflect → insights · merge duplicates · decay"]
    consolidate --> recluster
    recluster --> dream_weave["<b>dream_weave</b><br/>seed memories → associative walk → dreamer LLM"]
    dream_weave -- draft --> dream_critic["<b>dream_critic</b><br/>grounding + shape checks, LLM score"]
    dream_weave -- no memories yet --> persona_drift
    dream_critic -- "rejected, retries left (issues fed back)" --> dream_weave
    dream_critic -- accepted / out of retries --> persist_dream["<b>persist_dream</b><br/>repair weak drafts, link cited memories"]
    persist_dream --> persona_drift["<b>persona_drift</b><br/>day's top clusters nudge interests"]
    persona_drift --> E((end))
```

<sub>[`graphs/sleep.py`](backend/dreampet/graphs/sleep.py) · [`dreams/weave.py`](backend/dreampet/dreams/weave.py)</sub>

### And the rest

- **chat**: retrieves memories, replies in persona and mood, then logs the turn as a memory and a drive event.
- **video**: screenplay → human approval (`interrupt`) if over budget → shots rendered in parallel
  (`Send`) → ffmpeg stitch, with stills as a fallback.
- **awake_tick** and **proactive**: threshold routing and first messages.

All six graphs are diagrammed in [docs/graphs.md](docs/graphs.md).

<details>
<summary><b>Architecture and code map</b></summary>

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
| Drives (boredom, curiosity, energy) and topic choice | `backend/dreampet/drives` |
| Memory: tables, kNN, retrieval score, clustering, consolidation | `backend/dreampet/memory` |
| Roles, adapters, fakes, record/replay fixtures, budget guard | `backend/dreampet/providers` |
| Graphs (diagrams of all six: [docs/graphs.md](docs/graphs.md)) | `backend/dreampet/graphs` |
| Dream weaving, critic, ffmpeg stitching | `backend/dreampet/dreams` |
| Persona traits → parameters, presets, nightly drift | `backend/dreampet/persona`, `presets/` |
| Scheduler / runtime, proactive messages, crash-resume | `backend/dreampet/scheduler` |
| Scenario runner, assertions, comparison | `backend/dreampet/sim`, `scenarios/` |
| API and CLI | `backend/dreampet/api`, `backend/dreampet/cli` |
| Web UI (Home, Chat, Journal, Dreams, Mind map, Personality, Settings, Simulation) | `frontend/` |

</details>

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

License: AGPL-3.0 (see `LICENSE`). Contributions are made under the [CLA](CLA.md); see `CONTRIBUTING.md`. The offline corpus is
Wikipedia text under CC BY-SA 4.0 (`corpus/README.md`).
