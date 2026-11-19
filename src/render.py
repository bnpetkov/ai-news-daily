"""Step 6 — assemble everything with ffmpeg.

Layers (bottom → top): animated gradient · B-roll (optional) · drifting particles ·
title cards · animated key figures · progress bar · karaoke subtitles.
Audio: voice + background music ducked under the voice.
"""
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
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def render(cfg, layers: dict, timeline, total: float, voice: Path, ass: Path, out: Path,
           music: Path | None = None, broll: dict | None = None):
    v = cfg["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    cols = v["colors"]["bg"]
    spans = section_spans(timeline, total)
    cards, figures, part = layers["cards"], layers["figures"], layers["particles"]
    broll = broll or {}

    inputs: list[str] = []
    idx = 0

    def add(*args):
        nonlocal idx
        inputs.extend(args)
        idx += 1
        return idx - 1

    bg = add("-f", "lavfi", "-i",
             f"gradients=s={W}x{H}:r={fps}:d={total:.3f}:speed=0.008:type=linear:n={len(cols)}:"
             + ":".join(f"c{i}={c}" for i, c in enumerate(cols)))
    f = [f"[{bg}:v]format=yuv420p,vignette=PI/5[L0]"]
    last = "L0"

    def overlay(src_label, a, b, x="0", y="0"):
        nonlocal last
        out_l = f"L{len(f)}"
        f.append(f"[{last}][{src_label}]overlay=x={x}:y={y}:eof_action=pass:"
                 f"enable='between(t,{a:.3f},{b:.3f})'[{out_l}]")
        last = out_l

    # B-roll behind each story (darkened so text stays readable)
    for name, a, b in spans:
        if name in broll:
            k = add("-stream_loop", "-1", "-t", f"{b - a:.3f}", "-i", str(broll[name]))
            d = b - a
            f.append(f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},"
                     f"eq=brightness=-0.12:saturation=0.85,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.42:t=fill,format=yuva420p,"
                     f"fade=t=in:st=0:d=0.4:alpha=1,fade=t=out:st={max(0, d - 0.4):.3f}:d=0.4:alpha=1,"
                     f"setpts=PTS-STARTPTS+{a:.3f}/TB[br{k}]")
            overlay(f"br{k}", a, b)

    # drifting particles over the whole video
    k = add("-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", str(part))
    f.append(f"[{k}:v]format=rgba[pt]")
    overlay("pt", 0, total, y=f"'-mod(t*22,{H})'")

    # title cards: slide up + fade
    for name, a, b in spans:
        d = b - a
        k = add("-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", str(cards[name]))
        f.append(f"[{k}:v]format=rgba,fade=t=in:st=0:d={FADE}:alpha=1,"
                 f"fade=t=out:st={max(0, d - FADE):.3f}:d={FADE}:alpha=1,"
                 f"setpts=PTS-STARTPTS+{a:.3f}/TB[cd{k}]")
        overlay(f"cd{k}", a, b, y=f"'max(0,40-200*(t-{a:.3f}))'")

        # key figure counting up
        if name in figures:
            pattern, n, fy = figures[name]
            k2 = add("-framerate", str(fps), "-i", str(pattern))
            hold = max(0.1, d - n / fps)
            f.append(f"[{k2}:v]format=rgba,tpad=stop_mode=clone:stop_duration={hold:.3f},"
                     f"fade=t=out:st={max(0, d - FADE):.3f}:d={FADE}:alpha=1,"
                     f"setpts=PTS-STARTPTS+{a:.3f}/TB[fg{k2}]")
            overlay(f"fg{k2}", a, b, y=str(fy))

    accent = v["colors"]["accent"].lstrip("#")
    f.append(f"[{last}]drawbox=x=0:y=0:w=iw:h=14:color=white@0.12:t=fill,"
             f"drawbox=x=0:y=0:w='iw*t/{total:.3f}':h=14:color=0x{accent}@0.95:t=fill,"
             f"ass='{_esc(ass)}':fontsdir='{_esc(ROOT / 'assets' / 'fonts')}'[vout]")

    # audio
    vi = add("-i", str(voice))
    if music and Path(music).exists():
        mi = add("-stream_loop", "-1", "-i", str(music))
        vol = v.get("music_volume", 0.18)
        f.append(f"[{vi}:a]asplit=2[vo][sc]")
        f.append(f"[{mi}:a]lowpass=f=4200,volume={vol},afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[mu]")
        f.append("[mu][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[duck]")
        f.append("[vo][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
        amap = "[aout]"
    else:
        amap = f"{vi}:a"

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(f),
           "-map", "[vout]", "-map", amap, "-t", f"{total:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", str(v["crf"]), "-pix_fmt", "yuv420p",
           "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    log.info("rendering %s (%.1fs, %d sections, %d b-roll, music: %s)",
             out.name, total, len(spans), len(broll), "yes" if music else "no")
    run(cmd)


def thumbnail(video: Path, out: Path, at: float = 1.2):
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1", str(out)])
