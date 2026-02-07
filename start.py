"""
Single entrypoint for Railway: runs both the OAuth callback server (FastAPI)
and the Discord bot in one process. Web server runs in a background thread.
"""
import os
import threading

def run_web():
    import uvicorn
    from verify_service import app
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

def run_bot():
    import config
    import bot
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set.")
    bot.client.run(config.DISCORD_TOKEN)

if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    run_bot()
