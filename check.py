"""One-shot check for cron (GitHub Actions every 10 min).

Same logic as bot.py, but runs ONCE and exits:
  1. fetch getUpdates -> answer /start /subs /unsub + inline buttons
  2. scrape the faculty site -> notify subscribers of NEW pdfs
  3. save state (the workflow commits bot_state.json back to the repo,
     which is how subscriptions survive between runs - no server needed).

Needs BOT_TOKEN env var. Never prints it.
"""
import json
import logging
import os
import time

import bot
from bot import (
    HELP_TEXT,
    answer_callback,
    broadcast_exams,

    get_updates,
    group_markers,
    handle_actus,
    handle_listes,
    handle_planning,
    handle_recent,
    handle_search,
    handle_start,
    handle_subs,
    handle_unsub,
    load_state,
    matches,
    matches_group,
    normalize_subs,
    save_state,
    scrape_data,
    scrape_exams,
    scrape_listes,
    scrape_news,
    send_document,
    send_message,
    tag,
    track_label,
    track_variants,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("affichagebot.cron")

CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")


def load_cache():
    """Faculty data cached in the repo by the scrape workflow (every 5 min).
    The answering loop never hits the faculty site itself -> fast + polite."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
        data = c.get("data", {})
        log.info("cache loaded (%s): %d years, %d exams, %d news, %d listes",
                 c.get("scraped_at", "?"), len(data),
                 len(c.get("exams", [])), len(c.get("news", [])), len(c.get("listes", [])))
        return data, c.get("exams", []), c.get("news", []), c.get("listes", [])
    except Exception as e:
        log.warning("no usable cache: %s", e)
        return None, [], [], []


def handle_update(item, state, cached_data, cached_exams, cached_news, cached_listes):
    update_id = item["update_id"]
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
        elif text.startswith("/planning"):
            handle_planning(chat_id, cached_exams)
        elif text.startswith("/actus"):
            handle_actus(chat_id, cached_news)
        elif text.startswith("/listes"):
            handle_listes(chat_id, cached_listes)
        elif text.startswith("/help"):
            send_message(chat_id, HELP_TEXT)

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
                    send_message(chat_id, f"🗑️ Supprimé: {removed['year']} - {removed['module']} - {removed['group']}")
                    handle_subs(chat_id, state)
                else:
                    send_message(chat_id, "Déjà supprimé. Tapez /subs.")
            except Exception:
                send_message(chat_id, "Erreur. Tapez /subs.")
            state["last_update_id"] = update_id + 1
            save_state(state)
            return

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
                year_str = "1ère Année" if year_num == "1" else f"{year_num}ème Année"
                module_name = list(cached_data[year_str].keys())[int(m_idx)]
                mod_links = cached_data[year_str].get(module_name, [])
                mod_hay = " ".join((l.get("text", "") + " " + l.get("url", "")).upper() for l in mod_links)
                if mod_links and not group_markers(mod_hay):
                    if track_variants(mod_links):
                        markup = {"inline_keyboard": [
                            [{"text": "Médecine", "callback_data": f"s_{year_num}_{m_idx}_ALL_MED"}],
                            [{"text": "Médecine-Informatique", "callback_data": f"s_{year_num}_{m_idx}_ALL_INFO"}],
                            [{"text": "Tous", "callback_data": f"s_{year_num}_{m_idx}_ALL_ALL"}],
                        ]}
                        send_message(chat_id, f"Module: {module_name}\nVotre filière :", markup)
                    else:
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
                year_num, m_idx = parts[1], parts[2]
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
                sent = 0
                for link in cached_data.get(year_str, {}).get(module_name, []):
                    if not matches(link, sub):
                        continue
                    if sent >= 10:
                        send_message(chat_id, "… et d'autres fichiers déjà en ligne.")
                        break
                    send_message(chat_id, f"📄 Affichage existant: {tag(link)}{link['text']}")
                    send_document(chat_id, link["url"], caption=f"{year_str} - {module_name} ({group})")
                    sent += 1
                    if link["url"] not in state.get("seen_links", []):
                        state.setdefault("seen_links", []).append(link["url"])
                    time.sleep(1)
                if sent == 0:
                    send_message(chat_id, "Aucun fichier actuellement pour ce groupe. Vous serez notifié dès publication. 🔔")
                save_state(state)
            except Exception:
                log.exception("subscribe failed")
                send_message(chat_id, "Erreur lors de l'abonnement. Tapez /start pour recommencer.")

    state["last_update_id"] = update_id + 1
    save_state(state)


def main():
    if not bot.TOKEN:
        raise SystemExit("FATAL: BOT_TOKEN env var missing (GitHub secret BOT_TOKEN).")
    state = load_state()
    if normalize_subs(state):
        save_state(state)

    cached_data, cached_exams, cached_news, cached_listes = load_cache()
    if cached_data is None:
        log.warning("no cache found, one live scrape as fallback")
        cached_data = scrape_data() or {}
        cached_exams = scrape_exams() or []
        cached_news = scrape_news() or []
        cached_listes = scrape_listes() or []

    if not state.get("seen_links") and cached_data:
        state["seen_links"] = [
            l["url"] for mods in cached_data.values() for links in mods.values() for l in links
        ]
        save_state(state)

    # 1. Telegram updates (commands + buttons)
    updates = get_updates(state.get("last_update_id"))
    if updates and updates.get("ok"):
        for item in updates.get("result", []):
            try:
                handle_update(item, state, cached_data, cached_exams, cached_news, cached_listes)
            except Exception:
                log.exception("update failed, offset advanced anyway")
                state["last_update_id"] = item["update_id"] + 1
                save_state(state)
            time.sleep(0.5)  # stay under Telegram rate limits

    # 2. New PDFs -> notify subscribers
    seen_links = set(state.get("seen_links", []))
    new_found = False
    for chat, sub_list in state.get("subscriptions", {}).items():
        for sub in sub_list:
            for link in cached_data.get(sub["year"], {}).get(sub["module"], []):
                if not matches(link, sub):
                    continue
                if link["url"] not in seen_links:
                    trk = f"\nFilière: {track_label(sub.get('track'))}" if (sub.get("track") or "ALL") != "ALL" else ""
                    send_message(chat,
                                 f"🚨 NOUVEL AFFICHAGE!\nAnnée: {sub['year']}\nModule: {sub['module']}\n"
                                 f"Groupe: {sub['group']}{trk}\nFichier: {tag(link)}{link['text']}")
                    send_document(chat, link["url"],
                                  caption=f"{sub['year']} - {sub['module']} ({sub['group']})")
                    seen_links.add(link["url"])
                    new_found = True
                    time.sleep(1)
    if set(state.get("seen_links", [])) != seen_links:
        state["seen_links"] = sorted(seen_links)
        save_state(state)
    # 3. New exam plannings -> broadcast to ALL users
    n_ex = broadcast_exams(state, cached_exams)
    if n_ex:
        log.info("broadcast %d new exam files", n_ex)
    # News is NOT auto-broadcast here — handled by the worker's hourly cron
    # (0 * * * *). On-demand /actus still works. Lists never auto-broadcast.
    log.info("check done.")


if __name__ == "__main__":
    main()
