# Database Fix Required

## Problem
The database file `bot_database.db` exists but doesn't have the correct table structure. The bot is trying to query the `x_accounts` table which doesn't exist.

## Solution

### Step 1: Stop the Bot
In the terminal running `bot.py`, press **Ctrl+C**

### Step 2: Delete the Old Database
```powershell
cd C:\Antigravity\Discord_X_verif
Remove-Item bot_database.db
```

### Step 3: Restart the Bot
```powershell
python bot.py
```

The bot will automatically create a fresh database with the correct schema on startup.

### Step 4: Verify
You should see in the terminal:
```
Logged in as X_verification#9649
Database initialized.
Worker started. Waiting for images...
```

### Step 5: Test
Type `!xlink` in Discord - you should see `DEBUG:` messages in the terminal and the bot should respond!
