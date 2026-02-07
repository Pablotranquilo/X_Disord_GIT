# Fix: Enable Message Content Intent

## Problem
The bot logs in successfully but doesn't respond to messages or images.

## Cause
The **Message Content Intent** is not enabled in Discord Developer Portal.

## Solution

1. **Go to Discord Developer Portal**
   - Visit: https://discord.com/developers/applications
   - Select your application (bot)

2. **Enable Message Content Intent**
   - Click on the **Bot** section in the left sidebar
   - Scroll down to **Privileged Gateway Intents**
   - Enable the following intents:
     - ✅ **MESSAGE CONTENT INTENT** (required)
     - ✅ **SERVER MEMBERS INTENT** (recommended)
     - ✅ **PRESENCE INTENT** (optional)

3. **Save Changes**
   - Click **Save Changes** at the bottom

4. **Restart Your Bot**
   - Stop the running bot process (Ctrl+C)
   - Run: `python bot.py`

5. **Test**
   - Type `!xlink` in your Discord server
   - You should see DEBUG output in the terminal like:
     ```
     DEBUG: Received message from Username#1234: !xlink
     ```

## Verification
If the intent is enabled correctly, you should see DEBUG messages in your terminal every time someone sends a message in a channel where the bot has access.
