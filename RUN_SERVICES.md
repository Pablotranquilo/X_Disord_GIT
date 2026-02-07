# Running Both Services

You need **TWO separate terminals** running simultaneously:

## Terminal 1: Verify Service (OAuth Server)
```powershell
cd C:\Antigravity
.\.venv\Scripts\Activate.ps1
python Discord_X_verif\verify_service.py
```
**Keep this running!** You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

## Terminal 2: Discord Bot
```powershell
cd C:\Antigravity
.\.venv\Scripts\Activate.ps1
python Discord_X_verif\bot.py
```
**Keep this running!** You should see:
```
Logged in as X_verification#9649
Database initialized.
Worker started. Waiting for images...
```

## Terminal 3: Ngrok (Optional, if using remote access)
```powershell
ngrok http 8000
```

## Current Status
✅ Bot is running (Terminal 1 shows it's been running for 1m57s)  
❌ Verify service is NOT running (it shut down immediately)  
✅ Ngrok is running but can't connect because verify_service stopped

## Quick Fix
You already have the bot running. Just start verify_service.py in a **new terminal** and keep it running!
