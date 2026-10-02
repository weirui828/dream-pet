"""Build the offline corpus used by the fake search and fetch providers.

Pulls plain-text extracts of Wikipedia articles for a spread of seed topics via the
MediaWiki API and writes them to corpus/wikipedia.jsonl.gz. Text is CC BY-SA 4.0;
see corpus/README.md for attribution.

    uv run --directory backend python ../scripts/build_corpus.py --per-topic 50
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

import httpx

API = "https://en.wikipedia.org/w/api.php"
UA = "DreamPetCorpusBuilder/0.1 (https://github.com/weirui828/dream-pet; offline sim corpus)"

TOPICS = [
    "black hole", "quantum entanglement", "dark matter", "exoplanet", "neutron star",
    "thermodynamics", "chaos theory", "superconductivity", "tardigrade", "octopus",
    "cephalopod intelligence", "coral reef", "deep sea creature", "bioluminescence",
    "fungus mycelium network", "bird migration", "whale song", "ancient Rome",
    "Byzantine Empire", "Silk Road", "Bronze Age collapse", "medieval cartography",
    "Ming dynasty treasure voyages", "Egyptian hieroglyphs", "haiku", "Romantic poetry",
    "surrealism", "impressionism", "jazz improvisation", "origami mathematics",
    "prime numbers", "topology", "cellular automaton", "cryptography history",
    "clockwork automaton", "lighthouse", "tide", "volcano", "glacier", "aurora",
    "sleep and dreaming", "memory consolidation", "circadian rhythm", "lucid dream",
    "tea culture", "fermentation", "bread baking history", "spice trade",
    "ancient astronomy", "comet", "meteor shower", "moth", "honeybee dance",
    "slime mold", "desert ecology", "rainforest canopy", "cave formation",
    "invented language", "writing system", "library of Alexandria",
]


def fetch_topic(client: httpx.Client, topic: str, per_topic: int) -> list[dict]:
    out: list[dict] = []
    params = {
        "action": "query", "format": "json", "formatversion": "2",
        "generator": "search", "gsrsearch": topic, "gsrlimit": "20", "gsrnamespace": "0",
        "prop": "extracts|info", "inprop": "url", "explaintext": "1",
        "exintro": "1", "exlimit": "20",
    }
    cont: dict = {}
    for _ in range(8):  # page through search results (gsroffset), not extract continuations
        if len(out) >= per_topic:
            break
        r = client.get(API, params={**params, **cont}, timeout=30)
        r.raise_for_status()
        data = r.json()
        for page in data.get("query", {}).get("pages", []):
            text = (page.get("extract") or "").strip()
            if len(text) < 300 or "may refer to" in text[:300]:
                continue
            out.append({
                "id": f"wiki:{page['pageid']}",
                "title": page["title"],
                "url": page.get("fullurl") or f"https://en.wikipedia.org/?curid={page['pageid']}",
                "topic": topic,
                "text": text,
            })
        offset = (data.get("continue") or {}).get("gsroffset")
        if offset is None:
            break
        cont = {"gsroffset": offset}
        time.sleep(0.2)
    return out[:per_topic]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-topic", type=int, default=50)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "corpus" / "wikipedia.jsonl.gz"))
    args = ap.parse_args()

    seen: set[str] = set()
    rows: list[dict] = []
    with httpx.Client(headers={"User-Agent": UA}) as client:
        for topic in TOPICS:
            try:
                got = fetch_topic(client, topic, args.per_topic)
            except httpx.HTTPError as exc:  # keep going; a partial corpus is still useful
                print(f"  ! {topic}: {exc}")
                continue
            new = [g for g in got if g["id"] not in seen]
            seen.update(g["id"] for g in new)
            rows.extend(new)
            print(f"{topic:32s} +{len(new):3d}  total {len(rows)}")

    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} articles to {args.out}")


if __name__ == "__main__":
    main()
