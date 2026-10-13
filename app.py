"""Web entrypoint (Render / Railway / Koyeb).

IMPORTANT: gunicorn imports this module (`gunicorn app:app`), so the bot
thread is started ON IMPORT, not inside `if __name__ == '__main__'`.
The old code only started the bot under `__main__`, which is why the bot
was dead on every host that uses gunicorn (Flask answered, bot never ran).

Run locally:  BOT_TOKEN=... py app.py
Run on host:  gunicorn app:app --workers 1 ...
(workers MUST be 1, otherwise Telegram returns 409 Conflict.)
"""
import logging
import os
import threading

from flask import Flask, jsonify

log = logging.getLogger("affichagebot.web")

app = Flask(__name__)

_bot_thread = None
_bot_started = False


def _start_bot_once():
    global _bot_thread, _bot_started
    if _bot_started:
        return
    if os.environ.get("SKIP_BOT_THREAD") == "1":
        log.warning("SKIP_BOT_THREAD=1, bot thread not started")
        return
    _bot_started = True

    def run_bot():
        import bot as bot_module
        try:
            bot_module.main()
        except SystemExit as e:
            log.error("Bot exited: %s (set BOT_TOKEN env var)", e)
        except Exception:
            log.exception("Bot thread crashed")

    _bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-poller")
    _bot_thread.start()
    log.info("Bot thread started")


# Start on import so gunicorn workers run the bot too.
# Flask's debug reloader imports twice; guard is via _bot_started flag +
# callers should use --reload only in dev.
_start_bot_once()


@app.route("/")
def home():
    return "Bot is running! Ouvrez Telegram et cherchez @afffichagebot puis envoyez /start"


@app.route("/health")
def health():
    alive = _bot_thread is not None and _bot_thread.is_alive()
    return jsonify(status="ok", bot_thread_alive=alive), 200 if alive else 503


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    # threaded=True so /health answers while polling loop runs
    app.run(host="0.0.0.0", port=port, threaded=True)
