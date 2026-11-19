#!/usr/bin/env python3
"""IA en 1 minute — daily AI news video (French voice, English subtitles).

  python run_daily.py                 full run for today
  python run_daily.py --dry-run       news + script only (no audio/video)
  python run_daily.py --force         redo today's video
  python run_daily.py --from-script output/2026-11-20/script.json   render from an existing script
"""
import argparse
import logging
import shutil
import sys
import traceback
from pathlib import Path

from src import broll, fetch, music, render, scriptgen, subtitles, tts, visuals
from src.util import ROOT, french_date, load_config, load_env, log, now, read_json, write_json


def setup_logging(day: str):
    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(ROOT / "logs" / f"{day}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)


def publish_text(script: dict) -> str:
    lines = [script["title_fr"], "", script.get("description", ""), "", "Sources :"]
    for i, s in enumerate(script["stories"], 1):
        lines.append(f"{i}. {s['headline_fr']} — {s['source']} : {s['url']}")
    if script.get("_broll_used"):
        lines += ["", "Vidéos d'illustration : Pexels"]
    lines += ["", " ".join(script.get("hashtags", ["#IA", "#AI"]))]
    return "\n".join(lines) + "\n"


def make_video(cfg, script: dict, outdir: Path, date_fr: str):
    work = outdir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    voice, timeline, total = tts.build_voiceover(script, work, cfg)
    cues = subtitles.build_cues(timeline, cfg)
    subtitles.write_srt(cues, outdir / "subtitles_en.srt")
    ass = work / "subtitles_en.ass"
    subtitles.write_ass(cues, ass, cfg)
    layers = visuals.build_cards(cfg, script, date_fr, work)
    clips = broll.fetch_all(script, render.section_spans(timeline, total), work)
    mus = None
    m = cfg["video"].get("music", "auto")
    if m == "auto":
        mus = music.generate(total, work / "music.wav", seed_text=script.get("date", ""))
    elif m:
        mus = ROOT / m
    video = outdir / "video.mp4"
    render.render(cfg, layers, timeline, total, voice, ass, video, music=mus, broll=clips)
    script["_broll_used"] = bool(clips)
    render.thumbnail(video, outdir / "thumbnail.png")
    write_json(outdir / "timeline.json", timeline)
    return video, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from-script", type=Path)
    ap.add_argument("--llm", choices=["claude_cli", "anthropic_api", "ollama"],
                    help="override llm.backend for this run (e.g. to compare with --dry-run)")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate files")
    args = ap.parse_args()

    load_env()
    cfg = load_config()
    if args.llm:
        cfg["llm"]["backend"] = args.llm
    today = now(cfg)
    day = today.strftime("%Y-%m-%d")
    date_fr = french_date(today)
    setup_logging(day)
    outdir = ROOT / "output" / day

    if (outdir / "video.mp4").exists() and not (args.force or args.dry_run or args.from_script):
        log.info("video for %s already exists (use --force to redo)", day)
        return 0
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        if args.from_script:
            script = read_json(args.from_script)
            outdir = args.from_script.resolve().parent
        else:
            articles = fetch.collect(cfg)
            write_json(outdir / "candidates.json", articles)
            if len(articles) < cfg["news"]["min_stories"]:
                log.error("not enough fresh news (%d) — no video today", len(articles))
                return 2
            try:
                script = scriptgen.write_script(cfg, articles, date_fr)
            except Exception as e:
                fb = cfg["llm"].get("fallback")
                if not fb or fb == cfg["llm"]["backend"]:
                    raise
                log.warning("%s failed (%s) → falling back to %s", cfg["llm"]["backend"], e, fb)
                cfg["llm"]["backend"] = fb
                script = scriptgen.write_script(cfg, articles, date_fr)
            script["date"] = day
            script["_llm"] = cfg["llm"]["backend"]
            write_json(outdir / "script.json", script)
            log.info("script by %s: %s", script["_llm"], script["title_fr"])
            if args.dry_run:
                print("\n" + script["hook_fr"])
                for i, st in enumerate(script["stories"], 1):
                    print(f"\n[{i}] {st['headline_fr']} ({st['source']})  {st.get('key_figure') or ''}")
                    print("    " + " ".join(st["sentences_fr"]))
                print("\n" + script["outro_fr"])
                print(f"\n→ {scriptgen.word_count(script)} mots · écrit par {script['_llm']}\n")
                return 0

        try:
            video, total = make_video(cfg, script, outdir, date_fr)
        except tts.TooLong as e:
            if args.from_script:
                raise
            log.warning("%s → asking for a shorter script", e)
            script = scriptgen.write_script(cfg, articles, date_fr, shorter=True)
            script["date"] = day
            write_json(outdir / "script.json", script)
            video, total = make_video(cfg, script, outdir, date_fr)

        (outdir / "publish.txt").write_text(publish_text(script), encoding="utf-8")
        if not args.from_script:
            fetch.save_history(cfg, script["stories"])
        if not args.keep_work:
            shutil.rmtree(outdir / "_work", ignore_errors=True)
        log.info("DONE ✔ %s (%.1fs)", video, total)
        return 0
    except Exception as e:
        log.error("FAILED: %s\n%s", e, traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
