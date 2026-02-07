import aiosqlite
import os
import time

DB_FILE = "bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS x_accounts (
                discord_id TEXT PRIMARY KEY,
                x_user_id TEXT,
                x_username TEXT,
                x_name TEXT,
                verified BOOLEAN,
                verified_type TEXT,
                linked_at INTEGER
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS verification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT,
                discord_username TEXT,
                guild_id TEXT,
                project TEXT,
                score TEXT,
                role_assigned TEXT,
                timestamp INTEGER
            )
        """)
        await db.commit()

async def get_link(discord_id: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM x_accounts WHERE discord_id = ?", (discord_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def save_link(discord_id: str, data: dict):
    # data expects keys: x_user_id, x_username, x_name, verified, verified_type, linked_at
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT OR REPLACE INTO x_accounts 
            (discord_id, x_user_id, x_username, x_name, verified, verified_type, linked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            discord_id,
            data.get("x_user_id"),
            data.get("x_username"),
            data.get("x_name"),
            data.get("verified"),
            data.get("verified_type"),
            data.get("linked_at", int(time.time()))
        ))
        await db.commit()

async def delete_link(discord_id: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM x_accounts WHERE discord_id = ?", (discord_id,))
        await db.commit()
        return True # logic in bot was "if removed"

async def log_result(discord_id: str, discord_username: str, guild_id: str, project: str, score: str, role_assigned: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO verification_history 
            (discord_id, discord_username, guild_id, project, score, role_assigned, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            discord_id,
            discord_username,
            guild_id,
            project,
            score,
            role_assigned,
            int(time.time())
        ))
        await db.commit()
