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
    
    # Start the Flask web server
    app.run(host='0.0.0.0', port=10000)
