"""Deterministic fake providers for every role. Same inputs + seed -> same outputs.

They are built to give *realistic dynamics*, not just placeholder strings: embeddings share
words the way real ones share meaning, predictions improve as the pet learns a topic, and
dreams are stitched from short phrases of real memories.
"""

from __future__ import annotations

import hashlib
import io
import math
import random
import shutil
import subprocess
import textwrap
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from dreampet.providers.base import (
    ChatRequest,
    ChatResult,
    FetchedPage,
    ProviderError,
    SearchResult,
    ShotRequest,
    StructResult,
    Usage,
    VideoLimits,
    estimate_tokens,
)
from dreampet.providers.corpus import Corpus
from dreampet.schemas import (
    Critique,
    DreamDraft,
    DriftProposal,
    Insights,
    Prediction,
    QueryPlan,
    ReadNotes,
    Screenplay,
)
from dreampet.text import content_tokens, keywords, moderation_flags, sentences, truncate_words


def _seed(*parts: Any) -> int:
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big")


# ---------------------------------------------------------------------------------------------
# Embeddings


class FakeEmbeddings:
    """Signed feature hashing over content words and bigrams, plus a shared component so that
    unrelated texts sit around cosine distance 0.6 and related ones nearer 0.2-0.4, roughly like
    real sentence embeddings."""

    def __init__(self, dim: int = 256, shared: float = 0.35):
        self.dim = dim
        self.model_name = f"fake-hash-{dim}"
        self._shared = math.sqrt(shared)
        self._own = math.sqrt(1 - shared)
        rng = np.random.default_rng(12345)
        c = rng.standard_normal(dim).astype(np.float32)
        self._common = c / np.linalg.norm(c)

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = content_tokens(text)
        weights: dict[str, float] = {}
        for t in toks:
            weights[t] = weights.get(t, 0.0) + 1.0
        weights = {t: 1 + math.log(c) for t, c in weights.items()}  # sublinear tf
        for a, b in zip(toks, toks[1:], strict=False):
            weights.setdefault(a + "_" + b, 0.5)  # bigrams add a little word-order signal
        for t, w in weights.items():
            h = zlib.crc32(t.encode())
            v[h % self.dim] += w if (h >> 20) & 1 else -w
        n = np.linalg.norm(v)
        if n == 0:
            rng = np.random.default_rng(_seed(text) % (2**32))
            v = rng.standard_normal(self.dim).astype(np.float32)
            n = np.linalg.norm(v)
        v = v / n
        # remove any accidental overlap with the common direction, then mix it in
        v = v - float(v @ self._common) * self._common
        v = v / (np.linalg.norm(v) or 1.0)
        out = self._own * v + self._shared * self._common
        return (out / np.linalg.norm(out)).astype(np.float32)

    def embed(self, texts: list[str]) -> tuple[list[np.ndarray], Usage]:
        return [self._vec(t) for t in texts], Usage(tokens_in=sum(estimate_tokens(t) for t in texts))


# ---------------------------------------------------------------------------------------------
# Search and fetch


class FakeSearch:
    """TF-IDF search over the offline corpus. `boost_ids` lets a scenario flood results with
    injected articles (a trending topic for a day)."""

    def __init__(self, corpus_loader):
        self._load = corpus_loader
        self.boost_ids: list[str] = []

    @property
    def corpus(self) -> Corpus:
        return self._load()

    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        return [
            SearchResult(title=a["title"], url=a["url"], snippet=Corpus.snippet(a), source="corpus")
            for a in self.corpus.search(query, k, self.boost_ids)
        ]


class FakeFetch:
    def __init__(self, corpus_loader):
        self._load = corpus_loader

    def fetch(self, url: str) -> FetchedPage:
        a = self._load().by_url.get(url)
        if a is None:
            raise ProviderError(f"fake fetch: {url} is not in the offline corpus")
        return FetchedPage(url=url, title=a["title"], text=a["text"])


# ---------------------------------------------------------------------------------------------
# Chat


STRIKING = ("extinct", "largest", "smallest", "survive", "mysterious", "strange", "oldest", "deadly", "glow",
            "ancient", "lost", "secret", "legend", "impossible", "invisible", "dream", "giant", "collapse",
            "infinite", "paradox", "forbidden", "haunted", "space", "radiation", "shipwreck", "vanished")

SETTINGS = ("a library that breathed slowly", "an upside-down ocean", "a clocktower full of moths",
            "a train made of paper lanterns", "a greenhouse on the Moon", "a staircase that folded into itself",
            "a market where the stalls sold weather", "a lighthouse at the bottom of a well",
            "a desert of sleeping bells", "a kitchen floating in the aurora", "a museum after closing time",
            "a forest where the trees were pages")
MOODS = ("wistful", "eerie", "joyful", "tender", "restless", "awestruck", "melancholic", "playful")
VERBS = ("was humming a song about", "kept rearranging", "was trying to explain", "slowly turned into",
         "was carrying", "whispered the secret of", "was building a tiny model of", "kept losing")

TEMPERAMENT_OPENERS = {
    "playful": ("Ooh!", "Hehe,", "Guess what?", "Oh oh oh!"),
    "earnest": ("Honestly,", "I've been thinking,", "That's a good question.", "Let me tell you,"),
    "dry": ("Well.", "Naturally,", "Fun fact, apparently:", "Hm."),
    "melancholic": ("Ah...", "You know,", "It's strange,", "Sometimes I wonder..."),
}


class FakeChat:
    def __init__(self, model_name: str = "fake-llm", seed: int = 0):
        self.model_name = model_name
        self.seed = seed

    def _rng(self, req: ChatRequest) -> random.Random:
        return random.Random(_seed(self.seed, req.task, req.user))

    def generate(self, req: ChatRequest) -> ChatResult:
        fn = getattr(self, f"_text_{req.task}", None)
        text = fn(req.context, self._rng(req)) if fn else f"({req.task})"
        return ChatResult(text=text, usage=Usage(estimate_tokens(req.system + req.user), estimate_tokens(text)))

    def structured(self, req: ChatRequest, schema):
        fn = getattr(self, f"_struct_{req.task}", None)
        if fn is None:
            raise ProviderError(f"fake chat has no generator for task {req.task!r}")
        data = schema.model_validate(fn(req.context, self._rng(req)))
        out = data.model_dump_json()
        return StructResult(data=data, usage=Usage(estimate_tokens(req.system + req.user), estimate_tokens(out)))

    # ---- explorer ----------------------------------------------------------------------------

    def _struct_plan_queries(self, ctx: dict, rng: random.Random) -> dict:
        label = ctx.get("label", "something new")
        facts = " ".join(ctx.get("facts", []))
        kws = keywords(facts, 6, exclude=set(content_tokens(label))) if facts else []
        rng.shuffle(kws)
        queries = [label]
        if kws:
            queries.append(f"{label} {kws[0]}")
        if len(kws) > 1 and ctx.get("n", 2) > 2:
            queries.append(f"{kws[1]} {label}")
        questions = [f"What is most surprising about {label}?"]
        if kws:
            questions.append(f"How is {kws[0]} connected to {label}?")
        return QueryPlan(questions=questions, queries=queries[: max(1, ctx.get("n", 2))]).model_dump()

    def _struct_predict(self, ctx: dict, rng: random.Random) -> dict:
        title = ctx.get("title", "")
        known = ctx.get("known", [])
        if known:
            best = sentences(known[0])[:2]
            gist = f"{title}. " + " ".join(best)
        else:
            gist = f"{title} is probably about {ctx.get('topic', title).lower()}."
        return Prediction(gist=truncate_words(gist, 60)).model_dump()

    def _struct_read(self, ctx: dict, rng: random.Random) -> dict:
        text = ctx.get("text", "")
        sents = sentences(text)
        summary = truncate_words(" ".join(sents[:3]), 70) if sents else truncate_words(text, 70)
        facts = [truncate_words(s, 22) for s in sents[3:7]]
        low = text.lower()
        strike = sum(low.count(w) for w in STRIKING)
        salience = min(1.0, 0.15 + 0.12 * strike + rng.random() * 0.2)
        importance = min(1.0, 0.3 + 0.1 * len(keywords(text, 12)) / 3 + rng.random() * 0.2)
        return ReadNotes(summary=summary, facts=facts, importance=round(importance, 3),
                         salience=round(salience, 3), safe=not moderation_flags(text)).model_dump()

    # ---- chat --------------------------------------------------------------------------------

    def _text_chat(self, ctx: dict, rng: random.Random) -> str:
        msg = ctx.get("message", "")
        recalled = ctx.get("recalled", [])
        temperament = ctx.get("temperament", "playful")
        opener = rng.choice(TEMPERAMENT_OPENERS.get(temperament, TEMPERAMENT_OPENERS["playful"]))
        if ctx.get("mode") == "grumpy":
            opener = "*rubs eyes* You woke me up..."
        if recalled:
            r = recalled[0]
            fact = truncate_words(sentences(r["content"])[0] if sentences(r["content"]) else r["content"], 28)
            body = f"{opener} That reminds me of something I read about {r.get('title') or 'recently'}: {fact}"
            if len(recalled) > 1 and not ctx.get("tired"):
                body += f" It also connects to {recalled[1].get('title') or 'another thing I learned'}, somehow."
        else:
            kw = keywords(msg, 2)
            topic = kw[0] if kw else "that"
            body = f"{opener} I don't know much about {topic} yet. Maybe I'll go read about it when I get bored!"
        if ctx.get("tired"):
            body = "*yawn* " + truncate_words(body, 18) + " ...so sleepy."
        if ctx.get("question_back") and not ctx.get("tired"):
            body += f" What made you think of that, {ctx.get('owner', 'friend')}?"
        return body

    def _text_sleeptalk(self, ctx: dict, rng: random.Random) -> str:
        phrases = ctx.get("phrases") or ["the tide", "little lights"]
        a = rng.choice(phrases)
        b = rng.choice(phrases)
        return f"mmm... {a}... no, the {b.lower()} goes on the other side... zzz"

    def _text_proactive(self, ctx: dict, rng: random.Random) -> str:
        trig = ctx.get("trigger")
        d = ctx.get("detail", {})
        if trig == "wake_dream":
            return f"Good morning! I dreamed {d.get('first_scene', 'something strange')}… It was called “{d.get('title', 'a dream')}”. Want to see it?"
        if trig == "finding":
            fact = truncate_words(d.get("fact", ""), 30)
            return f"Did you know? {fact} I just read about {d.get('title', 'it')}!"
        if trig == "silence":
            return "Haven't heard from you in a while. Want to hear what I learned?"
        if trig == "drift":
            return f"I think I'm getting into {d.get('topic', 'something new')}."
        return "Hi!"

    # ---- sleep -------------------------------------------------------------------------------

    def _struct_reflect(self, ctx: dict, rng: random.Random) -> dict:
        out = []
        for g in ctx.get("groups", []):
            mems = g["memories"]
            if len(mems) < 2:
                continue
            kws = keywords(" ".join(m["content"] for m in mems), 4, exclude=set(content_tokens(g["label"])))
            joined = ", ".join(kws[:3]) if kws else "several details"
            text = f"Today I kept circling back to {g['label']}. The threads were {joined}; they seem more connected than I expected."
            out.append({"text": text, "memory_ids": [m["id"] for m in mems]})
        return Insights.model_validate({"insights": out}).model_dump()

    def _struct_dream_weave(self, ctx: dict, rng: random.Random) -> dict:
        frags = ctx.get("fragments", [])
        if not frags:
            raise ProviderError("no fragments to dream about")
        mood = ctx.get("mood_hint") or rng.choice(MOODS)
        attempt = int(ctx.get("attempt", 0))
        rng.seed(_seed(rng.random(), attempt))
        n_scenes = max(3, min(6, len(frags) if len(frags) >= 3 else 3))
        order = list(range(len(frags)))
        rng.shuffle(order)
        elements = []
        settings = list(SETTINGS)
        rng.shuffle(settings)
        for i in range(n_scenes):
            a = frags[order[i % len(frags)]]
            b = frags[order[(i + 1) % len(frags)]]
            pa = rng.choice(a.get("phrases") or [a.get("title") or "something"])
            pb = rng.choice(b.get("phrases") or [b.get("title") or "something"])
            setting = settings[i % len(settings)]
            verb = rng.choice(VERBS)
            templates = [
                f"I was in {setting}, where {pa} {verb} {pb.lower()}.",
                f"Somewhere in {setting}, {pa} and {pb} were the same thing, and nobody found it odd.",
                f"I followed {pa} into {setting}; the walls were written with {pb.lower()}.",
                f"{pa} {verb} {pb.lower()}, and I understood it perfectly until I tried to say it out loud.",
            ]
            text = rng.choice(templates)
            ids = [a["id"]] if a["id"] == b["id"] else [a["id"], b["id"]]
            elements.append({"text": text, "memory_ids": ids, "image_hint": f"{pa} and {pb} in {setting}"})
        name = ctx.get("name", "I")
        opening = rng.choice([
            "It started quietly, the way my dreams usually do.",
            "At first I thought I was still awake.",
            "The light was the colour of old tea.",
        ])
        closing = rng.choice([
            "When I woke up I could still hear it, very faintly.",
            "Then everything folded up like a letter and I woke.",
            "I tried to hold on to the last image, but it slipped away like water.",
        ])
        middle = []
        for el in elements:
            middle.append(el["text"])
            middle.append(rng.choice([
                "It felt important, though I couldn't say why.",
                "Everything smelled faintly of rain.",
                "Someone kept counting softly behind me.",
                "The colours kept changing whenever I blinked.",
                "I wasn't afraid, only curious.",
            ]))
        narrative = " ".join([opening, *middle, closing])
        filler = [
            f"I remember thinking that {name} should write this down.",
            "Time moved sideways for a while, and the scenes blurred into each other.",
            "There was a sound like pages turning in a very large room.",
            "Somewhere far off, a bell rang once and then forgot how.",
            "Every door I opened led back to the same small, warm room.",
            "The floor was soft, like walking on the back of a sleeping animal.",
            "I kept almost remembering a word that meant all of it at once.",
            "The air tasted of copper and cold rain.",
        ]
        rng.shuffle(filler)
        for line in filler:
            if len(narrative.split()) >= 150:
                break
            narrative += " " + line
        narrative = truncate_words(narrative, 400)
        first = frags[order[0]]
        title_word = (first.get("phrases") or [first.get("title") or "night"])[0]
        title = f"The {rng.choice(['clockwork', 'drifting', 'silent', 'glowing', 'folded', 'upside-down'])} {title_word.lower()}"
        return DreamDraft.model_validate({"title": title, "mood": mood, "narrative": narrative,
                                          "elements": elements}).model_dump()

    def _struct_dream_critic(self, ctx: dict, rng: random.Random) -> dict:
        n = int(ctx.get("n_elements", 3))
        grounded = float(ctx.get("grounded_ratio", 1.0))
        score = min(1.0, 0.35 + 0.08 * n + 0.2 * grounded + rng.random() * 0.15)
        return Critique(score=round(score, 3), notes="vivid enough").model_dump()

    def _struct_persona_drift(self, ctx: dict, rng: random.Random) -> dict:
        changes = []
        top = ctx.get("top_clusters", [])
        interests = {i["topic"].lower(): i["weight"] for i in ctx.get("interests", [])}
        if top:
            c = top[0]
            topic = c["label"]
            delta = 2.0 if topic.lower() in interests else 3.0
            changes.append({"field": f"interests.{topic}", "delta": delta,
                            "reason": f"I spent a lot of today reading about {topic} and it kept pulling me back.",
                            "memory_ids": c["memory_ids"][:4]})
        stale = [t for t, w in interests.items() if t not in {x["label"].lower() for x in top}]
        if stale and rng.random() < 0.3:
            t = rng.choice(sorted(stale))
            changes.append({"field": f"interests.{t}", "delta": -1.0,
                            "reason": f"I haven't thought about {t} in a while.",
                            "memory_ids": (top[0]["memory_ids"][:1] if top else [])})
        if ctx.get("new_clusters", 0) >= 2 and rng.random() < 0.5:
            changes.append({"field": "traits.openness", "delta": 1.0,
                            "reason": "I wandered into several new topics today and liked it.",
                            "memory_ids": (top[0]["memory_ids"][:2] if top else [])})
        return DriftProposal.model_validate({"changes": changes}).model_dump()

    # ---- video -------------------------------------------------------------------------------

    def _struct_screenwrite(self, ctx: dict, rng: random.Random) -> dict:
        dream = ctx["dream"]
        style = ctx["style"]
        n = int(ctx.get("shots", 3))
        secs = float(ctx.get("shot_seconds", 5))
        els = dream["elements"]
        shots = []
        for i in range(n):
            idx = min(len(els) - 1, round(i * (len(els) - 1) / max(1, n - 1))) if len(els) > 1 else 0
            el = els[idx]
            shots.append({
                "id": i + 1, "seconds": secs,
                "prompt": f"{el.get('image_hint') or el['text']}, dreamlike, {style['camera']}",
                "negative": "text, watermark, logo, real people, brand names",
                "element_ref": idx, "transition": "dissolve" if i else "fade",
            })
        return Screenplay.model_validate({
            "title": dream["title"], "style_bible": style, "total_seconds": secs * n, "shots": shots,
            "narration": None,
        }).model_dump()


# ---------------------------------------------------------------------------------------------
# Image and video


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def title_card(prompt: str, width: int, height: int, seed: int, palette: list[str] | None = None) -> Image.Image:
    rng = random.Random(seed)
    pal = [_hex(p) for p in (palette or [])] or [
        (rng.randrange(20, 120), rng.randrange(20, 120), rng.randrange(60, 160)),
        (rng.randrange(150, 250), rng.randrange(120, 220), rng.randrange(80, 200)),
    ]
    a, b = pal[0], pal[-1] if len(pal) > 1 else (240, 200, 150)
    t = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    grad = (np.array(a, np.float32) * (1 - t) + np.array(b, np.float32) * t).astype(np.uint8)
    img = Image.fromarray(np.repeat(grad, width, axis=1), "RGB")
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(14):
        r = rng.randrange(height // 12, height // 3)
        cx, cy = rng.randrange(width), rng.randrange(height)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, rng.randrange(12, 40)))
    font = _font(max(14, height // 18))
    lines = textwrap.wrap(prompt, width=max(20, width // max(8, height // 30)))[:5]
    y = height // 2 - len(lines) * (height // 16) // 2
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((width - w) / 2 + 2, y + 2), line, font=font, fill=(0, 0, 0, 120))
        d.text(((width - w) / 2, y), line, font=font, fill=(255, 255, 255, 235))
        y += height // 16
    return img


class FakeImage:
    model_name = "fake-cards"

    def __init__(self, palette: list[str] | None = None):
        self.palette = palette

    def generate(self, prompt: str, width: int, height: int, n: int = 1, seed: int = 0) -> list[bytes]:
        out = []
        for i in range(n):
            img = title_card(prompt, width, height, _seed(prompt, seed, i), self.palette)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            out.append(buf.getvalue())
        return out


class FakeVideo:
    """Returns colored title cards rendered to MP4 with ffmpeg (a slow zoom so it's a real clip)."""

    model_name = "fake-cards"

    def __init__(self, palette: list[str] | None = None, fps: int = 12):
        self.limits = VideoLimits(max_shot_seconds=6, min_shot_seconds=2, max_total_seconds=60)
        self.palette = palette
        self.fps = fps
        self._jobs: dict[str, ShotRequest] = {}

    def submit(self, shot: ShotRequest) -> str:
        jid = f"fake-{_seed(shot.prompt, shot.seed, shot.seconds) & 0xFFFFFFFF:08x}"
        self._jobs[jid] = shot
        return jid

    def poll(self, job_id: str) -> str:
        return "done" if job_id in self._jobs else "failed"

    def download(self, job_id: str, dest: Path) -> Path:
        shot = self._jobs.get(job_id)
        if shot is None:
            raise ProviderError("unknown fake video job")
        if not shutil.which("ffmpeg"):
            raise ProviderError("ffmpeg not found")
        img = title_card(shot.prompt, shot.width, shot.height, _seed(job_id), self.palette)
        png = dest.with_suffix(".png")
        img.save(png)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-t", f"{shot.seconds:.2f}",
            "-vf", f"scale={shot.width}:{shot.height},format=yuv420p", "-r", str(self.fps),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        png.unlink(missing_ok=True)
        if r.returncode != 0:
            raise ProviderError(f"ffmpeg failed: {r.stderr[-400:]}")
        return dest
