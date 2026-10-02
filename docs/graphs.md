# Agent graphs

Every agent in Dream Pet is a LangGraph `StateGraph` in [`backend/dreampet/graphs`](../backend/dreampet/graphs). The scheduler picks which graph to run. It's a plain state machine, never an LLM. Graphs that do long or costly work are checkpointed after every node, so a crash resumes mid-run.

| Graph | Runs when | Checkpointed |
| --- | --- | --- |
| [awake_tick](#awake_tick) | every scheduler tick while awake | no |
| [explore](#explore) | the drives say "go look something up" | yes |
| [chat](#chat) | the owner sends a message (one thread per conversation) | yes |
| [proactive](#proactive) | the pet has something to say first | no |
| [sleep](#sleep-and-dream-weaving) | bedtime | yes |
| [video](#video) | a saved dream is rendered | yes |

Edge labels describe the routing condition. Rounded nodes are start and end.

## awake_tick

Steps the drives forward and then routes using fixed thresholds only, with no LLM call. The scheduler acts on `next`. ([`awake_tick.py`](../backend/dreampet/graphs/awake_tick.py))

```mermaid
flowchart LR
    S((start)) --> update_drives["<b>update_drives</b><br/>step dB/dt"] --> route["<b>route</b><br/>bedtime → sleep · low energy → nap<br/>curious → explore · else idle"] --> E((end))
```

## explore

The pet picks something it is curious about, guesses what a page will say before reading it, and learns most from what surprised it. Each read changes its drives, and the drives decide whether it keeps going, switches topic, or stops. ([`explore.py`](../backend/dreampet/graphs/explore.py))

```mermaid
flowchart TD
    S((start)) --> pick_topic
    pick_topic["<b>pick_topic</b><br/>softmax over learning progress, ε-wildcard"] --> plan_queries["<b>plan_queries</b><br/>LLM → structured QueryPlan"]
    plan_queries --> search
    search --> select_sources["<b>select_sources</b><br/>blocklist · dedupe · aversion · moderation"]
    select_sources -- sources found --> fetch_and_sanitize
    select_sources -- nothing new --> pick_topic
    select_sources -- out of topics --> finish
    fetch_and_sanitize -- page --> predict_then_read["<b>predict_then_read</b><br/>guess the gist from the title, then read"]
    fetch_and_sanitize -- fetch failed --> update_drives
    predict_then_read -- notes --> memorize["<b>memorize</b><br/>prediction error → novelty · learnability · habituation"]
    predict_then_read -- unsafe / provider error --> update_drives
    memorize --> update_drives["<b>update_drives</b><br/>step dB/dt, decide what's next"]
    update_drives -- more in queue --> fetch_and_sanitize
    update_drives -- new topic --> pick_topic
    update_drives -- tired · satisfied · cap · bedtime --> finish
    finish --> E((end))
```

## chat

One reply per invoke. The reply style depends on `mode` (awake, tired, grumpy after being woken, or sleep-talking). Each turn is stored as a memory and counts as a drive event, so chatting relieves boredom. ([`chat.py`](../backend/dreampet/graphs/chat.py))

```mermaid
flowchart TD
    S((start)) --> load_context
    load_context --> retrieve_memories["<b>retrieve_memories</b><br/>kNN + retrieval score · recent memories when asleep"]
    retrieve_memories --> respond_in_persona["<b>respond_in_persona</b><br/>persona prompt shaped by mode"]
    respond_in_persona --> log_event["<b>log_event</b><br/>store messages + chat memory, novelty · habituation"]
    log_event --> update_drives
    update_drives --> E((end))
```

## proactive

Drafts a message the pet sends first, triggered by waking from a dream, a surprising finding, a long silence, or a new interest. The scheduler decides when to send it. ([`proactive.py`](../backend/dreampet/graphs/proactive.py))

```mermaid
flowchart LR
    S((start)) --> draft_message["<b>draft_message</b><br/>wake_dream · finding · silence · drift"] --> E((end))
```

## sleep and dream-weaving

At night the pet consolidates the day into memory and reclusters it. It then weaves a dream from an associative walk over those memories. A critic checks that every scene is grounded in a real memory, and a rejected draft goes back to the weaver along with the critic's issues (up to 2 retries). ([`sleep.py`](../backend/dreampet/graphs/sleep.py), [`dreams/weave.py`](../backend/dreampet/dreams/weave.py))

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

## video

Renders a saved dream as a short film, on a best-effort basis. If the estimate exceeds the approval threshold, the graph pauses with a human-in-the-loop `interrupt` until someone approves or rejects it. Shots are generated in parallel through `Send` fan-out. Shots that go over budget or fail after one retry, and all shots in a rejected render, fall back to stills with a Ken Burns pan. With no ffmpeg, or if nothing rendered, the dream stays text-only. ([`video.py`](../backend/dreampet/graphs/video.py))

```mermaid
flowchart TD
    S((start)) --> screenwrite["<b>screenwrite</b><br/>LLM → screenplay, clamp to limits, budget plan"]
    screenwrite --> approve["<b>approve</b><br/>interrupt() if over the cost threshold"]
    approve -- "Send × N shots" --> generate_shot["<b>generate_shot</b> (parallel)<br/>video, retry once → still + Ken Burns"]
    generate_shot --> stitch["<b>stitch</b><br/>ffmpeg normalize · crossfade · poster"]
    stitch --> store["<b>store</b><br/>shot → scene → memory map, cost"]
    store --> E((end))
```
