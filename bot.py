import requests
from bs4 import BeautifulSoup
import time
import json
import os

TOKEN = "8569679779:AAFImZ_Yvcoxz9o2vhmIV3XP9ydVaAS_QW8"
CHAT_ID = "1759675108"
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
SITE_URL = "https://medecine.univ-batna2.dz/affichage-des-notes"

STATE_FILE = "bot_state.json"

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {'timeout': 10, 'offset': offset}
    try:
        r = requests.get(url, params=params, verify=False, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Error getting updates: {e}")
    return {}

def send_message(chat_id, text, reply_markup=None):
    url = f"{BASE_URL}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text}
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        requests.post(url, json=payload, verify=False)
    except Exception as e:
        print(f"Error sending message: {e}")

def send_document(chat_id, document_url, caption=""):
    url = f"{BASE_URL}/sendDocument"
    payload = {'chat_id': chat_id, 'caption': caption}
    
    try:
        print(f"Downloading {document_url}")
        r = requests.get(document_url, verify=False)
        filename = document_url.split('/')[-1].split('?')[0]
        if not filename.endswith('.pdf'):
            filename += ".pdf"
        files = {'document': (filename, r.content)}
        requests.post(url, data=payload, files=files, verify=False)
    except Exception as e:
        print(f"Error sending document: {e}")

def scrape_data():
    try:
        r = requests.get(SITE_URL, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table')
        
        data = {}
        for year_idx, table in enumerate(tables):
            year_name = f"{year_idx + 1}ème Année" if year_idx > 0 else "1ère Année"
            modules = {}
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if not cols: continue
                module_name = cols[0].get_text(strip=True)
                if not module_name: continue
                
                links = []
                for col in cols[1:]:
                    for a in col.find_all('a', href=True):
                        href = a['href']
                        if not href.startswith('http'):
                            if href.startswith('/'):
                                href = "https://medecine.univ-batna2.dz" + href
                            else:
                                continue
                        links.append({
                            'text': a.get_text(strip=True),
                            'url': href
                        })
                if links:
                    modules[module_name] = links
            if modules:
                data[year_name] = modules
        return data
    except Exception as e:
        print(f"Error scraping data: {e}")
        return {}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"subscriptions": {}, "last_update_id": None, "seen_links": []}

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def main():
    import urllib3
    urllib3.disable_warnings()

    state = load_state()
    offset = state.get("last_update_id")
    
    print("Bot is starting, fetching initial data...")
    cached_data = scrape_data()
    last_scrape = time.time()
    
    # Initialize seen links so we don't spam existing PDFs on new subscriptions
    if not state.get("seen_links") and cached_data:
        seen = []
        for y, modules in cached_data.items():
            for m, links in modules.items():
                for link in links:
                    seen.append(link['url'])
        state['seen_links'] = seen
        save_state(state)
        print(f"Initialized {len(seen)} existing links as seen.")
    
    print("Bot is running and listening for Telegram messages...")
    
    while True:
        # 1. Process Telegram Messages
        updates = get_updates(offset)
        if updates and updates.get('ok'):
            for item in updates['result']:
                update_id = item['update_id']
                offset = update_id + 1
                state['last_update_id'] = offset
                save_state(state)
                
                msg = item.get('message')
                cb = item.get('callback_query')
                
                if msg and msg.get('text'):
                    chat_id = str(msg['chat']['id'])
                    text = msg['text']
                    
                    if text == '/start':
                        markup = {
                            "inline_keyboard": [
                                [{"text": "1ère Année", "callback_data": "y_1"}, {"text": "2ème Année", "callback_data": "y_2"}],
                                [{"text": "3ème Année", "callback_data": "y_3"}, {"text": "4ème Année", "callback_data": "y_4"}],
                                [{"text": "5ème Année", "callback_data": "y_5"}, {"text": "6ème Année", "callback_data": "y_6"}],
                            ]
                        }
                        send_message(chat_id, "Bienvenue! Sélectionnez l'année à surveiller:", markup)
                    elif text == '/subs':
                        subs = state.get("subscriptions", {}).get(chat_id, [])
                        if not subs:
                            send_message(chat_id, "Vous n'avez aucun abonnement. Tapez /start pour en créer.")
                        else:
                            resp = "Vos abonnements actuels:\n"
                            for idx, s in enumerate(subs):
                                resp += f"{idx+1}. {s['year']} - {s['module']} - {s['group']}\n"
                            send_message(chat_id, resp)
                
                elif cb:
                    chat_id = str(cb['message']['chat']['id'])
                    data = cb['data']
                    
                    if time.time() - last_scrape > 300 or not cached_data:
                        cached_data = scrape_data()
                        last_scrape = time.time()
                    
                    if data.startswith("y_"):
                        year_num = data.split("_")[1]
                        year_str = f"{year_num}ème Année" if year_num != "1" else "1ère Année"
                        
                        if year_str in cached_data:
                            modules = cached_data[year_str].keys()
                            keyboard = []
                            for m in modules:
                                m_idx = list(cached_data[year_str].keys()).index(m)
                                # Clean module name for display
                                display_m = m[:35] + ("..." if len(m) > 35 else "")
                                keyboard.append([{"text": display_m, "callback_data": f"m_{year_num}_{m_idx}"}])
                            markup = {"inline_keyboard": keyboard}
                            send_message(chat_id, f"{year_str}: Choisissez le module:", markup)
                        else:
                            send_message(chat_id, "Aucun module trouvé pour cette année.")
                            
                    elif data.startswith("m_"):
                        parts = data.split("_")
                        year_num = parts[1]
                        m_idx = int(parts[2])
                        year_str = f"{year_num}ème Année" if year_num != "1" else "1ère Année"
                        try:
                            module_name = list(cached_data[year_str].keys())[m_idx]
                            
                            markup = {
                                "inline_keyboard": [
                                    [{"text": "G01", "callback_data": f"s_{year_num}_{m_idx}_G01"}, {"text": "G02", "callback_data": f"s_{year_num}_{m_idx}_G02"}],
                                    [{"text": "G03", "callback_data": f"s_{year_num}_{m_idx}_G03"}, {"text": "G04", "callback_data": f"s_{year_num}_{m_idx}_G04"}],
                                    [{"text": "G05", "callback_data": f"s_{year_num}_{m_idx}_G05"}, {"text": "G06", "callback_data": f"s_{year_num}_{m_idx}_G06"}],
                                    [{"text": "Tous les groupes", "callback_data": f"s_{year_num}_{m_idx}_ALL"}]
                                ]
                            }
                            send_message(chat_id, f"Module: {module_name}\nChoisissez le groupe:", markup)
                        except Exception as e:
                            send_message(chat_id, "Erreur lors de la sélection.")
                            
                    elif data.startswith("s_"):
                        parts = data.split("_")
                        year_num = parts[1]
                        m_idx = int(parts[2])
                        group = parts[3]
                        year_str = f"{year_num}ème Année" if year_num != "1" else "1ère Année"
                        module_name = list(cached_data[year_str].keys())[m_idx]
                        
                        subs = state.get("subscriptions", {})
                        if chat_id not in subs:
                            subs[chat_id] = []
                        
                        sub = {"year": year_str, "module": module_name, "group": group}
                        if sub not in subs[chat_id]:
                            subs[chat_id].append(sub)
                            state["subscriptions"] = subs
                            save_state(state)
                            
                        send_message(chat_id, f"✅ Abonnement ajouté!\nAnnée: {year_str}\nModule: {module_name}\nGroupe: {group}\nTapez /subs pour voir vos abonnements.")
                        
                        if year_str in cached_data and module_name in cached_data[year_str]:
                            links = cached_data[year_str][module_name]
                            sent_count = 0
                            for link in links:
                                link_url = link['url']
                                link_text = link['text'].upper()
                                if group != "ALL":
                                    if group.upper() not in link_text and group.upper() not in link_url.upper():
                                        continue
                                send_message(chat_id, f"📄 Affichage existant trouvé: {link['text']}")
                                send_document(chat_id, link_url, caption=f"{year_str} - {module_name} ({group})")
                                sent_count += 1
                                if link_url not in state.get('seen_links', []):
                                    state.setdefault('seen_links', []).append(link_url)
                            if sent_count == 0:
                                send_message(chat_id, "Aucun fichier actuellement disponible pour ce groupe. Vous serez notifié dès sa publication.")
                            save_state(state)

        # 2. Check website for updates
        if time.time() - last_scrape > 60:
            cached_data = scrape_data()
            last_scrape = time.time()
            
            subs = state.get("subscriptions", {})
            seen_links = set(state.get("seen_links", []))
            new_links_found = False
            
            for chat, sub_list in subs.items():
                for sub in sub_list:
                    y = sub['year']
                    m = sub['module']
                    g = sub['group']
                    
                    if y in cached_data and m in cached_data[y]:
                        links = cached_data[y][m]
                        for link in links:
                            link_url = link['url']
                            link_text = link['text'].upper()
                            
                            if g != "ALL":
                                if g.upper() not in link_text and g.upper() not in link_url.upper():
                                    continue
                            
                            if link_url not in seen_links:
                                send_message(chat, f"🚨 NOUVEL AFFICHAGE!\nAnnée: {y}\nModule: {m}\nGroupe: {g}\nFichier: {link['text']}")
                                send_document(chat, link_url, caption=f"{y} - {m} ({g})")
                                seen_links.add(link_url)
                                new_links_found = True
            
            if new_links_found:
                state['seen_links'] = list(seen_links)
                save_state(state)
                
        time.sleep(1)

if __name__ == '__main__':
    main()
