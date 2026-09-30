# Offline corpus

`wikipedia.jsonl.gz` backs the **fake** `search` and `fetch` providers, so simulations get
realistic exploration dynamics with no network and no cost. It holds ~2,300 plain-text
introductions of English Wikipedia articles across 60 seed topics (physics, sea life, history,
poetry, astronomy, food, languages…), one JSON object per line:

```json
{"id": "wiki:123", "title": "Tardigrade", "url": "https://en.wikipedia.org/wiki/Tardigrade", "topic": "tardigrade", "text": "..."}
```

Rebuild or extend it with `scripts/build_corpus.py` (edit `TOPICS` there).

Text is from Wikipedia, © its contributors, licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Each record keeps its source URL
for attribution. The corpus is data, not part of the AGPL-licensed code.
