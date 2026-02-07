import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
VERIFY_CHANNEL = (os.getenv("VERIFY_CHANNEL", "verify") or "").strip()

# X OAuth2 Settings
X_CLIENT_ID = os.getenv("X_CLIENT_ID", "")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET", "")
X_REDIRECT_URI = os.getenv("X_REDIRECT_URI", "")
X_SCOPES = os.getenv("X_SCOPES", "users.read tweet.read")

# Callback Server Settings
OAUTH_HOST = os.getenv("OAUTH_HOST", "0.0.0.0")
OAUTH_PORT = os.getenv("OAUTH_PORT", "8000")
