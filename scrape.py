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

from bot import scrape_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("affichagebot.scrape")

CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")


def main():
    data = scrape_data()
    if data is None:
        log.warning("faculty site unreachable, keeping old cache")
        return 0

    old_data = {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            old_data = json.load(f).get("data", {})
    except Exception:
        pass

    n_mods = sum(len(m) for m in data.values())
    n_links = sum(len(v) for m in data.values() for v in m.values())
    if old_data == data:
        log.info("cache unchanged (%d modules, %d links)", n_mods, n_links)
        return 0

    payload = {"scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, CACHE_FILE)
    log.info("cache updated (%d modules, %d links)", n_mods, n_links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
