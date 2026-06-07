from flask import Flask
import threading
import bot # Import our bot script

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_bot():
    bot.main()

if __name__ == '__main__':
    # Start the bot in a background thread
    t = threading.Thread(target=run_bot)
    t.daemon = True
    t.start()
    
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
