// Affichage Bot - Cloudflare Worker front-end (instant replies, free tier).
// Telegram webhook POSTs updates here. PDFs pass by URL. KV binding: BOTSTATE.
// Secrets: BOT_TOKEN, ADMIN_CHAT_ID. Cron: */5 * * * * . Cache: repo cache.json.

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
const alertAdmin = (env, text) =>
  env.ADMIN_CHAT_ID ? tg(env, "sendMessage", { chat_id: env.ADMIN_CHAT_ID, text: text.slice(0, 4000) }) : Promise.resolve({});
const answerCb = (env, id) => tg(env, "answerCallbackQuery", { callback_query_id: id });
const sendDoc = (env, chat_id, url, caption) =>
  tg(env, "sendDocument", { chat_id, document: url, caption: (caption || "").slice(0, 1000) });

async function getCache() {
  const r = await fetch(CACHE_URL, { cf: { cacheTtl: 300 } });
  if (!r.ok) return {};
  const j = await r.json();
  // attach exams/listes so callers don't re-fetch cache.json
  const c = j.data || {};
  c.exams = j.exams || [];
  c.listes = j.listes || [];
  return c;
}
async function getExams() {
  try { return (await getCache()).exams || []; } catch { return []; }
}
async function getListes() {
  try { return (await getCache()).listes || []; } catch { return []; }
}
async function doPlanning(env, chat, exams) {
  if (!exams.length) {
    await sendMessage(env, chat, "📅 Aucun planning d'examen en ligne pour le moment.");
    return;
  }
  const byYear = {};
  for (const ex of exams) (byYear[ex.year || "?"] = byYear[ex.year || "?"] || []).push(ex);
  let resp = "📅 Plannings des examens:\n";
  for (const [year, items] of Object.entries(byYear)) {
    resp += `\n${year}:\n`;
    for (const ex of items.slice(0, 6)) resp += `  [${ex.kind || "Examen"}] ${ex.text || ""}\n  ${ex.url || ""}\n`;
  }
  await sendMessage(env, chat, resp.slice(0, 4000));
}
async function doListes(env, chat, listes) {
  if (!listes.length) {
    await sendMessage(env, chat, "📋 Aucune liste en ligne pour le moment.");
    return;
  }
  const byYear = {};
  for (const it of listes) (byYear[it.year || "?"] = byYear[it.year || "?"] || []).push(it);
  let resp = "📋 Listes des étudiants / salles:\n";
  for (const [year, items] of Object.entries(byYear)) {
    resp += `\n${year}:\n`;
    for (const it of items.slice(0, 8)) resp += `  [${it.kind || "Listes"}] ${it.text || ""}\n  ${it.url || ""}\n`;
  }
  await sendMessage(env, chat, resp.slice(0, 4000));
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
  // migrate legacy single-group subs to pairs (once)
  let changed = false;
  for (const [chat, list] of Object.entries(s)) {
    const seen = new Set();
    const out = [];
    for (const sub of list) {
      sub.group = normalizeGroup(sub.group);
      const k = `${sub.year}|${sub.module}|${sub.group}`;
      if (!seen.has(k)) {
        seen.add(k);
        out.push(sub);
      }
    }
    if (out.length !== list.length || out.some((o, i) => o.group !== list[i].group)) {
      s[chat] = out;
      changed = true;
    }
  }
  if (changed) await env.BOTSTATE.put("subs", JSON.stringify(s));
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
const groupMarkers = (hay) => ["G01", "G02", "G03", "G04", "G05", "G06"].filter((m) => hay.includes(m));
const isInfoTrack = (link) => {
  const t = ((link.text || "") + " " + (link.url || "")).toLowerCase();
  return t.includes("medecine_informatique") || t.includes("medecine-informatique") || t.includes("med_inf");
};
const trackVariants = (links) => {
  const f = (links || []).map(isInfoTrack);
  return f.some(Boolean) && !f.every(Boolean);
};
const trackLabel = (t) => ({ MED: "Médecine", INFO: "Médecine-Info" }[t] || "Tous");
const matchesSub = (link, sub) => {
  const g = sub.group || "ALL";
  const t = (sub.track || "ALL").toUpperCase();
  // group part
  const hay = ((link.text || "") + " " + (link.url || "")).toUpperCase();
  let gok;
  if (g === "ALL") gok = true;
  else if (!groupMarkers(hay).length) gok = true;
  else if (PAIRS[g.toUpperCase()]) gok = PAIRS[g.toUpperCase()].some((m) => hay.includes(m));
  else gok = hay.includes(g.toUpperCase());
  if (!gok) return false;
  if (t === "ALL") return true;
  const info = isInfoTrack(link);
  return t === "INFO" ? info : !info;
};
const PAIRS = { "G01-G02": ["G01", "G02"], "G03-G04": ["G03", "G04"], "G05-G06": ["G05", "G06"] };
const normalizeGroup = (g) => {
  const G = (g || "").toUpperCase();
  for (const [pair, members] of Object.entries(PAIRS)) {
    if (G === pair || members.includes(G)) return pair;
  }
  return G === "ALL" ? "ALL" : g;
};
const tag = (link) => (link && link.kind ? `[${link.kind}] ` : "");
const mtime = (link) => {
  try {
    const m = new URL(link.url).searchParams.get("m");
    return m && /^\d+$/.test(m) ? parseInt(m, 10) : 0;
  } catch { return 0; }
};

const HELP =
  "/start — choisir année → module → groupe et recevoir les PDF\n" +
  "/subs — voir mes abonnements (touchez ❌ pour retirer)\n" +
  "/unsub <n> ou /unsub all — supprimer\n" +
  "/search <mot> — chercher (ex: /search cardio)\n" +
  "/recent — derniers fichiers publiés\n" +
  "/planning — plannings des examens\n" +
  "/listes — listes étudiants / salles\n" +
  "/help — ce message";

async function doSearch(env, chat, cache, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) {
    await sendMessage(env, chat, "Usage: /search <mot>  (ex: /search cardio)");
    return;
  }
  const hits = [];
  for (const [year, mods] of Object.entries(cache)) {
    for (const [mod, links] of Object.entries(mods)) {
      if (mod.toLowerCase().includes(q)) hits.push({ year, mod, link: null });
      for (const link of links) {
        if ((link.text || "").toLowerCase().includes(q)) hits.push({ year, mod, link });
      }
    }
  }
  if (!hits.length) {
    await sendMessage(env, chat, `Aucun résultat pour « ${query.trim()} ».`);
    return;
  }
  let resp = `🔎 Résultats pour « ${query.trim()} »:\n`;
  for (const h of hits.slice(0, 10)) {
    resp += h.link
      ? `\n${h.year} — ${h.mod}\n${tag(h.link)}${h.link.text}\n${h.link.url}\n`
      : `\n${h.year} — module: ${h.mod}\n`;
  }
  if (hits.length > 10) resp += `\n… +${hits.length - 10} autres.`;
  await sendMessage(env, chat, resp.slice(0, 4000));
}

async function doRecent(env, chat, cache, n = 8) {
  const all = [];
  for (const [year, mods] of Object.entries(cache)) {
    for (const [mod, links] of Object.entries(mods)) {
      for (const link of links) all.push({ ts: mtime(link), year, mod, link });
    }
  }
  all.sort((a, b) => b.ts - a.ts);
  if (!all.length) {
    await sendMessage(env, chat, "Aucun fichier en ligne pour le moment.");
    return;
  }
  let resp = "🆕 Derniers affichages:\n";
  for (const h of all.slice(0, n)) resp += `\n${h.year} — ${h.mod}\n${tag(h.link)}${h.link.text}\n`;
  await sendMessage(env, chat, resp.slice(0, 4000));
}

async function showSubs(env, chat, subs) {
  if (!subs.length) {
    await sendMessage(env, chat, "Vous n'avez aucun abonnement. Tapez /start pour en créer.");
    return;
  }
  let resp = "Vos abonnements (touchez ❌ pour retirer):\n";
  const kb = [];
  subs.forEach((s, i) => {
    const extra = (s.track && s.track !== "ALL") ? ` - ${trackLabel(s.track)}` : "";
    resp += `${i + 1}. ${s.year} - ${s.module} - ${s.group}${extra}\n`;
    kb.push([{ text: `❌ ${i + 1}. ${s.module.slice(0, 25)} (${s.group})`, callback_data: `u_${i}` }]);
  });
  await sendMessage(env, chat, resp, { inline_keyboard: kb });
}

async function handleUpdate(env, u) {
  const msg = u.message;
  const cb = u.callback_query;

  if (msg && msg.text) {
    const chat = String(msg.chat.id);
    const text = msg.text.trim();
    if (text === "/start") {
      await sendMessage(env, chat,
        "Bienvenue! 🎓\nSélectionnez l'année à surveiller:\n\nCommandes: /subs • /search • /recent • /planning • /listes • /help",
        { inline_keyboard: [
          [{ text: "1ère Année", callback_data: "y_1" }, { text: "2ème Année", callback_data: "y_2" }],
          [{ text: "3ème Année", callback_data: "y_3" }, { text: "4ème Année", callback_data: "y_4" }],
          [{ text: "5ème Année", callback_data: "y_5" }, { text: "6ème Année", callback_data: "y_6" }],
        ] });
    } else if (text.startsWith("/subs")) {
      await showSubs(env, chat, (await getSubs(env))[chat] || []);
    } else if (text.startsWith("/unsub")) {
      const subs = await getSubs(env);
      const mine = subs[chat] || [];
      const arg = (text.split(/\s+/)[1] || "").toLowerCase();
      if (arg === "all") {
        subs[chat] = [];
        await env.BOTSTATE.put("subs", JSON.stringify(subs));
        await sendMessage(env, chat, `🗑️ ${mine.length} abonnement(s) supprimé(s).`);
      } else {
        const n = parseInt(arg, 10);
        if (n >= 1 && n <= mine.length) {
          const [rm] = mine.splice(n - 1, 1);
          subs[chat] = mine;
          await env.BOTSTATE.put("subs", JSON.stringify(subs));
          await sendMessage(env, chat, `🗑️ Supprimé: ${rm.year} - ${rm.module} - ${rm.group}`);
        } else {
          await showSubs(env, chat, mine);
          await sendMessage(env, chat, "Usage: /unsub <numéro> ou /unsub all  (ex: /unsub 1)");
        }
      }
    } else if (text.startsWith("/search")) {
      await doSearch(env, chat, await getCache(), text.slice("/search".length));
    } else if (text.startsWith("/recent")) {
      await doRecent(env, chat, await getCache());
    } else if (text.startsWith("/planning")) {
      await doPlanning(env, chat, await getExams());
    } else if (text.startsWith("/listes")) {
      await doListes(env, chat, await getListes());
    } else if (text.startsWith("/help")) {
      await sendMessage(env, chat, "🤖 Affichage Bot — notifie dès qu'une note est publiée.\n\n" + HELP);
    }
    return;
  }

  if (cb) {
    const chat = String(cb.message.chat.id);
    const data = cb.data || "";
    await answerCb(env, cb.id).catch(() => {});
    if (data.startsWith("u_")) {
      const subs = await getSubs(env);
      const mine = subs[chat] || [];
      const idx = parseInt(data.split("_")[1], 10);
      if (idx >= 0 && idx < mine.length) {
        const [rm] = mine.splice(idx, 1);
        subs[chat] = mine;
        await env.BOTSTATE.put("subs", JSON.stringify(subs));
        await sendMessage(env, chat, `🗑️ Supprimé: ${rm.year} - ${rm.module} - ${rm.group}`);
        await showSubs(env, chat, mine);
      } else {
        await sendMessage(env, chat, "Déjà supprimé. Tapez /subs.");
      }
      return;
    }
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
      const modLinks = (cache[y] || {})[mod] || [];
      const modHay = modLinks.map((l) => `${l.text || ""} ${l.url || ""}`.toUpperCase()).join(" ");
      if (modLinks.length && !groupMarkers(modHay).length) {
        if (trackVariants(modLinks)) {
          await sendMessage(env, chat, `Module: ${mod}\nVotre filière :`, { inline_keyboard: [
            [{ text: "Médecine", callback_data: `s_${yn}_${mi}_ALL_MED` }],
            [{ text: "Médecine-Informatique", callback_data: `s_${yn}_${mi}_ALL_INFO` }],
            [{ text: "Tous", callback_data: `s_${yn}_${mi}_ALL_ALL` }],
          ] });
          return;
        }
        await sendMessage(env, chat, `Module: ${mod}\nPas de groupes pour ce module :`, { inline_keyboard: [
          [{ text: "Tous (promo entière)", callback_data: `s_${yn}_${mi}_ALL` }],
        ] });
        return;
      }
      await sendMessage(env, chat, `Module: ${mod}\nChoisissez le groupe:`, { inline_keyboard: [
        [{ text: "G01 - G02", callback_data: `s_${yn}_${mi}_G01-G02` }],
        [{ text: "G03 - G04", callback_data: `s_${yn}_${mi}_G03-G04` }],
        [{ text: "G05 - G06", callback_data: `s_${yn}_${mi}_G05-G06` }],
        [{ text: "Tous les groupes", callback_data: `s_${yn}_${mi}_ALL` }],
      ] });
    } else if (data.startsWith("s_")) {
      const parts = data.split("_");
      const yn = parts[1], mi = parts[2];
      const group = parts[3] || "ALL", track = parts[4] || "ALL";
      const y = yearStr(yn);
      const mod = Object.keys(cache[y] || {})[parseInt(mi, 10)];
      if (!mod) {
        await sendMessage(env, chat, "Erreur. Tapez /start pour recommencer.");
        return;
      }
      const subs = await getSubs(env);
      subs[chat] = subs[chat] || [];
      const sub = { year: y, module: mod, group, track };
      if (!subs[chat].some((s) => s.year === y && s.module === mod && s.group === group && (s.track || "ALL") === track)) {
        subs[chat].push(sub);
        await env.BOTSTATE.put("subs", JSON.stringify(subs));
      }
      const extra = track !== "ALL" ? `\nFilière: ${trackLabel(track)}` : "";
      await sendMessage(env, chat,
        `✅ Abonnement ajouté!\nAnnée: ${y}\nModule: ${mod}\nGroupe: ${group}${extra}\nTapez /subs pour voir vos abonnements.`);
      const seen = await getSeen(env);
      let sent = 0;
      for (const link of (cache[y][mod] || [])) {
        if (!matchesSub(link, sub)) continue;
        if (sent >= MAX_EXISTING) {
          await sendMessage(env, chat, "… et d'autres fichiers déjà en ligne.");
          break;
        }
        await sendMessage(env, chat, `📄 Affichage existant: ${tag(link)}${link.text}`);
        await sendDoc(env, chat, link.url, `${y} - ${mod} (${group})`);
        sent++;
        if (!seen.has(link.url)) seen.add(link.url);
      }
      if (!sent) await sendMessage(env, chat, "Aucun fichier actuellement pour ce groupe. Vous serez notifié dès publication. 🔔");
      await env.BOTSTATE.put("seen", JSON.stringify([...seen]));
    }
  }
}

// Fast path (every 5 min): grades for subscribers + exam plannings broadcast.
// Fetches cache.json ONCE per run (exams derived from it) to save KV/HTTP ops.
// Also performs the daily alive ping (guarded by a last_ping marker).
async function notifyFast(env) {
  const [cache, seen, subs] = [await getCache(), await getSeen(env), await getSubs(env)];
  const exams = (cache && cache.exams) ? cache.exams : (await getExams());
  let added = false;
  for (const [chat, list] of Object.entries(subs)) {
    for (const sub of list) {
      for (const link of (((cache[sub.year] || {})[sub.module]) || [])) {
        if (!matchesSub(link, sub) || seen.has(link.url)) continue;
        const trk = (sub.track && sub.track !== "ALL") ? `\nFilière: ${trackLabel(sub.track)}` : "";
        await sendMessage(env, chat,
          `🚨 NOUVEL AFFICHAGE!\nAnnée: ${sub.year}\nModule: ${sub.module}\nGroupe: ${sub.group}${trk}\nFichier: ${tag(link)}${link.text}`);
        await sendDoc(env, chat, link.url, `${sub.year} - ${sub.module} (${sub.group})`);
        seen.add(link.url);
        added = true;
      }
    }
  }
  // exam plannings: broadcast to EVERY known user
  const chats = Object.keys(subs);
  for (const ex of (exams || [])) {
    if (!ex.url || seen.has(ex.url)) continue;
    for (const chat of chats) {
      await sendMessage(env, chat,
        `📅 NOUVEAU PLANNING!\nAnnée: ${ex.year || "?"}\nType: ${ex.kind || "Examen"}\nFichier: ${ex.text || ""}`);
      await sendDoc(env, chat, ex.url, `${ex.year || ""} - ${ex.kind || "Examen"}`);
    }
    seen.add(ex.url);
    added = true;
  }
  if (added) await env.BOTSTATE.put("seen", JSON.stringify([...seen]));
  // daily alive ping (max once per UTC day)
  if (env.ADMIN_CHAT_ID) {
    const today = new Date().toISOString().slice(0, 10);
    const last = await env.BOTSTATE.get("last_ping");
    if (last !== today) {
      await env.BOTSTATE.put("last_ping", today);
      await sendMessage(env, env.ADMIN_CHAT_ID, "✅ worker alive — scrape + webhook OK");
    }
  }
  return added;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // authenticated KV backup for disaster recovery (BACKUP_KEY secret)
    if (url.pathname === "/backup") {
      if (url.searchParams.get("key") !== env.BACKUP_KEY) return new Response("forbidden", { status: 403 });
      const subs = (await env.BOTSTATE.get("subs", "json")) || {};
      const seen = (await env.BOTSTATE.get("seen", "json")) || [];
      return Response.json({ subscriptions: subs, seen_links: seen });
    }
    if (request.method === "POST") {
      try {
        const update = await request.json();
        // process in background, ACK Telegram immediately (<1s latency)
        ctx.waitUntil(
          handleUpdate(env, update).catch((e) => alertAdmin(env, `WORKER update failed: ${String(e).slice(0, 300)}`))
        );
      } catch (e) {
        console.log("bad update", String(e).slice(0, 200));
      }
      return new Response("ok");
    }
    return new Response("affichage bot worker alive. Set Telegram webhook to this URL.");
  },
  async scheduled(event, env) {
    try {
      await notifyFast(env);
    } catch (e) {
      await alertAdmin(env, `WORKER notify failed: ${String(e && e.stack || e).slice(0, 500)}`);
    }
  },
};
