"""Step 2 — pick the stories and write the French script + English translation."""
import json
import os
import re
import subprocess

import requests

from .util import log

PROMPT = """Tu es le rédacteur en chef de « IA en 1 minute », une vidéo verticale quotidienne
(TikTok / YouTube Shorts / Reels) qui résume les actualités de l'intelligence artificielle.
Date du jour : {date_fr}.

Voici les articles candidats (JSON). Ce sont tes SEULES sources :
{articles}

TÂCHE
1. Choisis les {n} actualités les plus intéressantes pour un large public francophone.
   Varie les sujets (ex. : nouveau modèle/produit, recherche, régulation/entreprise/société).
   Évite les promotions, les tutoriels et les articles d'opinion.
2. Écris le script de la voix off en français, puis sa traduction anglaise phrase par phrase.

RÈGLES DU SCRIPT
- Total de la voix off (hook + toutes les phrases + outro) : entre {wmin} et {wmax} mots en français.
- hook_fr : une seule phrase accrocheuse (≤ 12 mots).
- Pour chaque actualité : 2 ou 3 phrases courtes (≤ 16 mots chacune) : ce qui s'est passé, puis pourquoi c'est important.
- outro_fr : une phrase, invite à s'abonner pour la minute IA de demain.
- Style oral, simple, dynamique. Explique tout terme technique en quelques mots.
- UNIQUEMENT des faits présents dans les articles fournis. N'invente aucun chiffre, date, nom ou citation.
  En cas de doute, omets le détail.
- Écris les nombres et sigles comme ils doivent être prononcés naturellement par une voix de synthèse.
- headline_fr : titre à l'écran, 6 mots maximum.
- sentences_en / hook_en / outro_en : traduction anglaise naturelle, MÊME NOMBRE de phrases que le français.
- source : nom du média ; url : l'URL exacte de l'article utilisé.
- key_figure : LE chiffre clé de l'actualité s'il y en a un dans l'article (ex. {{"value": "83 $", "label": "prix de l'action"}},
  {{"value": "-40 %", "label": "de clics vers les sites"}}). value ≤ 14 caractères, label ≤ 30 caractères.
  Si l'article ne contient aucun chiffre marquant, mets null. N'invente jamais de chiffre.
- broll_query_en : 2 à 4 mots anglais décrivant une vidéo d'illustration générique (banque d'images), sans marque
  ni personne célèbre (ex. "stock market screen", "data center servers", "hacker typing code").
{extra}
Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, au format :
{{
  "title_fr": "titre de l'épisode",
  "hook_fr": "...", "hook_en": "...",
  "stories": [
    {{"headline_fr": "...", "sentences_fr": ["...", "..."], "sentences_en": ["...", "..."],
      "source": "...", "url": "https://...",
      "key_figure": {{"value": "...", "label": "..."}} ou null, "broll_query_en": "..."}}
  ],
  "outro_fr": "...", "outro_en": "...",
  "description": "description pour la publication (2-3 phrases en français)",
  "hashtags": ["#IA", "#AI", "..."]
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


def _ask_claude_cli(prompt: str, timeout: int) -> str:
    r = subprocess.run(["claude", "-p", "--output-format", "json"], input=prompt,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {r.stderr[-1000:]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude CLI error: {out.get('result')}")
    return out["result"]


def _ask_api(prompt: str, model: str, timeout: int) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing (put it in .env)")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": model, "max_tokens": 4000,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def _ask_ollama(prompt: str, lcfg: dict) -> str:
    """Local model through Ollama (http://localhost:11434). Free and offline, but slower / weaker."""
    r = requests.post(lcfg.get("ollama_url", "http://localhost:11434") + "/api/generate",
                      timeout=lcfg.get("ollama_timeout_sec", 2400),
                      json={"model": lcfg.get("ollama_model", "qwen2.5:3b"), "prompt": prompt,
                            "stream": False, "format": "json",
                            "options": {"num_ctx": 8192, "temperature": 0.4}})
    r.raise_for_status()
    return r.json()["response"]


def word_count(script: dict) -> int:
    parts = [script["hook_fr"], script["outro_fr"]]
    for s in script["stories"]:
        parts += s["sentences_fr"]
    return sum(len(re.findall(r"[\wÀ-ÿ'’-]+", p)) for p in parts)


def validate(script: dict, cfg: dict, candidate_urls: set[str] | None) -> list[str]:
    errs = []
    for k in ("title_fr", "hook_fr", "hook_en", "stories", "outro_fr", "outro_en"):
        if not script.get(k):
            errs.append(f"missing field {k}")
    if errs:
        return errs
    n_min = cfg["news"]["min_stories"]
    if len(script["stories"]) < n_min:
        errs.append(f"only {len(script['stories'])} stories")
    for i, s in enumerate(script["stories"], 1):
        if len(s.get("sentences_fr", [])) != len(s.get("sentences_en", [])) or not s.get("sentences_fr"):
            errs.append(f"story {i}: FR/EN sentence count differs")
        if not str(s.get("url", "")).startswith("http"):
            errs.append(f"story {i}: missing url")
        elif candidate_urls is not None and s["url"] not in candidate_urls:
            errs.append(f"story {i}: url not among the provided articles")
        if len(s.get("headline_fr", "").split()) > 8:
            errs.append(f"story {i}: headline too long")
        kf = s.get("key_figure")
        if kf is not None and (not isinstance(kf, dict) or not kf.get("value")
                               or len(str(kf["value"])) > 16 or len(str(kf.get("label", ""))) > 34):
            errs.append(f"story {i}: key_figure must be null or {{value ≤14 chars, label ≤30 chars}}")
    wc = word_count(script)
    lo, hi = cfg["llm"]["words_min"], cfg["llm"]["words_max"]
    if not lo <= wc <= hi:
        errs.append(f"word count {wc} not in [{lo}, {hi}]")
    return errs


def write_script(cfg: dict, articles: list[dict], date_fr: str, shorter: bool = False) -> dict:
    lcfg = cfg["llm"]
    local = lcfg["backend"] == "ollama"
    if local:  # small local models: fewer, shorter articles so it fits and stays fast
        articles = articles[: lcfg.get("ollama_max_articles", 15)]
    n = min(cfg["news"]["stories_per_video"], len(articles))
    compact = [{"source": a["source"], "also_covered_by": a.get("also", []), "title": a["title"],
                "url": a["url"], "published": a["published"],
                "summary": a["summary"][:350] if local else a["summary"]} for a in articles]
    wmin, wmax = lcfg["words_min"], lcfg["words_max"]
    if shorter:
        wmin, wmax = wmin - 15, wmax - 20
    extra = ""
    urls = {a["url"] for a in articles}
    last_err = None
    for attempt in range(1, 4):
        prompt = PROMPT.format(date_fr=date_fr, articles=json.dumps(compact, ensure_ascii=False, indent=1),
                               n=n, wmin=wmin, wmax=wmax, extra=extra)
        try:
            if lcfg["backend"] == "anthropic_api":
                raw = _ask_api(prompt, lcfg["api_model"], lcfg["timeout_sec"])
            elif local:
                log.info("asking local model %s (can take several minutes)…", lcfg.get("ollama_model"))
                raw = _ask_ollama(prompt, lcfg)
            else:
                raw = _ask_claude_cli(prompt, lcfg["timeout_sec"])
            script = _extract_json(raw)
        except Exception as e:
            last_err = str(e)
            log.warning("script attempt %d failed: %s", attempt, e)
            continue
        errs = validate(script, {**cfg, "llm": {**lcfg, "words_min": wmin, "words_max": wmax}}, urls)
        if not errs:
            log.info("script ok (%d words, %d stories)", word_count(script), len(script["stories"]))
            return script
        last_err = "; ".join(errs)
        log.warning("script attempt %d invalid: %s", attempt, last_err)
        extra = f"\nATTENTION — ta réponse précédente avait ces problèmes, corrige-les : {last_err}\n"
    raise RuntimeError(f"could not get a valid script: {last_err}")
