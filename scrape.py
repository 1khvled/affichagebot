"""Slow poller: scrapes the faculty site, caches result to cache.json.

Runs every 5 min via the scrape workflow, which commits cache.json when the
data actually changed. The answering loop (check.py) only reads this cache
and never touches the faculty site -> fast runs, no IP-ban risk.
Site-down = warning + exit 0 (no alert spam); code bugs exit nonzero.
"""
import json
import logging
import os
from datetime import datetime, timezone

from bot import admin_alert, scrape_data, scrape_exams, scrape_listes, scrape_news

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("affichagebot.scrape")

CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "history.json")


def main():
    data = scrape_data()
    if data is None:
        log.warning("faculty site unreachable, keeping old cache")
        return 0

    old_data = {}
    old_exams = []
    old_news = []
    old_listes = []
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            old_cache = json.load(f)
            old_data = old_cache.get("data", {})
            old_exams = old_cache.get("exams", [])
            old_news = old_cache.get("news", [])
            old_listes = old_cache.get("listes", [])
    except Exception:
        pass

    n_mods = sum(len(m) for m in data.values())
    n_links = sum(len(v) for m in data.values() for v in m.values())
    old_mods = sum(len(m) for m in old_data.values()) if isinstance(old_data, dict) else 0
    if n_mods == 0 and old_mods > 0:
        # faculty redesign (or parser breakage) — never wipe the cache with this
        admin_alert(f"ALERTE scrape: 0 module détecté (avant: {old_mods}). Structure du site changée ? Cache conservé.")
        log.warning("0 modules scraped, keeping old cache")
        return 0
    if old_mods > 0 and n_mods < old_mods * 0.5:
        admin_alert(f"ALERTE scrape: modules {old_mods} → {n_mods}. Vérifier la structure du site.")
    exams = scrape_exams()
    if exams is None:
        exams = old_exams
        log.warning("exam page unreachable, keeping %d cached exam files", len(exams))
    news = scrape_news()
    if news is None:
        news = old_news
        log.warning("news unreachable, keeping %d cached news", len(news))
    listes = scrape_listes()
    if listes is None:
        listes = old_listes
        log.warning("listes unreachable, keeping %d cached files", len(listes))
    if old_data != data or old_exams != exams or old_news != news or old_listes != listes:
        payload = {"scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "data": data, "exams": exams, "news": news, "listes": listes}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, CACHE_FILE)
        log.info("cache updated (%d modules, %d links, %d exams, %d news, %d listes)",
                 n_mods, n_links, len(exams), len(news), len(listes))
    else:
        log.info("cache unchanged (%d modules, %d links, %d exams, %d news, %d listes)",
                 n_mods, n_links, len(exams), len(news), len(listes))

    # Archive: every file ever seen, with first-seen date. Never shrinks,
    # so removed affichages stay browsable via /history.
    history = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f).get("files", [])
    except Exception:
        pass
    known = {h["url"] for h in history}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    added = 0
    for year, mods in data.items():
        for mod, links in mods.items():
            for link in links:
                if link["url"] not in known:
                    history.append({"url": link["url"], "text": link["text"],
                                    "kind": link.get("kind", ""), "year": year,
                                    "module": mod, "first_seen": today})
                    known.add(link["url"])
                    added += 1
    if added:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"files": history}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, HISTORY_FILE)
    log.info("history: %d files total (+%d new)", len(history), added)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
