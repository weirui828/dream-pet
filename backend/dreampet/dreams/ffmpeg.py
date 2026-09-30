"""ffmpeg helpers for the dream video: Ken Burns stills, normalizing clips, stitching with
dissolves, an ambient audio bed, and a poster frame."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from dreampet.providers.base import ProviderError

XFADE = 0.6  # seconds of dissolve between shots


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ProviderError(f"ffmpeg: {r.stderr.strip()[-500:]}")


def ken_burns(image: Path, out: Path, seconds: float, width: int, height: int, fps: int, zoom_in: bool = True) -> Path:
    frames = max(1, int(seconds * fps))
    z = "min(zoom+0.0012,1.25)" if zoom_in else "if(eq(on,1),1.25,max(zoom-0.0012,1.0))"
    vf = (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,crop={width * 2}:{height * 2},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
          f"format=yuv420p")
    _run(["-loop", "1", "-i", str(image), "-vf", vf, "-t", f"{seconds:.2f}", "-r", str(fps),
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)])
    return out


def normalize(clip: Path, out: Path, seconds: float, width: int, height: int, fps: int) -> Path:
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
          f"setsar=1,fps={fps},format=yuv420p")
    # tpad clones the last frame if the provider returned a slightly short clip
    _run(["-i", str(clip), "-vf", vf + f",tpad=stop_mode=clone:stop_duration={seconds:.2f}", "-t", f"{seconds:.2f}",
          "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)])
    return out


def stitch(clips: list[tuple[Path, float]], out: Path, audio_bed: bool = True) -> float:
    """Concatenate normalized clips with dissolves. Returns the final duration in seconds."""
    if not clips:
        raise ProviderError("nothing to stitch")
    inputs: list[str] = []
    for p, _ in clips:
        inputs += ["-i", str(p)]
    n = len(clips)
    total = sum(d for _, d in clips) - XFADE * (n - 1)
    parts = []
    last = "[0:v]"
    offset = 0.0
    for i in range(1, n):
        offset += clips[i - 1][1] - XFADE
        label = f"[v{i}]"
        parts.append(f"{last}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}{label}")
        last = label
    vmap = last
    fc = ";".join(parts)
    args = [*inputs]
    if audio_bed:
        args += ["-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=0.08:duration={total:.2f}:seed=7"]
        afilter = (f"[{n}:a]lowpass=f=320,highpass=f=40,volume=0.6,afade=t=in:d=2,"
                   f"afade=t=out:st={max(0.0, total - 2.5):.2f}:d=2.5[a]")
        fc = f"{fc};{afilter}" if fc else afilter
    if fc:
        args += ["-filter_complex", fc]
    args += ["-map", vmap if n > 1 else "0:v"]
    if audio_bed:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "96k"]
    args += ["-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)]
    _run(args)
    return total


def poster(video: Path, out: Path, at: float = 1.0) -> Path:
    _run(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)])
    return out
