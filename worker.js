// Affichage Bot - Cloudflare Worker front-end (instant replies, free tier).
// Telegram webhook POSTs updates here. PDFs pass by URL. KV binding: BOTSTATE.
// Secrets: BOT_TOKEN, ADMIN_CHAT_ID. Cron: */2 * * * * . Cache: repo cache.json.

const CACHE_URL = "https://raw.githubusercontent.com/1khvled/affichagebot/main/cache.json";
const STATE_URL = "https://raw.githubusercontent.com/1khvled/affichagebot/main/bot_state.json";
const MAX_EXISTING = 10;

const yearStr = (n) => (n === "1" ? "1ère Année" : `${n}ème Année`);

async function tg(env, method, payload) {
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await r.json();
  } catch (e) {
    console.log("tg error", method, String(e).slice(0, 200));
    return {};
  }
}
const sendMessage = (env, chat_id, text, reply_markup) =>
  tg(env, "sendMessage", reply_markup ? { chat_id, text, reply_markup } : { chat_id, text });
const answerCb = (env, id) => tg(env, "answerCallbackQuery", { callback_query_id: id });
const sendDoc = (env, chat_id, url, caption) =>
  tg(env, "sendDocument", { chat_id, document: url, caption: (caption || "").slice(0, 1000) });

async function getCache() {
  const r = await fetch(CACHE_URL, { cf: { cacheTtl: 120 } });
  if (!r.ok) return {};
  return (await r.json()).data || {};
}
async function seedFromRepo() {
  try {
    const r = await fetch(STATE_URL, { cf: { cacheTtl: 60 } });
    if (!r.ok) return { subscriptions: {}, seen_links: [] };
    const s = await r.json();
    return { subscriptions: s.subscriptions || {}, seen_links: s.seen_links || [] };
  } catch {
    return { subscriptions: {}, seen_links: [] };
  }
}
async function getSubs(env) {
  let s = await env.BOTSTATE.get("subs", "json");
  if (!s) {
    s = (await seedFromRepo()).subscriptions;
    await env.BOTSTATE.put("subs", JSON.stringify(s));
  }
  return s;
}
async function getSeen(env) {
  let s = await env.BOTSTATE.get("seen", "json");
  if (!s) {
    s = (await seedFromRepo()).seen_links;
    await env.BOTSTATE.put("seen", JSON.stringify(s));
  }
  return new Set(s);
}
const matchesGroup = (link, g) =>
  g === "ALL" || ((link.text || "") + " " + (link.url || "")).toUpperCase().includes(g.toUpperCase());

const HELP =
  "/start — choisir année → module → groupe et recevoir les PDF\n" +
  "/subs — voir mes abonnements\n/unsub <n> — supprimer l'abonnement n\n/help — ce message";

async function handleUpdate(env, u) {
  const msg = u.message;
  const cb = u.callback_query;

  if (msg && msg.text) {
    const chat = String(msg.chat.id);
    const text = msg.text.trim();
    if (text === "/start") {
      await sendMessage(env, chat,
        "Bienvenue! 🎓\nSélectionnez l'année à surveiller:\n\nCommandes: /subs • /unsub • /help",
        { inline_keyboard: [
          [{ text: "1ère Année", callback_data: "y_1" }, { text: "2ème Année", callback_data: "y_2" }],
          [{ text: "3ème Année", callback_data: "y_3" }, { text: "4ème Année", callback_data: "y_4" }],
          [{ text: "5ème Année", callback_data: "y_5" }, { text: "6ème Année", callback_data: "y_6" }],
        ] });
    } else if (text.startsWith("/subs")) {
      const subs = (await getSubs(env))[chat] || [];
      await sendMessage(env, chat, subs.length
        ? "Vos abonnements:\n" + subs.map((s, i) => `${i + 1}. ${s.year} - ${s.module} - ${s.group}`).join("\n") + "\n\nSupprimer: /unsub <numéro>"
        : "Aucun abonnement. Tapez /start pour en créer.");
    } else if (text.startsWith("/unsub")) {
      const subs = await getSubs(env);
      const mine = subs[chat] || [];
      const n = parseInt((text.split(/\s+/)[1] || ""), 10);
      if (n >= 1 && n <= mine.length) {
        const [rm] = mine.splice(n - 1, 1);
        subs[chat] = mine;
        await env.BOTSTATE.put("subs", JSON.stringify(subs));
        await sendMessage(env, chat, `🗑️ Supprimé: ${rm.year} - ${rm.module} - ${rm.group}`);
      } else {
        await sendMessage(env, chat, "Usage: /unsub <numéro>  (ex: /unsub 1). Tapez /subs pour la liste.");
      }
    } else if (text.startsWith("/help")) {
      await sendMessage(env, chat, "🤖 Affichage Bot — notifie dès qu'une note est publiée.\n\n" + HELP);
    }
    return;
  }

  if (cb) {
    const chat = String(cb.message.chat.id);
    const data = cb.data || "";
    await answerCb(env, cb.id).catch(() => {});
    const cache = await getCache();

    if (data.startsWith("y_")) {
      const y = yearStr(data.split("_")[1]);
      const mods = cache[y] || {};
      if (!Object.keys(mods).length) {
        await sendMessage(env, chat, "Aucun module trouvé pour cette année (site momentanément vide — réessayez plus tard).");
        return;
      }
      const kb = Object.keys(mods).map((m, i) => {
        const n = mods[m].length;
        const label = (m.length > 35 ? m.slice(0, 35) + "..." : m) + (n ? ` (${n})` : "");
        return [{ text: label, callback_data: `m_${data.split("_")[1]}_${i}` }];
      });
      await sendMessage(env, chat, `${y}: Choisissez le module:`, { inline_keyboard: kb });
    } else if (data.startsWith("m_")) {
      const [, yn, mi] = data.split("_");
      const y = yearStr(yn);
      const mod = Object.keys(cache[y] || {})[parseInt(mi, 10)];
      if (!mod) {
        await sendMessage(env, chat, "Erreur. Tapez /start pour recommencer.");
        return;
      }
      await sendMessage(env, chat, `Module: ${mod}\nChoisissez le groupe:`, { inline_keyboard: [
        [{ text: "G01", callback_data: `s_${yn}_${mi}_G01` }, { text: "G02", callback_data: `s_${yn}_${mi}_G02` }],
        [{ text: "G03", callback_data: `s_${yn}_${mi}_G03` }, { text: "G04", callback_data: `s_${yn}_${mi}_G04` }],
        [{ text: "G05", callback_data: `s_${yn}_${mi}_G05` }, { text: "G06", callback_data: `s_${yn}_${mi}_G06` }],
        [{ text: "Tous les groupes", callback_data: `s_${yn}_${mi}_ALL` }],
      ] });
    } else if (data.startsWith("s_")) {
      const [, yn, mi, group] = data.split("_");
      const y = yearStr(yn);
      const mod = Object.keys(cache[y] || {})[parseInt(mi, 10)];
      if (!mod) {
        await sendMessage(env, chat, "Erreur. Tapez /start pour recommencer.");
        return;
      }
      const subs = await getSubs(env);
      subs[chat] = subs[chat] || [];
      const sub = { year: y, module: mod, group };
      if (!subs[chat].some((s) => s.year === y && s.module === mod && s.group === group)) {
        subs[chat].push(sub);
        await env.BOTSTATE.put("subs", JSON.stringify(subs));
      }
      await sendMessage(env, chat,
        `✅ Abonnement ajouté!\nAnnée: ${y}\nModule: ${mod}\nGroupe: ${group}\nTapez /subs pour voir vos abonnements.`);
      const seen = await getSeen(env);
      let sent = 0;
      for (const link of (cache[y][mod] || [])) {
        if (!matchesGroup(link, group)) continue;
        if (sent >= MAX_EXISTING) {
          await sendMessage(env, chat, "… et d'autres fichiers déjà en ligne.");
          break;
        }
        await sendMessage(env, chat, `📄 Affichage existant: ${link.text}`);
        await sendDoc(env, chat, link.url, `${y} - ${mod} (${group})`);
        sent++;
        if (!seen.has(link.url)) seen.add(link.url);
      }
      if (!sent) await sendMessage(env, chat, "Aucun fichier actuellement pour ce groupe. Vous serez notifié dès publication. 🔔");
      await env.BOTSTATE.put("seen", JSON.stringify([...seen]));
    }
  }
}

async function notifyNew(env) {
  const [cache, seen, subs] = [await getCache(), await getSeen(env), await getSubs(env)];
  let added = false;
  for (const [chat, list] of Object.entries(subs)) {
    for (const sub of list) {
      for (const link of (((cache[sub.year] || {})[sub.module]) || [])) {
        if (!matchesGroup(link, sub.group) || seen.has(link.url)) continue;
        await sendMessage(env, chat,
          `🚨 NOUVEL AFFICHAGE!\nAnnée: ${sub.year}\nModule: ${sub.module}\nGroupe: ${sub.group}\nFichier: ${link.text}`);
        await sendDoc(env, chat, link.url, `${sub.year} - ${sub.module} (${sub.group})`);
        seen.add(link.url);
        added = true;
      }
    }
  }
  if (added) await env.BOTSTATE.put("seen", JSON.stringify([...seen]));
  return added;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "POST") {
      try {
        const update = await request.json();
        // process in background, ACK Telegram immediately (<1s latency)
        ctx.waitUntil(handleUpdate(env, update).catch((e) => console.log("bg error", String(e).slice(0, 200))));
      } catch (e) {
        console.log("bad update", String(e).slice(0, 200));
      }
      return new Response("ok");
    }
    return new Response("affichage bot worker alive. Set Telegram webhook to this URL.");
  },
  async scheduled(event, env) {
    await notifyNew(env);
    if (new Date().getUTCHours() === 8 && env.ADMIN_CHAT_ID) {
      await sendMessage(env, env.ADMIN_CHAT_ID, "✅ worker alive — scrape + webhook OK");
    }
  },
};
