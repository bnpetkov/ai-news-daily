"""Step 6 — assemble background + cards + voice + burned-in English subtitles."""
from pathlib import Path

from .util import ROOT, log, run

FADE = 0.3


def section_spans(timeline: list[dict], total: float) -> list[tuple[str, float, float]]:
    order, starts = [], {}
    for seg in timeline:
        if seg["section"] not in starts:
            order.append(seg["section"])
            starts[seg["section"]] = seg["start"]
    spans = []
    for i, name in enumerate(order):
        a = 0.0 if i == 0 else starts[name] - 0.15
        b = total if i == len(order) - 1 else starts[order[i + 1]] - 0.15
        spans.append((name, max(0.0, a), b))
    return spans


def _esc(p: Path) -> str:
    # escaping for paths inside an ffmpeg filtergraph
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def render(cfg, cards: dict[str, Path], timeline, total: float, voice: Path, ass: Path, out: Path):
    v = cfg["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    cols = v["colors"]["bg"]
    spans = section_spans(timeline, total)

    inputs = ["-f", "lavfi", "-i",
              f"gradients=s={W}x{H}:r={fps}:d={total:.3f}:speed=0.008:type=linear:n={len(cols)}:"
              + ":".join(f"c{i}={c}" for i, c in enumerate(cols))]
    for name, a, b in spans:
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{b - a:.3f}", "-i", str(cards[name])]
    inputs += ["-i", str(voice)]
    audio_idx = len(spans) + 1
    music = v.get("music")
    music_path = ROOT / music if music else None
    if music_path and music_path.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]

    f = []
    # subtle vignette + slow zoom feel on background
    f.append("[0:v]format=yuv420p,vignette=PI/5[bg0]")
    last = "bg0"
    for i, (name, a, b) in enumerate(spans, 1):
        d = b - a
        # cards slide up slightly and fade in/out
        f.append(f"[{i}:v]format=rgba,fade=t=in:st=0:d={FADE}:alpha=1,"
                 f"fade=t=out:st={max(0, d - FADE):.3f}:d={FADE}:alpha=1,"
                 f"setpts=PTS-STARTPTS+{a:.3f}/TB[c{i}]")
        f.append(f"[{last}][c{i}]overlay=x=0:y='max(0,40-200*(t-{a:.3f}))':eof_action=pass:"
                 f"enable='between(t,{a:.3f},{b:.3f})'[v{i}]")
        last = f"v{i}"
    accent = v["colors"]["accent"].lstrip("#")
    f.append(f"[{last}]drawbox=x=0:y=0:w=iw:h=14:color=white@0.12:t=fill,"
             f"drawbox=x=0:y=0:w='iw*t/{total:.3f}':h=14:color=0x{accent}@0.95:t=fill,"
             f"ass='{_esc(ass)}':fontsdir='{_esc(ROOT / 'assets' / 'fonts')}'[vout]")
    if music_path and music_path.exists():
        f.append(f"[{audio_idx + 1}:a]volume={v.get('music_volume', 0.1)}[m];"
                 f"[{audio_idx}:a][m]amix=inputs=2:duration=first:normalize=0[aout]")
        amap = "[aout]"
    else:
        amap = f"{audio_idx}:a"

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(f),
           "-map", "[vout]", "-map", amap, "-t", f"{total:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", str(v["crf"]), "-pix_fmt", "yuv420p",
           "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    log.info("rendering %s (%.1fs, %d sections)", out.name, total, len(spans))
    run(cmd)


def thumbnail(video: Path, out: Path, at: float = 1.2):
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1", str(out)])
