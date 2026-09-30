"""Video and image adapters: submit / poll / download."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from dreampet.config import RoleConfig
from dreampet.providers.base import ProviderError, ShotRequest, VideoLimits

TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def _download(http: httpx.Client, url: str, dest: Path) -> Path:
    with http.stream("GET", url) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest


def _find_url(obj: Any, exts=(".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp")) -> str | None:
    """Providers nest output URLs differently; find the first media URL anywhere in the payload."""
    if isinstance(obj, str):
        return obj if obj.startswith("http") and any(e in obj.lower() for e in exts) else None
    if isinstance(obj, dict):
        for k in ("video", "url", "output", "images", "image"):
            if k in obj:
                u = _find_url(obj[k], exts)
                if u:
                    return u
        for v in obj.values():
            u = _find_url(v, exts)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _find_url(v, exts)
            if u:
                return u
    return None


class FalVideo:
    """fal.ai queue API: POST queue.fal.run/{model} -> request_id; GET status; GET response."""

    def __init__(self, rc: RoleConfig):
        if not rc.api_key or not rc.model:
            raise ProviderError("fal video needs api_key and model")
        self.model_name = f"fal:{rc.model}"
        self.model = rc.model
        self.http = httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Key {rc.api_key}"})
        extra = rc.extra_options()
        self.limits = VideoLimits(max_shot_seconds=float(extra.get("max_shot_seconds", 5)),
                                  min_shot_seconds=float(extra.get("min_shot_seconds", 2)),
                                  max_total_seconds=float(rc.max_seconds or 15))
        self.duration_param = extra.get("duration_param", "duration")
        self.extra_input = extra.get("input", {})
        self._urls: dict[str, dict[str, str]] = {}

    def submit(self, shot: ShotRequest) -> str:
        body = {"prompt": shot.prompt, "negative_prompt": shot.negative, self.duration_param: str(int(round(shot.seconds))),
                "aspect_ratio": "16:9", "seed": shot.seed, **self.extra_input}
        try:
            r = self.http.post(f"https://queue.fal.run/{self.model}", json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"fal submit: {exc}") from exc
        d = r.json()
        rid = d["request_id"]
        self._urls[rid] = {"status": d.get("status_url"), "response": d.get("response_url")}
        return rid

    def poll(self, job_id: str) -> str:
        url = self._urls.get(job_id, {}).get("status") or f"https://queue.fal.run/{self.model}/requests/{job_id}/status"
        try:
            r = self.http.get(url)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"fal poll: {exc}") from exc
        s = r.json().get("status", "")
        return {"IN_QUEUE": "queued", "IN_PROGRESS": "running", "COMPLETED": "done"}.get(s, "failed" if s else "running")

    def download(self, job_id: str, dest: Path) -> Path:
        url = self._urls.get(job_id, {}).get("response") or f"https://queue.fal.run/{self.model}/requests/{job_id}"
        try:
            r = self.http.get(url)
            r.raise_for_status()
            media = _find_url(r.json())
            if not media:
                raise ProviderError("fal: no video url in response")
            return _download(self.http, media, dest)
        except httpx.HTTPError as exc:
            raise ProviderError(f"fal download: {exc}") from exc


class ReplicateVideo:
    def __init__(self, rc: RoleConfig):
        if not rc.api_key or not rc.model:
            raise ProviderError("replicate video needs api_key and model (owner/name)")
        self.model_name = f"replicate:{rc.model}"
        self.model = rc.model
        self.http = httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Bearer {rc.api_key}"})
        extra = rc.extra_options()
        self.limits = VideoLimits(max_shot_seconds=float(extra.get("max_shot_seconds", 5)),
                                  max_total_seconds=float(rc.max_seconds or 15))
        self.extra_input = extra.get("input", {})
        self._out: dict[str, Any] = {}

    def submit(self, shot: ShotRequest) -> str:
        body = {"input": {"prompt": shot.prompt, "negative_prompt": shot.negative, "seed": shot.seed, **self.extra_input}}
        try:
            r = self.http.post(f"https://api.replicate.com/v1/models/{self.model}/predictions", json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"replicate submit: {exc}") from exc
        return r.json()["id"]

    def poll(self, job_id: str) -> str:
        try:
            r = self.http.get(f"https://api.replicate.com/v1/predictions/{job_id}")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"replicate poll: {exc}") from exc
        d = r.json()
        self._out[job_id] = d.get("output")
        return {"starting": "queued", "processing": "running", "succeeded": "done"}.get(d.get("status"), "failed")

    def download(self, job_id: str, dest: Path) -> Path:
        media = _find_url(self._out.get(job_id))
        if not media:
            raise ProviderError("replicate: no output url")
        return _download(self.http, media, dest)


class ComfyUIVideo:
    """Local ComfyUI. `workflow` (extra option) is a path to an API-format workflow JSON with
    the placeholders {{prompt}}, {{negative}}, {{seconds}}, {{frames}}, {{seed}}, {{width}}, {{height}}."""

    def __init__(self, rc: RoleConfig):
        extra = rc.extra_options()
        self.url = (rc.url or "http://127.0.0.1:8188").rstrip("/")
        wf = extra.get("workflow")
        if not wf:
            raise ProviderError("comfyui needs a `workflow` path (API-format JSON)")
        self.workflow_text = Path(wf).read_text()
        self.fps = int(extra.get("fps", 16))
        self.model_name = f"comfyui:{rc.model or Path(wf).stem}"
        self.limits = VideoLimits(max_shot_seconds=float(extra.get("max_shot_seconds", 5)),
                                  max_total_seconds=float(rc.max_seconds or 15))
        self.http = httpx.Client(timeout=TIMEOUT)
        self.client_id = str(uuid.uuid4())

    def submit(self, shot: ShotRequest) -> str:
        wf = self.workflow_text
        subs = {"prompt": shot.prompt, "negative": shot.negative, "seconds": shot.seconds,
                "frames": int(shot.seconds * self.fps) + 1, "seed": shot.seed, "width": shot.width, "height": shot.height}
        for k, v in subs.items():
            wf = wf.replace("{{" + k + "}}", json.dumps(v)[1:-1] if isinstance(v, str) else str(v))
        try:
            r = self.http.post(f"{self.url}/prompt", json={"prompt": json.loads(wf), "client_id": self.client_id})
            r.raise_for_status()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProviderError(f"comfyui submit: {exc}") from exc
        return r.json()["prompt_id"]

    def _history(self, job_id: str) -> dict:
        r = self.http.get(f"{self.url}/history/{job_id}")
        r.raise_for_status()
        return r.json().get(job_id, {})

    def poll(self, job_id: str) -> str:
        try:
            h = self._history(job_id)
        except httpx.HTTPError as exc:
            raise ProviderError(f"comfyui poll: {exc}") from exc
        if not h:
            return "running"
        st = (h.get("status") or {}).get("status_str")
        return "failed" if st == "error" else "done"

    def download(self, job_id: str, dest: Path) -> Path:
        h = self._history(job_id)
        for node in (h.get("outputs") or {}).values():
            for key in ("gifs", "videos", "images"):
                for f in node.get(key, []):
                    if f.get("filename", "").lower().endswith((".mp4", ".webm")):
                        params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
                        r = self.http.get(f"{self.url}/view", params=params)
                        r.raise_for_status()
                        dest.write_bytes(r.content)
                        return dest
        raise ProviderError("comfyui: no video in outputs")


class FalImage:
    def __init__(self, rc: RoleConfig):
        if not rc.api_key:
            raise ProviderError("fal image needs api_key")
        self.model = rc.model or "fal-ai/flux/schnell"
        self.model_name = f"fal:{self.model}"
        self.http = httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Key {rc.api_key}"})

    def generate(self, prompt: str, width: int, height: int, n: int = 1, seed: int = 0) -> list[bytes]:
        try:
            r = self.http.post(f"https://fal.run/{self.model}", json={
                "prompt": prompt, "image_size": {"width": width, "height": height}, "num_images": n, "seed": seed})
            r.raise_for_status()
            out = []
            for img in r.json().get("images", []):
                url = img["url"]
                if url.startswith("data:"):
                    out.append(base64.b64decode(url.split(",", 1)[1]))
                else:
                    out.append(self.http.get(url).content)
            return out
        except httpx.HTTPError as exc:
            raise ProviderError(f"fal image: {exc}") from exc


class ReplicateImage:
    def __init__(self, rc: RoleConfig):
        if not rc.api_key:
            raise ProviderError("replicate image needs api_key")
        self.model = rc.model or "black-forest-labs/flux-schnell"
        self.model_name = f"replicate:{self.model}"
        self.http = httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Bearer {rc.api_key}", "Prefer": "wait"})

    def generate(self, prompt: str, width: int, height: int, n: int = 1, seed: int = 0) -> list[bytes]:
        out = []
        try:
            for i in range(n):
                r = self.http.post(f"https://api.replicate.com/v1/models/{self.model}/predictions",
                                   json={"input": {"prompt": prompt, "seed": seed + i, "aspect_ratio": "16:9"
                                                   if width > height else "1:1"}})
                r.raise_for_status()
                url = _find_url(r.json().get("output"))
                if url:
                    out.append(self.http.get(url).content)
        except httpx.HTTPError as exc:
            raise ProviderError(f"replicate image: {exc}") from exc
        return out
