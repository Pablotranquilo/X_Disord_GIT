# Quick Start Guide

## Current Status ✅
- **Verify Service**: Already running on port 8000 (DO NOT restart)
- **Bot**: Needs restart to apply new changes

## Restart the Bot

### Step 1: Stop the current bot
Press `Ctrl+C` in the terminal where bot.py is running

### Step 2: Start the updated bot
```powershell
cd C:\Antigravity
.\.venv\Scripts\Activate.ps1
python Discord_X_verif\bot.py
```

### Step 3: Verify startup
You should see:
```
Logged in as X_verification#9649 (ID: 1466831420575186985)
Database initialized.
Worker started. Waiting for images...
```

## Test in Discord

1. Type: `!xlink`
   - Check terminal for: `DEBUG: Received message from...`
   
2. If you see DEBUG messages → Bot is working!
3. If you DON'T see DEBUG messages → Enable Message Content Intent in Discord Developer Portal

## Services Overview

You need 2 terminals running:
- **Terminal 1**: `bot.py` (Discord bot)
- **Terminal 2**: `verify_service.py` (OAuth server) - **ALREADY RUNNING** ✅

Optional:
- **Terminal 3**: `ngrok http 8000` (for external access) - **ALREADY RUNNING** ✅
