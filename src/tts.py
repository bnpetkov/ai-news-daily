"""Step 3 — French voice-over, one clip per sentence (gives exact subtitle timing)."""
import asyncio
import sys
import wave
from pathlib import Path

from .util import ROOT, duration, log, run

RATE = 24000  # Hz, mono 16-bit


def _edge(text: str, out_mp3: Path, cfg: dict):
    import edge_tts
    comm = edge_tts.Communicate(text, cfg["edge_voice"], rate=cfg.get("edge_rate", "+0%"))
    asyncio.run(comm.save(str(out_mp3)))
    if not out_mp3.exists() or out_mp3.stat().st_size < 1000:
        raise RuntimeError("edge-tts produced no audio")


def _piper(text: str, out_wav: Path, cfg: dict):
    model = ROOT / cfg["piper_model"]
    if not model.exists():
        raise RuntimeError(f"piper model missing: {model} (see README)")
    run([sys.executable, "-m", "piper", "-m", str(model), "-f", str(out_wav),
         "--length-scale", str(cfg.get("piper_length_scale", 1.0))], input=text)


def _to_wav(src: Path, dst: Path):
    # trim leading/trailing silence so pauses are controlled by us
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af",
         "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
         "silenceremove=start_periods=1:start_threshold=-50dB,areverse",
         "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", str(dst)])


def synth_sentence(text: str, dst: Path, cfg: dict) -> str:
    engines = [cfg["engine"]] + ([cfg["fallback"]] if cfg.get("fallback") and cfg["fallback"] != cfg["engine"] else [])
    last = None
    for eng in engines:
        try:
            raw = dst.with_suffix(".raw.mp3" if eng == "edge" else ".raw.wav")
            (_edge if eng == "edge" else _piper)(text, raw, cfg)
            _to_wav(raw, dst)
            raw.unlink(missing_ok=True)
            return eng
        except Exception as e:
            last = e
            log.warning("tts engine %s failed: %s", eng, str(e)[:300])
    raise RuntimeError(f"all TTS engines failed: {last}")


def build_voiceover(script: dict, workdir: Path, cfg: dict) -> tuple[Path, list[dict], float]:
    """Returns (voice.wav, timeline, total_seconds).

    timeline = [{"section": "hook"|"story1".."storyN"|"outro", "fr": str, "en": str,
                 "start": float, "end": float}, ...]
    """
    tcfg = cfg["tts"]
    clips = workdir / "clips"
    clips.mkdir(parents=True, exist_ok=True)

    sections = [("hook", [(script["hook_fr"], script["hook_en"])])]
    for i, s in enumerate(script["stories"], 1):
        sections.append((f"story{i}", list(zip(s["sentences_fr"], s["sentences_en"]))))
    sections.append(("outro", [(script["outro_fr"], script["outro_en"])]))

    timeline, frames, t = [], [], 0.25
    frames.append(b"\x00\x00" * int(RATE * 0.25))
    used = set()
    k = 0
    for si, (name, sents) in enumerate(sections):
        if si > 0:
            pad = tcfg["pause_section"]
            frames.append(b"\x00\x00" * int(RATE * pad))
            t += pad
        for j, (fr, en) in enumerate(sents):
            if j > 0:
                pad = tcfg["pause_sentence"]
                frames.append(b"\x00\x00" * int(RATE * pad))
                t += pad
            k += 1
            wav = clips / f"{k:02d}.wav"
            used.add(synth_sentence(fr, wav, tcfg))
            with wave.open(str(wav)) as w:
                data = w.readframes(w.getnframes())
            d = len(data) / 2 / RATE
            frames.append(data)
            timeline.append({"section": name, "fr": fr, "en": en, "start": t, "end": t + d})
            t += d
    frames.append(b"\x00\x00" * int(RATE * 0.6))
    t += 0.6

    raw = workdir / "voice_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"".join(frames))

    speed = 1.0
    if t > tcfg["max_duration"]:
        speed = t / tcfg["max_duration"]
        if speed > tcfg["max_speedup"]:
            raise TooLong(t)
        log.info("voice %.1fs > %.1fs → speeding up ×%.3f", t, tcfg["max_duration"], speed)
        for seg in timeline:
            seg["start"] /= speed; seg["end"] /= speed
        t /= speed

    out = workdir / "voice.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
         f"atempo={speed:.4f},loudnorm=I=-14:TP=-1.5:LRA=11",
         "-ar", "48000", "-ac", "2", str(out)])
    raw.unlink(missing_ok=True)
    log.info("voice-over %.1fs with %s", t, "+".join(sorted(used)))
    return out, timeline, max(t, duration(out))


class TooLong(Exception):
    def __init__(self, seconds):
        super().__init__(f"voice-over too long: {seconds:.1f}s")
        self.seconds = seconds
