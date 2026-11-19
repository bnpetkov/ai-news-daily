"""Step 4 — English subtitles (SRT for upload, ASS for burning in)."""
import textwrap
from pathlib import Path


def _chunks(text: str, max_chars: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(text, max_chars, break_long_words=False)
    if len(lines) <= max_lines:
        return ["\n".join(lines)]
    # split into balanced cues of ≤ max_lines lines
    n_cues = -(-len(lines) // max_lines)
    words = text.split()
    per = -(-len(words) // n_cues)
    out = []
    for i in range(0, len(words), per):
        part = " ".join(words[i:i + per])
        out.append("\n".join(textwrap.wrap(part, max_chars, break_long_words=False)[:max_lines + 1]))
    return out


def build_cues(timeline: list[dict], cfg: dict) -> list[dict]:
    scfg = cfg["subtitles"]
    cues = []
    for seg in timeline:
        parts = _chunks(seg["en"], scfg["max_chars_line"], scfg["max_lines"])
        total_chars = sum(len(p) for p in parts) or 1
        t = seg["start"]
        span = seg["end"] - seg["start"]
        for p in parts:
            d = span * len(p) / total_chars
            cues.append({"start": t, "end": t + d, "text": p})
            t += d
    # bridge tiny gaps so subtitles don't flicker
    for a, b in zip(cues, cues[1:]):
        if 0 < b["start"] - a["end"] < 0.35:
            a["end"] = b["start"]
    return cues


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_srt(cues, path: Path):
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{_srt_time(c['start'])} --> {_srt_time(c['end'])}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ass(cues, path: Path, cfg: dict):
    v, s = cfg["video"], cfg["subtitles"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {v['width']}
PlayResY: {v['height']}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{s['font']},{s['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,3,2,70,70,{s['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        txt = c["text"].replace("\n", "\\N").replace("{", "(").replace("}", ")")
        # quick pop-in animation
        anim = r"{\fad(80,60)\t(0,120,\fscx104\fscy104)\t(120,220,\fscx100\fscy100)}"
        events.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Sub,,0,0,0,,{anim}{txt}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
