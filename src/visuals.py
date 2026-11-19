"""Step 5 — transparent title cards (PNG) drawn over the animated background."""
import random
import zlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .util import ROOT

FONTS = ROOT / "assets" / "fonts"


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), size)


def _rgb(hex_color: str, alpha: int = 255):
    h = hex_color.lstrip("#").removeprefix("0x")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, weight, max_w, max_lines, start, minimum):
    size = start
    while size > minimum:
        f = _font(weight, size)
        lines = _wrap(draw, text, f, max_w)
        if len(lines) <= max_lines and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = _font(weight, minimum)
    return f, _wrap(draw, text, f, max_w)


def _glow(img, xy, r, color, alpha=110, blur=140):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    d.ellipse((x - r, y - r, x + r, y + r), fill=_rgb(color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(layer)


def _pill(draw, x, y, text, font, fg, bg, pad_x=30, pad_y=16, anchor="l"):
    w = draw.textlength(text, font=font)
    asc, desc = font.getmetrics()
    h = asc + desc
    if anchor == "c":
        x = x - (w + 2 * pad_x) / 2
    draw.rounded_rectangle((x, y, x + w + 2 * pad_x, y + h + 2 * pad_y), radius=(h + 2 * pad_y) / 2, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)
    return y + h + 2 * pad_y


def _center(draw, y, text, font, fill, W):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def _brand(draw, cfg):
    c = cfg["video"]["colors"]
    _pill(draw, 70, 175, cfg["video"]["brand"], _font("Bold", 34), _rgb(c["text"]), _rgb(c["accent"], 230))


def hook_card(cfg, date_fr: str, n_stories: int, path: Path):
    v, c = cfg["video"], cfg["video"]["colors"]
    W, H = v["width"], v["height"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _glow(img, (W * 0.25, H * 0.33), 330, c["accent"])
    _glow(img, (W * 0.8, H * 0.5), 280, c["accent2"], alpha=80)
    d = ImageDraw.Draw(img)
    _pill(d, W / 2, 470, date_fr.capitalize(), _font("SemiBold", 40), _rgb(c["text"]),
          (255, 255, 255, 40), anchor="c")
    words = v["brand"].split()
    split = max(1, len(words) - 2)
    l1, l2 = " ".join(words[:split]), " ".join(words[split:])
    _center(d, 620, l1, _font("Black", 190), _rgb(c["text"]), W)
    _center(d, 830, l2, _font("Black", 190), _rgb(c["accent2"]), W)
    _center(d, 1090, f"{n_stories} actus IA à ne pas rater", _font("SemiBold", 56), _rgb(c["muted"]), W)
    img.save(path)


def story_card(cfg, idx: int, total: int, headline: str, source: str, path: Path):
    v, c = cfg["video"], cfg["video"]["colors"]
    W, H = v["width"], v["height"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _glow(img, (W * (0.2 if idx % 2 else 0.8), H * 0.42), 320, c["accent"])
    _glow(img, (W * (0.85 if idx % 2 else 0.15), H * 0.62), 240, c["accent2"], alpha=70)
    d = ImageDraw.Draw(img)
    _brand(d, cfg)
    num_f = _font("Black", 190)
    d.text((70, 270), f"{idx:02d}", font=num_f, fill=_rgb(c["accent2"]))
    nw = d.textlength(f"{idx:02d}", font=num_f)
    d.text((70 + nw + 18, 385), f"/ {total:02d}", font=_font("Bold", 58), fill=_rgb(c["muted"], 200))
    # headline card
    f, lines = _fit(d, headline, "ExtraBold", W - 2 * 110, 3, 100, 64)
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * 1.08)
    top = 540
    box_h = lh * len(lines) + 90
    d.rounded_rectangle((60, top, W - 60, top + box_h), radius=44, fill=(10, 12, 35, 150),
                        outline=_rgb(c["accent"], 160), width=3)
    y = top + 45
    for line in lines:
        d.text((110, y), line, font=f, fill=_rgb(c["text"]))
        y += lh
    bottom = _pill(d, 60, top + box_h + 30, f"Source : {source}", _font("SemiBold", 38), _rgb(c["muted"]),
                   (255, 255, 255, 34))
    img.save(path)
    return int(bottom)


# ── animated key figure (count-up) ───────────────────────────
NUM_RE = re.compile(r"[-+−]?\d[\d\s .,]*\d|[-+−]?\d")


def _parse_number(value: str):
    m = NUM_RE.search(value)
    if not m:
        return None
    raw = m.group(0)
    sign = -1 if raw[0] in "-−" else 1
    digits = re.sub(r"[^\d.,]", "", raw)
    # "1 500" / "1,5" / "1.5" / "1,500"
    dec_sep = None
    if "," in digits and "." not in digits and len(digits.split(",")[-1]) != 3:
        dec_sep = ","
    elif "." in digits and "," not in digits and len(digits.split(".")[-1]) != 3:
        dec_sep = "."
    if dec_sep:
        intpart, dec = digits.split(dec_sep)[0].replace(",", "").replace(".", ""), digits.split(dec_sep)[1]
        num, decimals = float(f"{intpart}.{dec}"), len(dec)
    else:
        num, decimals = float(digits.replace(",", "").replace(".", "")), 0
    return value[:m.start()], sign * num, decimals, dec_sep or ",", value[m.end():], raw[0] in "+-−"


def _fmt(n: float, decimals: int, dec_sep: str, signed: bool) -> str:
    s = f"{abs(n):,.{decimals}f}".replace(",", " ").replace(".", dec_sep)
    if signed:
        s = ("−" if n < 0 else "+") + s
    return s


def figure_frames(cfg, value: str, label: str, outdir: Path, fps: int) -> tuple[Path, int, int]:
    """Writes a PNG sequence (fig_000.png …) of the key figure counting up. Returns (pattern, n_frames, height)."""
    c = cfg["video"]["colors"]
    W, Hh = cfg["video"]["width"], 300
    outdir.mkdir(parents=True, exist_ok=True)
    parsed = _parse_number(value)
    delay, count = int(0.35 * fps), int(0.9 * fps)
    n = delay + count + 1
    vf, lf = _font("Black", 140), _font("SemiBold", 42)
    for i in range(n):
        img = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
        if i >= delay:
            p = min(1.0, (i - delay) / count)
            e = 1 - (1 - p) ** 3  # ease-out
            if parsed:
                pre, num, decs, sep, post, signed = parsed
                txt = f"{pre}{_fmt(num * e, decs, sep, signed)}{post}".strip()
            else:
                txt = value
            alpha = int(255 * min(1.0, p * 3))
            d = ImageDraw.Draw(img)
            d.text((70, 0), txt, font=vf, fill=_rgb(c["accent2"], alpha))
            tw = d.textlength(txt, font=vf)
            # underline bar that grows with the count
            d.rounded_rectangle((72, 172, 72 + max(8, tw * e), 182), radius=5, fill=_rgb(c["accent"], alpha))
            if label:
                d.text((72, 200), label, font=lf, fill=_rgb(c["muted"], alpha))
        img.save(outdir / f"fig_{i:03d}.png")
    return outdir / "fig_%03d.png", n, Hh


# ── drifting particles layer (tileable, 2× height for scrolling) ──
def particles(cfg, path: Path, seed: int = 7):
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    rnd = random.Random(seed)
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for _ in range(70):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.choice([2, 2, 3, 3, 4, 6])
        a = rnd.randint(40, 130)
        col = rnd.choice([cfg["video"]["colors"]["accent2"], cfg["video"]["colors"]["accent"], "#ffffff"])
        for dy in (0, H, -H):  # wrap so the tile is seamless vertically
            d.ellipse((x - r, y + dy - r, x + r, y + dy + r), fill=_rgb(col, a))
    # faint connecting lines for a "network" look
    pts = [(rnd.uniform(0, W), rnd.uniform(0, H)) for _ in range(18)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if abs(x1 - x2) + abs(y1 - y2) < 700:
            d.line((x1, y1, x2, y2), fill=(255, 255, 255, 22), width=2)
    tile = tile.filter(ImageFilter.GaussianBlur(0.6))
    full = Image.new("RGBA", (W, H * 2), (0, 0, 0, 0))
    full.paste(tile, (0, 0)); full.paste(tile, (0, H))
    full.save(path)


def outro_card(cfg, path: Path):
    v, c = cfg["video"], cfg["video"]["colors"]
    W, H = v["width"], v["height"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _glow(img, (W * 0.5, H * 0.38), 380, c["accent"], alpha=120)
    d = ImageDraw.Draw(img)
    _center(d, 560, "Abonnez-vous", _font("Black", 124), _rgb(c["text"]), W)
    _center(d, 740, "Votre minute IA, chaque jour", _font("SemiBold", 58), _rgb(c["muted"]), W)
    _pill(d, W / 2, 880, v["handle"], _font("Bold", 56), _rgb(c["text"]), _rgb(c["accent"], 235),
          pad_x=44, pad_y=22, anchor="c")
    _center(d, 1060, "▶  À demain !", _font("Bold", 54), _rgb(c["accent2"]), W)
    img.save(path)


def build_cards(cfg, script: dict, date_fr: str, workdir: Path) -> dict:
    """Returns {"cards": {section: png}, "figures": {section: (pattern, n, y)}, "particles": png}."""
    cards, figures = {}, {}
    p = workdir / "card_hook.png"
    hook_card(cfg, date_fr, len(script["stories"]), p)
    cards["hook"] = p
    for i, s in enumerate(script["stories"], 1):
        p = workdir / f"card_story{i}.png"
        bottom = story_card(cfg, i, len(script["stories"]), s["headline_fr"], s["source"], p)
        cards[f"story{i}"] = p
        kf = s.get("key_figure")
        if isinstance(kf, dict) and kf.get("value"):
            pat, n, _ = figure_frames(cfg, str(kf["value"]), str(kf.get("label", "")),
                                      workdir / f"fig{i}", cfg["video"]["fps"])
            figures[f"story{i}"] = (pat, n, bottom + 30)
    p = workdir / "card_outro.png"
    outro_card(cfg, p)
    cards["outro"] = p
    part = workdir / "particles.png"
    particles(cfg, part, seed=zlib.crc32(script.get("date", "").encode()) % 1000)
    return {"cards": cards, "figures": figures, "particles": part}
