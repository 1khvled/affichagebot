"""
Affichage Bot — Telegram notifier for medecine.univ-batna2.dz/affichage-des-notes
Works for EVERYONE: any chat that sends /start can subscribe. No allow-list.
Token is read from the BOT_TOKEN environment variable (never commit it).
"""
import json
import logging
import os
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()  # local dev: reads BOT_TOKEN from .env (gitignored, never commit it)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("affichagebot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not TOKEN:
    log.error("BOT_TOKEN env var is missing. Set it in Render/Railway or export BOT_TOKEN=...")
    # Don't crash on import (so Flask /health still answers), crash when main() runs.
BASE_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
SITE_URL = "https://medecine.univ-batna2.dz/affichage-des-notes"
SITE_ROOT = "https://medecine.univ-batna2.dz"

STATE_FILE = os.environ.get("STATE_FILE", "bot_state.json")
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "120"))  # seconds, be kind to faculty site
POLL_TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (TelegramBot affichage-notifier; contact: admin) requests",
}

GROUPS = ["G01", "G02", "G03", "G04", "G05", "G06"]
YEARS = ["1ère Année", "2ème Année", "3ème Année", "4ème Année", "5ème Année", "6ème Année"]

session = requests.Session()
session.headers.update(HEADERS)


# ---------- Telegram API ----------

def api_get(method, params=None):
    try:
        r = session.get(f"{BASE_URL}/{method}", params=params, timeout=POLL_TIMEOUT + 10)
        if r.status_code == 409:
            log.error("Telegram 409 Conflict: another bot instance is polling. "
                      "Stop the old deployment/local script (gunicorn workers must be 1).")
            return {}
        if r.status_code != 200:
            log.warning("Telegram %s -> %s: %s", method, r.status_code, r.text[:300])
            return {}
        return r.json()
    except Exception as e:
        log.warning("api_get %s failed: %s", method, e)
        return {}


def api_post(method, data=None, files=None):
    try:
        if files:
            r = session.post(f"{BASE_URL}/{method}", data=data, files=files, timeout=60)
        else:
            r = session.post(f"{BASE_URL}/{method}", json=data, timeout=30)
        if r.status_code == 429:
            try:
                retry = r.json().get("parameters", {}).get("retry_after", 3)
            except Exception:
                retry = 3
            log.warning("Telegram rate-limited (429), sleeping %ss", retry)
            time.sleep(retry + 1)
            return {}
        if r.status_code != 200:
            log.warning("Telegram %s -> %s: %s", method, r.status_code, r.text[:300])
        return r.json() if r.text else {}
    except Exception as e:
        log.warning("api_post %s failed: %s", method, e)
        return {}


def get_updates(offset=None):
    params = {"timeout": POLL_TIMEOUT, "offset": offset}
    return api_get("getUpdates", params)


def answer_callback(callback_query_id):
    api_post("answerCallbackQuery", {"callback_query_id": callback_query_id})


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return api_post("sendMessage", payload)


def send_document(chat_id, document_url, caption=""):
    try:
        log.info("Downloading %s", document_url)
        r = session.get(document_url, timeout=60)
        if r.status_code != 200 or not r.content:
            log.warning("Download failed %s -> %s", document_url, r.status_code)
            send_message(chat_id, f"⚠️ Impossible de télécharger : {document_url}")
            return
        if len(r.content) > 48 * 1024 * 1024:
            send_message(chat_id, f"📄 Fichier trop lourd pour Telegram (>48Mo):\n{document_url}")
            return
        path = urlparse(document_url).path
        filename = (path.rsplit("/", 1)[-1] or "affichage.pdf").split("?")[0]
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        files = {"document": (filename, r.content, "application/pdf")}
        api_post("sendDocument",
                 {"chat_id": chat_id, "caption": caption[:1000]},
                 files=files)
    except Exception as e:
        log.warning("send_document failed: %s", e)


# ---------- Scraper ----------

def _year_name_from_header(text, fallback_idx):
    t = " ".join(text.split())
    for y in YEARS:
        if y.split(" ")[0].lower() in t.lower() or y.lower() in t.lower():
            return y
    # fallback: 1ere, 2eme ... 6eme
    for i in range(1, 7):
        if str(i) in t and ("ann" in t.lower() or "ème" in t or "ère" in t):
            return YEARS[i - 1]
    return YEARS[fallback_idx] if fallback_idx < len(YEARS) else f"Année {fallback_idx + 1}"


def kind_from_header(header):
    """Map a table column header to a file kind tag."""
    h = (header or "").lower()
    if "rattrap" in h:
        return "rattrapage"
    if h.strip() == "ct" or "ct" in h.split():
        return "CT"
    if "note" in h or "epreuve" in h or "épreuve" in h:
        return "Notes"
    return ""


def tag(link):
    """Display prefix for a link, e.g. '[CT] '. Empty when unknown."""
    k = ((link or {}).get("kind") or "").strip()
    return f"[{k}] " if k else ""


def scrape_data():
    """Returns dict {year: {module: [{text, url}]}}.

    IMPORTANT: modules with zero links are KEPT (empty list) so users can
    subscribe in advance even when the faculty site was wiped clean.
    Returns None on network/parse failure (caller must keep old cache).
    """
    try:
        r = session.get(SITE_URL, timeout=30)
        if r.status_code != 200:
            log.warning("Site returned %s", r.status_code)
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # Preferred: each year lives in a div.ready-tabber with an h4 header + table
        data = {}
        tabbers = soup.select("div.ready-tabber")
        tables = []
        if tabbers:
            for idx, div in enumerate(tabbers):
                h4 = div.find(["h4", "h3", "h2"])
                year = _year_name_from_header(h4.get_text(" ", strip=True) if h4 else "", idx)
                tbl = div.find("table")
                if tbl:
                    tables.append((year, tbl))
        if not tables:
            # Fallback: table order == year 1..6 (old behaviour)
            for idx, tbl in enumerate(soup.find_all("table")):
                tables.append((YEARS[idx] if idx < len(YEARS) else f"Année {idx+1}", tbl))

        for year, table in tables:
            modules = data.setdefault(year, {})
            # Column headers tell WHAT each file is (Notes / CT / Rattrapage).
            thead = table.find("thead")
            headers = [th.get_text(" ", strip=True) for th in thead.find_all("th")] if thead else []
            rows = table.find_all("tr")
            # find_all includes the thead header row: drop thead rows when a
            # thead exists, otherwise skip the first row (legacy layout).
            body_rows = [r for r in rows if not r.find_parent("thead")] if thead else rows[1:]
            for row in body_rows:
                cols = row.find_all(["td", "th"])
                if not cols:
                    continue
                module_name = cols[0].get_text(" ", strip=True)
                if not module_name:
                    continue
                links = []
                for ci, col in enumerate(cols):
                    kind = "" if ci == 0 else kind_from_header(headers[ci] if ci < len(headers) else "")
                    for a in col.find_all("a", href=True):
                        href = a["href"].strip()
                        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                            continue
                        url = urljoin(SITE_ROOT + "/", href)
                        if not url.lower().startswith(("http://", "https://")):
                            continue
                        text = a.get_text(" ", strip=True)
                        if not text:
                            # anchors are often empty; fall back to file name
                            text = urlparse(url).path.rsplit("/", 1)[-1].split("?")[0] or url
                        links.append({"text": text, "url": url, "kind": kind})
                # keep module even with no links (was: `if links:` — the bug that
                # made the bot show "Aucun module trouvé" after the site was emptied)
                if module_name not in modules:
                    modules[module_name] = []
                # avoid duplicates, preserve order
                seen = {l["url"] for l in modules[module_name]}
                for l in links:
                    if l["url"] not in seen:
                        modules[module_name].append(l)
                        seen.add(l["url"])
        total_mods = sum(len(m) for m in data.values())
        total_links = sum(len(v) for m in data.values() for v in m.values())
        log.info("Scraped %d years, %d modules, %d links", len(data), total_mods, total_links)
        return data
    except Exception as e:
        log.warning("scrape failed: %s", e)
        return None


# ---------- State ----------

def load_state():
    default = {"subscriptions": {}, "last_update_id": None, "seen_links": []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            for k in default:
                s.setdefault(k, default[k])
            return s
        except Exception as e:
            log.warning("load_state failed (%s), using defaults", e)
    return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


# The faculty publishes one file per PAIR (G01-G02, G03-G04, G05-G06),
# so subscriptions are pairs too. Old single-group subs auto-migrate.
PAIRS = {
    "G01-G02": ("G01", "G02"),
    "G03-G04": ("G03", "G04"),
    "G05-G06": ("G05", "G06"),
}
GROUP_CHOICES = ["G01-G02", "G03-G04", "G05-G06", "ALL"]


def normalize_group(group):
    g = (group or "").upper()
    for pair, members in PAIRS.items():
        if g == pair or g in members:
            return pair
    return "ALL" if g == "ALL" else group


def normalize_subs(state):
    """Migrate legacy single-group subs to pairs (dedupes). Returns True if changed."""
    changed = False
    subs = state.get("subscriptions", {})
    for chat, sub_list in subs.items():
        seen, out = set(), []
        for s in sub_list:
            s["group"] = normalize_group(s.get("group", "ALL"))
            key = (s.get("year"), s.get("module"), s["group"])
            if key not in seen:
                seen.add(key)
                out.append(s)
        if out != sub_list:
            subs[chat] = out
            changed = True
    return changed


def group_markers(hay):
    """Group labels (G01..G06) found in text (upper-cased)."""
    return [f"G{i:02d}" for i in range(1, 7) if f"G{i:02d}" in hay]


def is_info_track(link):
    """True if the file is for the Médecine-Informatique track
    (vs regular Médecine). Distinct from the 'Biostatistiques et
    Informatique' SUBJECT, which all 1ère students take."""
    t = ((link or {}).get("text", "") + " " + (link or {}).get("url", "")).lower()
    return ("medecine_informatique" in t or "medecine-informatique" in t
            or "med_inf" in t)


def track_variants(links):
    """Module has BOTH Médecine and Médecine-Informatique files?"""
    flags = [is_info_track(l) for l in (links or [])]
    return any(flags) and not all(flags)


def track_label(track):
    return {"MED": "Médecine", "INFO": "Médecine-Info"}.get(track, "Tous")


def matches(link, sub):
    """Full subscription match: group AND track. Old subs default to ALL."""
    if not matches_group(link, sub.get("group", "ALL")):
        return False
    t = (sub.get("track") or "ALL").upper()
    if t == "ALL":
        return True
    info = is_info_track(link)
    return info if t == "INFO" else not info


def matches_group(link, group):
    if group == "ALL":
        return True
    hay = (link.get("text", "") + " " + link.get("url", "")).upper()
    if not group_markers(hay):
        return True  # promotion-wide file (years 1-3): relevant to everyone
    g = (group or "").upper()
    if g in PAIRS:
        return any(m in hay for m in PAIRS[g])
    # legacy single group (pre-migration)
    return g in hay


# ---------- Bot logic ----------

def file_mtime(link):
    """Faculty Drupal '?m=<unix time>' cache-buster doubles as publish time."""
    try:
        url = (link or {}).get("url", "")
        q = urlparse(url).query
        for part in q.split("&"):
            if part.startswith("m=") and part[2:].isdigit():
                return int(part[2:])
    except Exception:
        pass
    return 0


def handle_start(chat_id):
    markup = {
        "inline_keyboard": [
            [{"text": "1ère Année", "callback_data": "y_1"}, {"text": "2ème Année", "callback_data": "y_2"}],
            [{"text": "3ème Année", "callback_data": "y_3"}, {"text": "4ème Année", "callback_data": "y_4"}],
            [{"text": "5ème Année", "callback_data": "y_5"}, {"text": "6ème Année", "callback_data": "y_6"}],
        ]
    }
    send_message(chat_id,
                 "Bienvenue! 🎓\nSélectionnez l'année à surveiller:\n\n"
                 "Commandes: /subs • /search <mot> • /recent • /help",
                 markup)


def handle_subs(chat_id, state):
    subs = state.get("subscriptions", {}).get(str(chat_id), [])
    if not subs:
        send_message(chat_id, "Vous n'avez aucun abonnement. Tapez /start pour en créer.")
        return
    resp = "Vos abonnements actuels (touchez ❌ pour retirer):\n"
    keyboard = []
    for idx, s in enumerate(subs):
        extra = f" - {track_label(s.get('track'))}" if (s.get("track") or "ALL") != "ALL" else ""
        resp += f"{idx+1}. {s['year']} - {s['module']} - {s['group']}{extra}\n"
        keyboard.append([{"text": f"❌ {idx+1}. {s['module'][:25]} ({s['group']})",
                          "callback_data": f"u_{idx}"}])
    send_message(chat_id, resp, {"inline_keyboard": keyboard})


def handle_unsub(chat_id, state, text):
    chat = str(chat_id)
    subs = state.get("subscriptions", {}).get(chat, [])
    if not subs:
        send_message(chat_id, "Aucun abonnement à supprimer.")
        return
    parts = text.split()
    if len(parts) < 2:
        handle_subs(chat_id, state)
        send_message(chat_id, "Usage: /unsub <numéro> ou /unsub all  (ex: /unsub 1)")
        return
    if parts[1].lower() == "all":
        state["subscriptions"][chat] = []
        save_state(state)
        send_message(chat_id, f"🗑️ {len(subs)} abonnement(s) supprimé(s).")
        return
    if not parts[1].isdigit():
        handle_subs(chat_id, state)
        return
    idx = int(parts[1]) - 1
    if 0 <= idx < len(subs):
        removed = subs.pop(idx)
        state["subscriptions"][chat] = subs
        save_state(state)
        send_message(chat_id, f"🗑️ Abonnement supprimé: {removed['year']} - {removed['module']} - {removed['group']}")
    else:
        send_message(chat_id, "Numéro invalide. Tapez /subs pour voir la liste.")


def handle_search(chat_id, cached_data, query):
    q = (query or "").strip().lower()
    if not q:
        send_message(chat_id, "Usage: /search <mot>  (ex: /search cardio)")
        return
    hits = []
    for year, mods in cached_data.items():
        for mod, links in mods.items():
            if q in mod.lower():
                hits.append((year, mod, None))
            for link in links:
                if q in link.get("text", "").lower():
                    hits.append((year, mod, link))
    if not hits:
        send_message(chat_id, f"Aucun résultat pour « {query} ».")
        return
    resp = f"🔎 Résultats pour « {query} »:\n"
    for year, mod, link in hits[:10]:
        if link:
            resp += f"\n{year} — {mod}\n{tag(link)}{link['text']}\n{link['url']}\n"
        else:
            resp += f"\n{year} — module: {mod}\n"
    if len(hits) > 10:
        resp += f"\n… +{len(hits) - 10} autres."
    send_message(chat_id, resp[:4000])


def handle_recent(chat_id, cached_data, n=8):
    all_links = [(file_mtime(l), y, m, l)
                 for y, mods in cached_data.items()
                 for m, links in mods.items() for l in links]
    all_links.sort(key=lambda t: t[0], reverse=True)
    if not all_links:
        send_message(chat_id, "Aucun fichier en ligne pour le moment.")
        return
    resp = "🆕 Derniers affichages:\n"
    for ts, y, m, link in all_links[:n]:
        resp += f"\n{y} — {m}\n{tag(link)}{link['text']}\n"
    send_message(chat_id, resp[:4000])


HELP_TEXT = (
    "🤖 *Affichage Bot* — notifie dès qu'une note est publiée.\n\n"
    "/start — choisir année → module → groupe et recevoir les PDF\n"
    "/subs — voir mes abonnements (❌ pour retirer)\n"
    "/unsub <n> ou /unsub all — supprimer\n"
    "/search <mot> — chercher (ex: /search cardio)\n"
    "/recent — derniers fichiers publiés\n"
    "/help — ce message\n\n"
    "Ouvert à tout le monde, aucun compte requis."
)


def main():
    if not TOKEN:
        raise SystemExit("FATAL: BOT_TOKEN env var missing. export BOT_TOKEN=<token from @BotFather>")
    log.info("Bot starting (polling, multi-user mode)...")
    state = load_state()
    offset = state.get("last_update_id")

    cached_data = scrape_data()
    if cached_data is None:
        cached_data = {}
    last_scrape = time.time()

    if not state.get("seen_links") and cached_data:
        seen = [l["url"] for mods in cached_data.values() for links in mods.values() for l in links]
        state["seen_links"] = seen
        save_state(state)
        log.info("Initialized %d existing links as seen.", len(seen))

    log.info("Bot is running and listening for Telegram messages...")
    failures = 0
    while True:
        try:
            updates = get_updates(offset)
            if updates and updates.get("ok"):
                failures = 0
                for item in updates.get("result", []):
                    update_id = item["update_id"]
                    offset = update_id + 1
                    state["last_update_id"] = offset
                    save_state(state)

                    msg = item.get("message")
                    cb = item.get("callback_query")

                    if msg and msg.get("text"):
                        chat_id = str(msg["chat"]["id"])
                        text = msg["text"].strip()
                        if text == "/start":
                            handle_start(chat_id)
                        elif text.startswith("/subs"):
                            handle_subs(chat_id, state)
                        elif text.startswith("/unsub"):
                            handle_unsub(chat_id, state, text)
                        elif text.startswith("/search"):
                            handle_search(chat_id, cached_data, text[len("/search"):])
                        elif text.startswith("/recent"):
                            handle_recent(chat_id, cached_data)
                        elif text.startswith("/help"):
                            send_message(chat_id, HELP_TEXT)
                        # ignore other text silently

                    elif cb:
                        try:
                            answer_callback(cb["id"])
                        except Exception:
                            pass
                        chat_id = str(cb["message"]["chat"]["id"])
                        data = cb.get("data", "")

                        if data.startswith("u_"):
                            try:
                                idx = int(data.split("_")[1])
                                subs = state.get("subscriptions", {}).get(chat_id, [])
                                if 0 <= idx < len(subs):
                                    removed = subs.pop(idx)
                                    state["subscriptions"][chat_id] = subs
                                    save_state(state)
                                    send_message(chat_id,
                                                 f"🗑️ Supprimé: {removed['year']} - {removed['module']} - {removed['group']}")
                                    handle_subs(chat_id, state)
                                else:
                                    send_message(chat_id, "Déjà supprimé. Tapez /subs.")
                            except Exception:
                                send_message(chat_id, "Erreur. Tapez /subs.")
                            continue

                        if time.time() - last_scrape > 300 or not cached_data:
                            fresh = scrape_data()
                            if fresh is not None:
                                cached_data = fresh
                            last_scrape = time.time()

                        if data.startswith("y_"):
                            year_num = data.split("_")[1]
                            year_str = "1ère Année" if year_num == "1" else f"{year_num}ème Année"
                            if year_str in cached_data and cached_data[year_str]:
                                keyboard = []
                                for m in cached_data[year_str].keys():
                                    m_idx = list(cached_data[year_str].keys()).index(m)
                                    display_m = (m[:35] + "...") if len(m) > 35 else m
                                    n = len(cached_data[year_str][m])
                                    label = f"{display_m} ({n})" if n else display_m
                                    keyboard.append([{"text": label, "callback_data": f"m_{year_num}_{m_idx}"}])
                                send_message(chat_id, f"{year_str}: Choisissez le module:", {"inline_keyboard": keyboard})
                            else:
                                send_message(chat_id, "Aucun module trouvé pour cette année (site momentanément vide — réessayez plus tard).")

                        elif data.startswith("m_"):
                            try:
                                _, year_num, m_idx = data.split("_")
                                m_idx = int(m_idx)
                                year_str = "1ère Année" if year_num == "1" else f"{year_num}ème Année"
                                module_name = list(cached_data[year_str].keys())[m_idx]
                                mod_links = cached_data[year_str].get(module_name, [])
                                mod_hay = " ".join((l.get("text", "") + " " + l.get("url", "")).upper()
                                                   for l in mod_links)
                                if mod_links and not group_markers(mod_hay):
                                    if track_variants(mod_links):
                                        # 1ère année: two tracks, no groups
                                        markup = {"inline_keyboard": [
                                            [{"text": "Médecine", "callback_data": f"s_{year_num}_{m_idx}_ALL_MED"}],
                                            [{"text": "Médecine-Informatique", "callback_data": f"s_{year_num}_{m_idx}_ALL_INFO"}],
                                            [{"text": "Tous", "callback_data": f"s_{year_num}_{m_idx}_ALL_ALL"}],
                                        ]}
                                        send_message(chat_id, f"Module: {module_name}\nVotre filière :", markup)
                                    else:
                                        # pre-clinical module: no groups exist, single choice
                                        markup = {"inline_keyboard": [[{"text": "Tous (promo entière)",
                                                                       "callback_data": f"s_{year_num}_{m_idx}_ALL"}]]}
                                        send_message(chat_id, f"Module: {module_name}\nPas de groupes pour ce module :", markup)
                                else:
                                    markup = {
                                        "inline_keyboard": [
                                            [{"text": "G01 - G02", "callback_data": f"s_{year_num}_{m_idx}_G01-G02"}],
                                            [{"text": "G03 - G04", "callback_data": f"s_{year_num}_{m_idx}_G03-G04"}],
                                            [{"text": "G05 - G06", "callback_data": f"s_{year_num}_{m_idx}_G05-G06"}],
                                            [{"text": "Tous les groupes", "callback_data": f"s_{year_num}_{m_idx}_ALL"}],
                                        ]
                                    }
                                    send_message(chat_id, f"Module: {module_name}\nChoisissez le groupe:", markup)
                            except Exception:
                                log.exception("module select failed")
                                send_message(chat_id, "Erreur lors de la sélection. Tapez /start pour recommencer.")

                        elif data.startswith("s_"):
                            try:
                                parts = data.split("_")
                                _, year_num, m_idx = parts[0], parts[1], parts[2]
                                group = parts[3] if len(parts) > 3 else "ALL"
                                track = parts[4] if len(parts) > 4 else "ALL"
                                year_str = "1ère Année" if year_num == "1" else f"{year_num}ème Année"
                                module_name = list(cached_data[year_str].keys())[int(m_idx)]
                                subs = state.setdefault("subscriptions", {})
                                subs.setdefault(chat_id, [])
                                sub = {"year": year_str, "module": module_name, "group": group, "track": track}
                                if sub not in subs[chat_id]:
                                    subs[chat_id].append(sub)
                                    save_state(state)
                                extra = f"\nFilière: {track_label(track)}" if track != "ALL" else ""
                                send_message(chat_id,
                                             f"✅ Abonnement ajouté!\nAnnée: {year_str}\nModule: {module_name}\nGroupe: {group}{extra}\n"
                                             "Tapez /subs pour voir vos abonnements, /unsub pour en retirer un.")
                                links = cached_data.get(year_str, {}).get(module_name, [])
                                sent = 0
                                for link in links:
                                    if not matches(link, sub):
                                        continue
                                    if sent >= 10:
                                        send_message(chat_id, f"... et {len(links)-sent} fichier(s) de plus déjà en ligne.")
                                        break
                                    send_message(chat_id, f"📄 Affichage existant: {tag(link)}{link['text']}")
                                    send_document(chat_id, link["url"], caption=f"{year_str} - {module_name} ({group})")
                                    sent += 1
                                    if link["url"] not in state.get("seen_links", []):
                                        state.setdefault("seen_links", []).append(link["url"])
                                    time.sleep(1)  # avoid 429 flood
                                if sent == 0:
                                    send_message(chat_id, "Aucun fichier actuellement pour ce groupe. Vous serez notifié dès publication. 🔔")
                                save_state(state)
                            except Exception:
                                log.exception("subscribe failed")
                                send_message(chat_id, "Erreur lors de l'abonnement. Tapez /start pour recommencer.")
            else:
                failures += 1
        except Exception:
            log.exception("loop error")
            failures += 1

        # 2. Check website for updates
        try:
            if time.time() - last_scrape > SCRAPE_INTERVAL:
                fresh = scrape_data()
                if fresh is not None:
                    cached_data = fresh
                    subs = state.get("subscriptions", {})
                    seen_links = set(state.get("seen_links", []))
                    new_found = False
                    for chat, sub_list in subs.items():
                        for sub in sub_list:
                            y, m, g = sub["year"], sub["module"], sub["group"]
                            for link in cached_data.get(y, {}).get(m, []):
                                if not matches(link, sub):
                                    continue
                                if link["url"] not in seen_links:
                                    trk = f"\nFilière: {track_label(sub.get('track'))}" if (sub.get("track") or "ALL") != "ALL" else ""
                                    send_message(chat,
                                                 f"🚨 NOUVEL AFFICHAGE!\nAnnée: {y}\nModule: {m}\nGroupe: {g}{trk}\nFichier: {tag(link)}{link['text']}")
                                    send_document(chat, link["url"], caption=f"{y} - {m} ({g})")
                                    seen_links.add(link["url"])
                                    new_found = True
                                    time.sleep(1)
                    if new_found:
                        state["seen_links"] = list(seen_links)
                    save_state(state)
                last_scrape = time.time()
        except Exception:
            log.exception("scrape/notify error")

        time.sleep(1 if failures == 0 else min(30, 2 ** failures))


if __name__ == "__main__":
    main()
