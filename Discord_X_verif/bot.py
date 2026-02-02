import discord
import asyncio
import re
import config
import easyocr
import io
import os
import json
import time
import tempfile
import secrets
import urllib.parse
import hashlib
import base64
import aiohttp
import hmac
import database

# -----------------------------
# X OAuth2 + JSON Store Settings
# -----------------------------
X_CLIENT_ID = getattr(config, "X_CLIENT_ID", os.getenv("X_CLIENT_ID", "")).strip()
X_CLIENT_SECRET = getattr(config, "X_CLIENT_SECRET", os.getenv("X_CLIENT_SECRET", "")).strip()
X_REDIRECT_URI = getattr(config, "X_REDIRECT_URI", os.getenv("X_REDIRECT_URI", "")).strip()

# Minimal scopes for /2/users/me is users.read; adding tweet.read is commonly used
X_SCOPES = getattr(config, "X_SCOPES", os.getenv("X_SCOPES", "users.read tweet.read")).strip()

OAUTH_HOST = getattr(config, "OAUTH_HOST", os.getenv("OAUTH_HOST", "0.0.0.0"))
OAUTH_PORT = int(getattr(config, "OAUTH_PORT", os.getenv("OAUTH_PORT", "8000")))

LINK_SECRET = getattr(config, "LINK_SECRET", os.getenv("LINK_SECRET", "default-secret-change-me")).strip()
LINK_TTL = 10 * 60  # 10 minutes

LINKS_FILE = "x_links.json"          # discord_id -> linked X user
PENDING_FILE = "oauth_pending.json"  # state -> (discord_id, code_verifier, created_at)
STORE_LOCK = asyncio.Lock()
PENDING_TTL_SECONDS = 10 * 60  # 10 minutes

# -----------------------------
# OCR Setup
# -----------------------------
PROJECTS = ["A", "B", "C", "D"]
reader = easyocr.Reader(['en'])

class VerificationJob:
    def __init__(self, message, image_data):
        self.message = message
        self.image_data = image_data
        self.user_id = str(message.author.id)
        self.guild_id = str(message.guild.id)
        self.author = message.author  # for logging

class VerificationResult:
    def __init__(self, job, detected_score, project="Unknown"):
        self.job = job
        self.detected_score = detected_score
        self.project = project
        if detected_score:
            if project == "Wallchain":
                self.role_name = f"Quack Score {detected_score}"
            elif project == "Kaito":
                self.role_name = f"Kaito Score {detected_score}"
            else:
                self.role_name = f"Score {detected_score}"
        else:
            self.role_name = None

# -----------------------------
# JSON store helpers (atomic write)
# -----------------------------
def _load_json_sync(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # If corrupted, start fresh
        return {}

def _atomic_write_json_sync(path: str, data: dict):
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="._tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

async def _cleanup_pending_locked(pending: dict) -> dict:
    now = int(time.time())
    cleaned = {}
    for state, obj in pending.items():
        created_at = int(obj.get("created_at", 0))
        if now - created_at <= PENDING_TTL_SECONDS:
            cleaned[state] = obj
    return cleaned

async def pending_put(state: str, discord_id: str, code_verifier: str):
    async with STORE_LOCK:
        pending = _load_json_sync(PENDING_FILE)
        pending = await _cleanup_pending_locked(pending)
        pending[state] = {
            "discord_id": discord_id,
            "code_verifier": code_verifier,
            "created_at": int(time.time())
        }
        _atomic_write_json_sync(PENDING_FILE, pending)

async def pending_pop(state: str):
    async with STORE_LOCK:
        pending = _load_json_sync(PENDING_FILE)
        pending = await _cleanup_pending_locked(pending)

        obj = pending.pop(state, None)
        _atomic_write_json_sync(PENDING_FILE, pending)
        return obj  # None or dict

async def link_get(discord_id: str):
    return await database.get_link(discord_id)

async def link_delete(discord_id: str):
    return await database.delete_link(discord_id)

# -----------------------------
# OAuth helpers (PKCE)
# -----------------------------
def _base64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("utf-8")

def pkce_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return _base64url_no_pad(digest)

def _check_sig(discord_id: str, ts: int, sig: str):
    if abs(int(time.time()) - ts) > LINK_TTL:
        raise HTTPException(400, "link expired, run !xlink again")

    msg = f"{discord_id}:{ts}".encode("utf-8")
    expected = hmac.new(LINK_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(400, "bad signature")

async def create_signed_start_link(discord_id: str) -> str:
    # This generates a link to OUR server's /x/start endpoint
    ts = int(time.time())
    msg = f"{discord_id}:{ts}".encode("utf-8")
    sig = hmac.new(LINK_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    
    # We need a base URL for our server. We'll use the one from redirect URI if possible or OAUTH_HOST
    # Assuming the server is reachable at the same host as X_REDIRECT_URI but without /x/callback
    base_url = X_REDIRECT_URI.replace("/x/callback", "")
    params = {
        "discord_id": discord_id,
        "ts": ts,
        "sig": sig
    }
    return f"{base_url}/x/start?" + urllib.parse.urlencode(params)

async def x_token_exchange(code: str, code_verifier: str) -> dict:
    url = "https://api.x.com/2/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Confidential client: use Basic auth if secret is present
    if X_CLIENT_SECRET:
        basic = base64.b64encode(f"{X_CLIENT_ID}:{X_CLIENT_SECRET}".encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {basic}"

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": X_REDIRECT_URI,
        "code_verifier": code_verifier,
        "client_id": X_CLIENT_ID,
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url, headers=headers, data=data) as resp:
            txt = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Token exchange failed ({resp.status}): {txt[:300]}")
            return json.loads(txt)

async def x_get_me(access_token: str) -> dict:
    url = "https://api.x.com/2/users/me"
    params = {"user.fields": "id,username,name,verified,verified_type,created_at,public_metrics"}
    headers = {"Authorization": f"Bearer {access_token}"}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            txt = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"/2/users/me failed ({resp.status}): {txt[:300]}")
            return json.loads(txt)

# -----------------------------
# Discord Bot Setup
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)
queue = asyncio.Queue()


# -----------------------------
# OCR Worker
# -----------------------------
# -----------------------------
# OCR Helpers
# -----------------------------
def classify_project(results):
    text_blob = " ".join([t[1].lower() for t in results])
    if "wallchain" in text_blob or "quacks" in text_blob or "quack balance" in text_blob:
        return "Wallchain"
    if "kaito" in text_blob or "total yaps" in text_blob or "earned yaps" in text_blob:
        return "Kaito"
    if "kol score" in text_blob or "mindoshare" in text_blob:
        return "Mindoshare"
    return "Unknown"

def extract_mindoshare_score(results):
    kw_bbox = None
    for (bbox, text, prob) in results:
        if "kol score" in text.lower():
            kw_bbox = bbox
            break
            
    if not kw_bbox:
        return None

    kw_center_x = (kw_bbox[0][0] + kw_bbox[1][0]) / 2
    kw_top_y = kw_bbox[0][1]

    candidates = []
    for (bbox, text, prob) in results:
        # Looking for numbers like 92.49 or 92
        if re.match(r'^\d+(\.\d+)?$', text.strip()):
            cand_center_x = (bbox[0][0] + bbox[1][0]) / 2
            cand_bottom_y = bbox[2][1]
            cand_height = bbox[2][1] - bbox[0][1]
            
            # Must be roughly centered horizontally and ABOVE the label
            if abs(cand_center_x - kw_center_x) < 100 and cand_bottom_y <= kw_top_y:
                dist = kw_top_y - cand_bottom_y
                # We store height as the primary sort key (descending)
                candidates.append((cand_height, dist, text))

    # Sort by height (descending) then by distance (ascending)
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates else None

def extract_wallchain_score(results):
    # Goal: Find "Score" label, then find number BELOW it
    score_bbox = None
    for (bbox, text, prob) in results:
        if text.strip() == "Score":
            score_bbox = bbox
            break
    
    if not score_bbox:
        return None
        
    score_center_x = (score_bbox[0][0] + score_bbox[1][0]) / 2
    score_bottom_y = score_bbox[2][1] # y-coord of bottom edge

    candidates = []
    for (bbox, text, prob) in results:
        # Match integer 85 or float 85.0 (avoiding 2.91%)
        clean_text = text.strip()
        if re.match(r'^\d+(\.\d+)?$', clean_text):
            cand_center_x = (bbox[0][0] + bbox[1][0]) / 2
            cand_top_y = bbox[0][1]
            cand_height = bbox[2][1] - bbox[0][1]
            
            # Must be roughly centered horizontally and BELOW the label
            if abs(cand_center_x - score_center_x) < 100 and cand_top_y >= score_bottom_y:
                dist = cand_top_y - score_bottom_y
                # Store height as priority check
                candidates.append((cand_height, dist, clean_text))

    # Sort by biggest height first (the main score), then by proximity
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates else None

def extract_kaito_score(results):
    # Search for "Total" and "Yaps" even if they are in separate boxes
    total_bbox = None
    yaps_bbox = None
    
    for (bbox, text, prob) in results:
        t = text.lower().strip()
        if "total" in t and "yaps" in t:
            total_bbox = bbox
            yaps_bbox = bbox
            break
        if t == "total":
            total_bbox = bbox
        if t == "yaps":
            yaps_bbox = bbox
            
    # Determine the anchor box
    label_bbox = None
    if total_bbox and yaps_bbox:
        # If they are close horizontally/vertically, use the yaps one as anchor
        dist_x = abs((total_bbox[0][0] + total_bbox[1][0])/2 - (yaps_bbox[0][0] + yaps_bbox[1][0])/2)
        dist_y = abs(total_bbox[2][1] - yaps_bbox[0][1])
        if dist_x < 150 and dist_y < 50:
             label_bbox = yaps_bbox
        else:
             # Just use total/yaps if they were combined, or default to yaps
             label_bbox = yaps_bbox
    elif yaps_bbox:
        label_bbox = yaps_bbox
    elif total_bbox:
        label_bbox = total_bbox

    if not label_bbox:
        return None

    label_center_x = (label_bbox[0][0] + label_bbox[1][0]) / 2
    label_bottom_y = label_bbox[2][1]

    candidates = []
    for (bbox, text, prob) in results:
        clean_text = text.strip().replace(',', '') # Handle 1,266.88
        if re.match(r'^\d+(\.\d+)?$', clean_text):
            cand_center_x = (bbox[0][0] + bbox[1][0]) / 2
            cand_top_y = bbox[0][1]
            cand_height = bbox[2][1] - bbox[0][1]
            
            # Kaito numbers are usually big. Increase x-tolerance to 300
            if abs(cand_center_x - label_center_x) < 300 and cand_top_y >= label_bottom_y:
                dist = cand_top_y - label_bottom_y
                candidates.append((cand_height, dist, clean_text))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates else None

async def worker():
    print("Worker started. Waiting for images...")
    while True:
        job = await queue.get()
        try:
            print(f"Processing image for {job.author}...")
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, reader.readtext, job.image_data)

            project = classify_project(results)
            print(f"Detected Project: {project}")

            score_val = None
            if project == "Wallchain":
                score_val = extract_wallchain_score(results)
            elif project == "Kaito":
                score_val = extract_kaito_score(results)
            elif project == "Mindoshare":
                score_val = extract_mindoshare_score(results)
            else:
                # Fallback sequence
                score_val = extract_mindoshare_score(results)
                if not score_val:
                     score_val = extract_wallchain_score(results)
                if not score_val:
                     score_val = extract_kaito_score(results)

            result = VerificationResult(job, score_val, project)
            await handle_result(result)

        except Exception as e:
            print(f"Error processing job for {job.user_id}: {e}")
        finally:
            queue.task_done()

async def handle_result(result: VerificationResult):
    guild = result.job.message.guild
    member = result.job.message.author
    channel = result.job.message.channel

    # Load linked X info for JSON output
    x_link = await link_get(result.job.user_id)

    # Assign Role
    role = discord.utils.get(guild.roles, name=result.role_name)
    if not role:
        try:
            role = await guild.create_role(name=result.role_name)
        except discord.Forbidden:
            print(f"Missing permissions to create role {result.role_name}")
            role = None

    if role:
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            await channel.send(
                f"⚠️ I tried to give you the `{result.role_name}` role, but I don't have permission. "
                "Please check my role hierarchy."
            )

    # Log to History DB
    # We need discord username
    await database.log_result(
        discord_id=result.job.user_id,
        discord_username=str(member),
        guild_id=result.job.guild_id,
        project=result.project,
        score=str(result.detected_score) if result.detected_score else None,
        role_assigned=result.role_name
    )

    # Embed + JSON payload
    if result.detected_score:
        desc = f"Detected **{result.project}** score: `{result.detected_score}`"
        color = 0x00ff00
    else:
        desc = f"Could not detect a score for **{result.project}**. Please ensure the full card is visible."
        color = 0xff0000

    embed = discord.Embed(title="Image Scan Complete", description=desc, color=color)
    if x_link:
        embed.add_field(name="Linked X", value=f"@{x_link.get('x_username')}", inline=True)
        embed.add_field(name="Verified", value=str(x_link.get("verified")), inline=True)
        embed.add_field(name="Verified type", value=str(x_link.get("verified_type")), inline=True)
    if result.detected_score:
        embed.add_field(name="Assigned Role", value=result.role_name, inline=True)

    payload = {
        "discord": {"user_id": result.job.user_id, "guild_id": result.job.guild_id},
        "x": x_link,
        "ocr": {
            "project": result.project,
            "score": result.detected_score
        },
        "role_assigned": result.role_name
    }
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)

    if len(json_text) < 1800:
        await channel.send(
            content=f"<@{result.job.user_id}> processing complete!\n```json\n{json_text}\n```",
            embed=embed
        )
    else:
        bio = io.BytesIO(json_text.encode("utf-8"))
        file = discord.File(fp=bio, filename="verification.json")
        await channel.send(content=f"<@{result.job.user_id}> processing complete! (JSON attached)", embed=embed, file=file)

# -----------------------------
# Discord events
# -----------------------------
@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")

    # Start OCR worker
    client.loop.create_task(worker())

@client.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    content = (message.content or "").strip()

    # ---- Commands ----
    if content.lower() == "!xlink":
        try:
            link = await create_signed_start_link(str(message.author.id))
            await message.reply(
                "Step 1: Click this link to connect your X account:\n"
                f"{link}\n\n"
                "Step 2: After you see ✅ Linked in your browser, post your image again."
            )
        except Exception as e:
            await message.reply(f"❌ Could not create link: {e}")
        return

    if content.lower() == "!xstatus":
        x_link = await link_get(str(message.author.id))
        if not x_link:
            await message.reply("You have not linked X yet. Use `!xlink`.")
        else:
            await message.reply(
                f"✅ Linked X: @{x_link.get('x_username')}\n"
                f"Verified: {x_link.get('verified')} | Type: {x_link.get('verified_type')}"
            )
        return

    if content.lower() == "!xunlink":
        removed = await link_delete(str(message.author.id))
        await message.reply("✅ Unlinked." if removed else "You were not linked.")
        return

    # ---- Gate OCR: must be linked ----
    x_link = await link_get(str(message.author.id))
    if not x_link:
        # only gate if they tried to submit an image
        image_attachments = [
            att for att in message.attachments
            if att.content_type and att.content_type.startswith("image/")
        ]
        if image_attachments:
            link = await create_signed_start_link(str(message.author.id))
            await message.reply(
                "❌ You must link your X account before using this bot.\n"
                f"Click to link:\n{link}\n\n"
                "After you see ✅ Linked, post your image again."
            )
        return

    # ---- Check Attachments ----
    image_attachments = [
        att for att in message.attachments
        if att.content_type and att.content_type.startswith("image/")
    ]
    if not image_attachments:
        return

    # Enqueue job
    await message.reply("Scanning your image for a number...")
    image_bytes = await image_attachments[0].read()
    job = VerificationJob(message, image_bytes)
    await queue.put(job)

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN is not set in .env or environment variables.")
    elif not X_CLIENT_ID or not X_REDIRECT_URI:
        print("Error: X_CLIENT_ID / X_REDIRECT_URI missing. Add them to config/env.")
    else:
        client.run(config.DISCORD_TOKEN)
