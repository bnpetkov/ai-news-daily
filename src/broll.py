"""Optional B-roll: short free stock videos from Pexels behind each story.

Needs a free API key (https://www.pexels.com/api/) in .env as PEXELS_API_KEY.
Without a key, or if anything fails, the animated background is used instead.
"""
import os
from pathlib import Path

import requests

from .util import log

API = "https://api.pexels.com/videos/search"


def _pick_file(video: dict) -> dict | None:
    files = [f for f in video.get("video_files", [])
             if f.get("height") and f.get("width") and f["height"] > f["width"]
             and 1000 <= f["height"] <= 2000 and f.get("file_type") == "video/mp4"]
    return min(files, key=lambda f: abs(f["height"] - 1920)) if files else None


def fetch_clip(query: str, min_seconds: float, dst: Path, used_ids: set) -> Path | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not query:
        return None
    try:
        r = requests.get(API, headers={"Authorization": key}, timeout=20,
                         params={"query": query, "orientation": "portrait", "per_page": 15, "size": "medium"})
        r.raise_for_status()
        videos = [v for v in r.json().get("videos", []) if v["id"] not in used_ids]
        # prefer clips long enough to avoid looping
        videos.sort(key=lambda v: (v.get("duration", 0) < min_seconds, abs(v.get("duration", 0) - 15)))
        for v in videos:
            f = _pick_file(v)
            if not f:
                continue
            with requests.get(f["link"], stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(dst, "wb") as out:
                    for chunk in resp.iter_content(1 << 16):
                        out.write(chunk)
            used_ids.add(v["id"])
            log.info("b-roll '%s' → pexels #%s (%ss)", query, v["id"], v.get("duration"))
            return dst
    except Exception as e:
        log.warning("b-roll '%s' failed: %s", query, e)
    return None


def fetch_all(script: dict, spans: list, workdir: Path) -> dict[str, Path]:
    if not os.environ.get("PEXELS_API_KEY"):
        return {}
    durations = {name: b - a for name, a, b in spans}
    clips, used = {}, set()
    for i, s in enumerate(script["stories"], 1):
        name = f"story{i}"
        p = fetch_clip(s.get("broll_query_en", ""), durations.get(name, 15), workdir / f"broll{i}.mp4", used)
        if p:
            clips[name] = p
    return clips
