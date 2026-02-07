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
    def __init__(self, job, detected_score, project="Unknown", handle_match_error=None):
        self.job = job
        self.detected_score = detected_score
        self.project = project
        self.handle_match_error = handle_match_error
        self.role_name = None

        if detected_score and not handle_match_error:
            try:
                # Remove commas and convert to float for comparison
                val = float(detected_score.replace(',', '').strip())
                self.role_name = None

                if project == "Kaito":
                    # Kaito: >50..200 (Lite), >200..1000 (Amp), >1000 (Top)
                    if 50 < val < 200:
                        self.role_name = "Signal Lite"
                    elif 200 <= val < 1000:
                        self.role_name = "Signal Amplifier"
                    elif val >= 1000:
                        self.role_name = "Top Signal"
                    else:
                        # Fallback if below 50 or other edge case, maintain old behavior or None?
                        # User didn't specify, but let's default to generic or None.
                        # For now, let's leave it as None if it doesn't meet the "Lite" bar
                        pass
                
                elif project == "Wallchain":
                    # Wallchain: >10..75 (Lite), 76..400 (Amp), 401..1000+ (Top)
                    if 10 < val <= 75:
                        self.role_name = "Signal Lite"
                    elif 76 <= val <= 400:
                        self.role_name = "Signal Amplifier"
                    elif val >= 401:
                        self.role_name = "Top Signal"
                
                elif project == "Cookie":
                    # Cookie: 10-200 (Lite), 201-400 (Amp), 401+ (Top)
                    if 10 <= val <= 200:
                        self.role_name = "Signal Lite"
                    elif 201 <= val <= 400:
                        self.role_name = "Signal Amplifier"
                    elif val >= 401:
                        self.role_name = "Top Signal"

                elif project == "Xeet":
                    # Xeet: 100-300 (Lite), 301-1099 (Amp), 1100+ (Top)
                    if 100 <= val <= 300:
                        self.role_name = "Signal Lite"
                    elif 301 <= val < 1100:
                        self.role_name = "Signal Amplifier"
                    elif val >= 1100:
                        self.role_name = "Top Signal"

                else:
                    # Legacy behavior for Mindoshare or unknown
                    self.role_name = f"Score {detected_score}"

            except ValueError:
                # If parsing fails, fall back to string-based legacy role
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
    if "xeet" in text_blob or "xeets earned" in text_blob:
        return "Xeet"
    if "cookie" in text_blob or "snaps earned" in text_blob or "total snaps" in text_blob:
        return "Cookie"
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

def extract_xeet_score(results):
    # Goal: Find "Xeets earned" label (bottom-left), then find large number ABOVE it
    label_bbox = None
    
    for (bbox, text, prob) in results:
        t = text.lower().strip()
        if "xeets earned" in t or ("xeet" in t and "earned" in t):
            label_bbox = bbox
            break
    
    if not label_bbox:
        # Fallback: look for just "earned" near bottom
        for (bbox, text, prob) in results:
            if "earned" in text.lower().strip():
                label_bbox = bbox
                break
    
    if not label_bbox:
        return None
    
    label_center_x = (label_bbox[0][0] + label_bbox[1][0]) / 2
    label_top_y = label_bbox[0][1]  # Top edge of "Xeets earned"
    
    candidates = []
    for (bbox, text, prob) in results:
        clean_text = text.strip().replace(',', '')
        if re.match(r'^\d+(\.\d+)?$', clean_text):
            cand_center_x = (bbox[0][0] + bbox[1][0]) / 2
            cand_bottom_y = bbox[2][1]  # Bottom edge of number
            cand_height = bbox[2][1] - bbox[0][1]
            
            # Number must be ABOVE the label (bottom-left positioning)
            # And roughly aligned horizontally
            if abs(cand_center_x - label_center_x) < 200 and cand_bottom_y <= label_top_y:
                dist = label_top_y - cand_bottom_y
                candidates.append((cand_height, dist, clean_text))
    
    # Prioritize biggest font
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates else None

def extract_cookie_score(results):
    # Goal: Find "Total snaps earned" or "snaps earned", then find number nearby (usually above/left)
    label_bbox = None
    
    for (bbox, text, prob) in results:
        t = text.lower().strip()
        if "total snaps earned" in t or "snaps earned" in t:
            label_bbox = bbox
            break
    
    if not label_bbox:
        # Fallback: look for "snaps" or "earned"
        for (bbox, text, prob) in results:
            t = text.lower().strip()
            if "snaps" in t or "earned" in t:
                label_bbox = bbox
                break
    
    if not label_bbox:
        return None
    
    label_center_x = (label_bbox[0][0] + label_bbox[1][0]) / 2
    label_center_y = (label_bbox[0][1] + label_bbox[2][1]) / 2
    
    candidates = []
    for (bbox, text, prob) in results:
        clean_text = text.strip().replace(',', '')
        if re.match(r'^\d+(\.\d+)?$', clean_text):
            cand_center_x = (bbox[0][0] + bbox[1][0]) / 2
            cand_center_y = (bbox[0][1] + bbox[2][1]) / 2
            cand_height = bbox[2][1] - bbox[0][1]
            
            # Calculate distance from label center
            dist = ((cand_center_x - label_center_x)**2 + (cand_center_y - label_center_y)**2)**0.5
            
            # Number should be relatively close (within 300px)
            if dist < 300:
                candidates.append((cand_height, dist, clean_text))
    
    # Prioritize biggest font, then closest
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2] if candidates else None

def extract_handle(results):
    for (bbox, text, prob) in results:
        # Looking for @username (at least 3 chars)
        t = text.strip()
        if t.startswith('@') and len(t) > 3:
            return t.lstrip('@')
    return None

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
            elif project == "Xeet":
                score_val = extract_xeet_score(results)
            elif project == "Cookie":
                score_val = extract_cookie_score(results)
            elif project == "Mindoshare":
                score_val = extract_mindoshare_score(results)
            else:
                # Fallback sequence
                score_val = extract_mindoshare_score(results)
                if not score_val:
                     score_val = extract_wallchain_score(results)
                if not score_val:
                     score_val = extract_kaito_score(results)

            # Compare Handle for Identity Verification
            x_link = await link_get(job.user_id)
            handle_error = None
            
            if x_link:
                img_handle = extract_handle(results)
                required_handle = x_link.get("x_username", "").lower()
                
                if img_handle:
                    img_handle_clean = img_handle.lower()
                    # Fuzzy/Exact check: case-insensitive
                    if img_handle_clean != required_handle:
                         handle_error = f"Found @{img_handle} in image, but your linked account is @{required_handle}"

            result = VerificationResult(job, score_val, project, handle_match_error=handle_error)
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
    if not role and result.role_name:
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
    await database.log_result(
        discord_id=result.job.user_id,
        discord_username=str(member),
        guild_id=result.job.guild_id,
        project=result.project,
        score=str(result.detected_score) if result.detected_score else None,
        role_assigned=result.role_name
    )

    # --- New Aesthetic Embed ---
    if result.handle_match_error:
        # Identity Mismatch: Discord Red
        embed = discord.Embed(
            description=f"❌ **Identity Mismatch**\n{result.handle_match_error}\nThis screenshot does not belong to your linked account.",
            color=0xED4245
        )
    elif result.detected_score:
        # Success: Discord Green
        embed = discord.Embed(
            description=f"✅ **Verification Successful**\nFound **{result.project}** score!",
            color=0x57F287 
        )
    else:
        # Failure: Discord Red
        embed = discord.Embed(
            description=f"❌ **Verification Failed**\nCould not detect a **{result.project}** score.\nPlease ensure the image is clear and uncropped.",
            color=0xED4245
        )

    # Author
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    # Field Data
    if result.detected_score:
        embed.add_field(name="🎯 Score", value=f"`{result.detected_score}`", inline=True)
    
    if result.role_name:
        embed.add_field(name="🎭 Role", value=f"`{result.role_name}`", inline=True)

    # Linked X
    if x_link:
        x_handle = f"[@{x_link.get('x_username')}](https://x.com/{x_link.get('x_username')})"
        # Checkmark logic
        is_verified = x_link.get("verified") or x_link.get("verified_type") in ["blue", "business", "government"]
        if is_verified:
            x_handle += " ☑️"
        
        embed.add_field(name="🔗 X Account", value=x_handle, inline=False)
    else:
        embed.add_field(name="🔗 X Account", value="*Not Linked*", inline=False)

    # Footer
    embed.set_footer(text="Mindo AI Verifier", icon_url=client.user.display_avatar.url if client.user else None)

    # Send
    await channel.send(content=f"<@{result.job.user_id}>", embed=embed)

# -----------------------------
# Discord events
# -----------------------------
@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    await database.init_db()
    print("Database initialized.")

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
            
            embed = discord.Embed(
                title="🔗 Link Your X Account",
                description="Click the button below to securely connect your X (Twitter) account.\n\n"
                            "After linking, you'll see a success page. Then return to Discord and post your image!",
                color=0x1DA1F2  # Twitter blue
            )
            embed.set_footer(text="⏱️ Link expires in 10 minutes")
            
            view = discord.ui.View()
            button = discord.ui.Button(
                label="Connect X Account",
                style=discord.ButtonStyle.link,
                url=link,
                emoji="🔵"
            )
            view.add_item(button)
            
            await message.reply(embed=embed, view=view)
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
            
            embed = discord.Embed(
                title="❌ X Account Required",
                description="You must link your X account before using this bot.\n\n"
                            "Click the button below to connect your account, then post your image again!",
                color=0xFF0000  # Red
            )
            embed.set_footer(text="⏱️ Link expires in 10 minutes")
            
            view = discord.ui.View()
            button = discord.ui.Button(
                label="Connect X Account",
                style=discord.ButtonStyle.link,
                url=link,
                emoji="🔵"
            )
            view.add_item(button)
            
            await message.reply(embed=embed, view=view)
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
