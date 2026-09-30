"""The offline corpus behind fake search and fetch: a TF-IDF index over Wikipedia extracts."""

from __future__ import annotations

import fnmatch
import gzip
import json
import math
import threading
from collections import Counter, defaultdict
from pathlib import Path

from dreampet.text import content_tokens, sentences, truncate_words

_FALLBACK = [
    {"id": "fb:1", "title": "Tardigrade", "topic": "tardigrade", "url": "https://en.wikipedia.org/wiki/Tardigrade",
     "text": "Tardigrades are eight-legged micro-animals that can survive extreme conditions. They survive "
             "exposure to the vacuum of space and to intense radiation. In a dried state called a tun they can "
             "live for decades without water."},
    {"id": "fb:2", "title": "Black hole", "topic": "black hole", "url": "https://en.wikipedia.org/wiki/Black_hole",
     "text": "A black hole is a region of spacetime where gravity is so strong that nothing, not even light, can "
             "escape. The boundary is called the event horizon. Hawking radiation suggests black holes slowly evaporate."},
    {"id": "fb:3", "title": "Octopus", "topic": "octopus", "url": "https://en.wikipedia.org/wiki/Octopus",
     "text": "The octopus is a soft-bodied cephalopod with eight arms. Octopuses are highly intelligent and can "
             "solve puzzles, use tools, and change colour to camouflage themselves. Each arm has its own cluster of neurons."},
    {"id": "fb:4", "title": "Tide", "topic": "tide", "url": "https://en.wikipedia.org/wiki/Tide",
     "text": "Tides are the rise and fall of sea levels caused by the gravitational forces of the Moon and the Sun. "
             "Tidal patterns vary by coastline. Ancient sailors kept tide tables long before Newton explained them."},
    {"id": "fb:5", "title": "Haiku", "topic": "haiku", "url": "https://en.wikipedia.org/wiki/Haiku",
     "text": "Haiku is a short form of Japanese poetry with three phrases in a 5, 7, 5 pattern. Matsuo Basho is its "
             "most famous master. Haiku often contain a seasonal reference called a kigo."},
    {"id": "fb:6", "title": "Clockwork", "topic": "clockwork automaton", "url": "https://en.wikipedia.org/wiki/Clockwork",
     "text": "Clockwork is the inner workings of mechanical devices driven by a wound spring. Automata built with "
             "clockwork could write letters and play music in eighteenth century Europe."},
]


class Corpus:
    def __init__(self, articles: list[dict]):
        self.articles = articles
        self.by_url = {a["url"]: a for a in articles}
        self.by_id = {a["id"]: a for a in articles}
        self._postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        df: Counter[str] = Counter()
        tfs = []
        for a in articles:
            toks = content_tokens(a["title"]) * 3 + content_tokens(a["text"][:2500])
            tf = Counter(toks)
            tfs.append(tf)
            df.update(tf.keys())
        n = len(articles)
        self.idf = {t: math.log(1 + n / (1 + c)) for t, c in df.items()}
        for i, tf in enumerate(tfs):
            norm = math.sqrt(sum((1 + math.log(c)) ** 2 for c in tf.values())) or 1.0
            for t, c in tf.items():
                self._postings[t].append((i, (1 + math.log(c)) / norm))

    def search(self, query: str, k: int = 5, boost_ids: list[str] | None = None) -> list[dict]:
        scores: dict[int, float] = defaultdict(float)
        for t in sorted(set(content_tokens(query))):
            idf = self.idf.get(t)
            if not idf:
                continue
            for i, w in self._postings[t]:
                scores[i] += w * idf
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        out = [self.articles[i] for i, _ in ranked[:k]]
        boost = [self.by_id[b] for b in (boost_ids or []) if b in self.by_id]
        if boost:
            # injected articles flood the results, like a trending topic that day
            seen = {a["id"] for a in boost[:k]}
            out = boost[: max(1, k // 2) + 1] + [a for a in out if a["id"] not in seen]
            out = out[:k]
        return out

    def match(self, pattern: str) -> list[str]:
        """`corpus/<topic>/*` selects a topic; anything else is a glob on titles."""
        parts = pattern.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "corpus":
            topic_glob = parts[1].replace("_", " ")
            return [a["id"] for a in self.articles if fnmatch.fnmatch(a.get("topic", "").lower(), topic_glob.lower())
                    or fnmatch.fnmatch(a["title"].lower(), topic_glob.lower() + "*")]
        return [a["id"] for a in self.articles if fnmatch.fnmatch(a["title"].lower(), pattern.lower())]

    @staticmethod
    def snippet(a: dict) -> str:
        s = sentences(a["text"])
        return truncate_words(" ".join(s[:2]), 40) if s else ""


_CACHE: dict[str, Corpus] = {}
_CACHE_LOCK = threading.Lock()


def load_corpus(path: Path) -> Corpus:
    key = str(path)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
        articles: list[dict] = []
        if path.exists():
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        articles.append(json.loads(line))
        if not articles:
            articles = list(_FALLBACK)
        corpus = Corpus(articles)
        _CACHE[key] = corpus
        return corpus
