# Affichage Bot 🎓

Telegram bot that watches the **Batna 2 Faculty of Medicine** website and notifies
students the moment grades, exam plannings, student lists, or faculty news appear.

**Bot:** [@afffichagebot](https://t.me/afffichagebot) · **Langue:** Français · **Coût:** 0 DA (GitHub + Cloudflare free tiers, no card)

## Commands

| Command | What it does |
|---|---|
| `/start` | Year → module → group/track picker, subscribe |
| `/subs` | My subscriptions (❌ tap to remove) |
| `/unsub <n>` / `/unsub all` | Remove one / all subscriptions |
| `/search <mot>` | Search modules + files, e.g. `/search cardio` |
| `/recent` | Latest published files, newest first |
| `/planning` | Exam plannings (Examen + ECOS) per year |
| `/actus` | Latest faculty news (Nouveautés) |
| `/listes` | Student lists (alphabétique / mérite / stages / salles) |
| `/help` | Help |

Push alerts: 🚨 new grades (subscribers only) · 📅 plannings, 📋 lists, 📰 news (broadcast to **all** users).

## How it works

```
Faculty site ──5 min──▶ scrape.py ──▶ cache.json (in repo)
                                              │
                        ┌─────────────────────┘
                        ▼
Telegram ──webhook──▶ Cloudflare Worker ──reads──▶ cache.json (raw GitHub)
   (instant)               │                        BOTSTATE (KV: subs + seen)
                           └──cron */2──▶ new-PDF notifications + alive ping
```

- **Scraping is centralized** (`scrape.py`, GitHub Actions every 5 min) into
  `cache.json` — the bot never hammers the faculty site from user requests.
- **Replies are instant**: Telegram webhook → Cloudflare Worker (<1s).
  PDFs are passed to Telegram **by URL** (`sendDocument` with `document=url`),
  so the Worker never downloads file bytes (fits the free CPU limits).
- **State** (subscriptions + seen links) lives in Cloudflare KV; the repo's
  `bot_state.json` is the fallback seed. `history.json` archives every file
  ever seen with its first-seen date.
- `answer.yml` (old minute-poller) is **disabled** — webhook + polling conflict
  (Telegram 409). `scrape.yml` + `watchdog.yml` stay on.

## Faculty quirks encoded here

- Years 1–3 are **promotion-wide**: no groups, ever → single "Tous" button,
  group-less files match everyone.
- Groups are **pairs** (`G01-G02`, `G03-G04`, `G05-G06`) — the faculty
  publishes one file per pair.
- 1ère année has **two tracks**: files double as `…_medecine.pdf` vs
  `…_medecine_informatique.pdf` (distinct from the *Biostatistiques et
  Informatique* subject). The bot asks for your filière per module.
- File-type tags come from table columns: `[Notes]` / `[CT]` / `[rattrapage]`.
- The `?m=` cache-buster doubles as publish time (used by `/recent`).

## Deploy your own copy

1. **Scraper (GitHub):** fork → Settings → Secrets → `BOT_TOKEN` (from
   [@BotFather](https://t.me/BotFather)), `ADMIN_CHAT_ID` (your numeric id
   from [@userinfobot](https://t.me/userinfobot)). The `scrape` + `watchdog`
   workflows run as-is. If GitHub's scheduler stalls, trigger
   `workflow_dispatch` externally (e.g. cron-job.org → dispatch API).
2. **Worker (Cloudflare, free, no card):** Workers & Pages → Create Worker →
   paste `worker.js`. Add KV binding `BOTSTATE` (new namespace), secrets
   `BOT_TOKEN` + `ADMIN_CHAT_ID`, cron trigger `*/2 * * * *`, enable
   `workers.dev`, then set the webhook:
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>`
3. Disable `answer.yml` (Actions tab → … → Disable workflow) once the
   webhook answers.

Local dev: `copy .env.example .env` → fill `BOT_TOKEN` → `python check.py`
(one-shot) or `python bot.py` (long-polling, needs workers/answer off).

## Repo map

| File | Role |
|---|---|
| `worker.js` | Live bot: webhook replies + notify cron (Cloudflare) |
| `bot.py` | Scrapers (`notes`/`exams`/`news`/`listes`) + shared logic + polling loop |
| `check.py` | One-shot answer run (backup path; workflow disabled) |
| `scrape.py` | 5-min cache builder (`cache.json` + `history.json`) |
| `app.py`, `Procfile`, `render.yaml` | Legacy Render path (kept as fallback) |
| `.github/workflows/` | `scrape.yml`, `watchdog.yml` on · `answer.yml` off |

## Contribute / reuse for another faculty

The core is generic: point `SITE_URL`/`EXAM_URL`/… at another site, adapt the
table selectors, keep the notifier. PRs welcome — French UI strings preferred.
