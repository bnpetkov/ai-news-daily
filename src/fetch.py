"""Step 1 — collect fresh AI news from RSS feeds and Hacker News."""
import calendar
import hashlib
import html
import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

from .util import ROOT, log, read_json, write_json

UA = "Mozilla/5.0 (X11; Linux x86_64) ia1minute/1.0 (+news digest)"
HISTORY = ROOT / "data" / "history.json"


def _clean(text: str, limit: int = 700) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _norm_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", ""))


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def _entry_time(e) -> float | None:
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return calendar.timegm(e[k])
    return None


def _matches(text: str, keywords: list[str]) -> bool:
    t = f" {text.lower()} "
    return any(k.lower() in t for k in keywords)


def fetch_feeds(cfg: dict) -> list[dict]:
    ncfg = cfg["news"]
    cutoff = time.time() - ncfg["max_age_hours"] * 3600
    items = []
    for feed in ncfg["feeds"]:
        try:
            r = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
        except Exception as e:  # one broken feed must not stop the run
            log.warning("feed %s failed: %s", feed["name"], e)
            continue
        n = 0
        for e in parsed.entries:
            ts = _entry_time(e)
            if not ts or ts < cutoff or not e.get("link"):
                continue
            title = _clean(e.get("title", ""), 200)
            summary = _clean(e.get("summary") or e.get("description") or "")
            if feed.get("filter") and not _matches(f"{title} {summary}", ncfg["keywords"]):
                continue
            items.append({"title": title, "url": e.link, "source": feed["name"],
                          "published": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                          "summary": summary, "score": 0})
            n += 1
        log.info("feed %-16s %d items", feed["name"], n)
    return items


def fetch_hn(cfg: dict) -> list[dict]:
    hcfg = cfg["news"].get("hacker_news", {})
    if not hcfg.get("enabled"):
        return []
    since = int(time.time() - cfg["news"]["max_age_hours"] * 3600)
    out = []
    for q in ("AI", "LLM", "OpenAI", "Anthropic", "model"):
        try:
            r = requests.get("https://hn.algolia.com/api/v1/search",
                             params={"query": q, "tags": "story",
                                     "numericFilters": f"created_at_i>{since},points>{hcfg.get('min_points', 150)}"},
                             headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.warning("hacker news failed: %s", e)
            break
        for h in r.json().get("hits", []):
            if not h.get("url") or not _matches(h.get("title", ""), cfg["news"]["keywords"]):
                continue
            out.append({"title": h["title"], "url": h["url"], "source": "Hacker News",
                        "published": h["created_at"], "summary": "", "score": h.get("points", 0)})
    log.info("feed %-16s %d items", "Hacker News", len(out))
    return out


def _page_description(url: str) -> str:
    """For items without a summary (e.g. Hacker News): read the page's meta description."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        r.raise_for_status()
        page = r.text[:400_000]
    except Exception:
        return ""
    page = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", page, flags=re.S | re.I)
    parts = []
    for prop in ("og:description", "description", "twitter:description"):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)', page, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']', page, re.I)
        if m:
            parts.append(m.group(1))
            break
    # first meaningful paragraphs
    for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)[:12]:
        t = _clean(p, 400)
        if len(t) > 80 and "{" not in t and ";--" not in t:
            parts.append(t)
        if sum(len(x) for x in parts) > 700:
            break
    return _clean(" ".join(parts), 900)


def _id(item) -> str:
    return hashlib.sha1(_norm_url(item["url"]).encode()).hexdigest()[:16]


def dedupe(items: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_urls = {}
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        u = _norm_url(it["url"])
        if u in seen_urls:
            continue
        dup = None
        for m in merged:
            if SequenceMatcher(None, _key(m["title"]), _key(it["title"])).ratio() > 0.72:
                dup = m
                break
        if dup:
            if it["source"] not in dup["also"]:
                dup["also"].append(it["source"])
            if len(it["summary"]) > len(dup["summary"]):
                dup["summary"] = it["summary"]
            dup["score"] = max(dup["score"], it["score"])
        else:
            merged.append({**it, "also": [], "id": _id(it)})
        seen_urls[u] = True
    return merged


def load_history(cfg) -> dict:
    hist = read_json(HISTORY, {"used": []})
    keep = time.time() - cfg["news"]["history_days"] * 86400
    hist["used"] = [h for h in hist["used"] if h.get("ts", 0) > keep]
    return hist


def save_history(cfg, stories: list[dict]):
    hist = load_history(cfg)
    for s in stories:
        hist["used"].append({"id": _id(s), "title": s.get("title", s.get("headline_fr", "")),
                             "url": s["url"], "ts": time.time()})
    write_json(HISTORY, hist)


def collect(cfg: dict) -> list[dict]:
    items = dedupe(fetch_feeds(cfg) + fetch_hn(cfg))
    hist = load_history(cfg)
    used_ids = {h["id"] for h in hist["used"]}
    used_titles = [_key(h["title"]) for h in hist["used"]]
    fresh = [i for i in items
             if i["id"] not in used_ids
             and not any(SequenceMatcher(None, _key(i["title"]), t).ratio() > 0.72 for t in used_titles)]
    # More sources covering a story + HN points = more important
    fresh.sort(key=lambda x: (len(x["also"]) * 50 + x["score"], x["published"]), reverse=True)
    out = fresh[: cfg["news"]["max_candidates"]]
    for it in out:
        if len(it["summary"]) < 80:
            it["summary"] = _page_description(it["url"]) or it["summary"]
    log.info("candidates: %d (from %d unique, %d total)", len(out), len(items), len(fresh))
    return out
