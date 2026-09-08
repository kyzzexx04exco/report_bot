"""
database/db.py
Koneksi MongoDB asinkronus (motor) + semua operasi CRUD
untuk collection senders dan config.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME", "report_bot")

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


# ─────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────

def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        _db = _client[DB_NAME]
        logger.info(f"[DB] Terhubung ke MongoDB: {MONGO_URI}")
    return _db


async def close_pool() -> None:
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("[DB] Koneksi MongoDB ditutup.")


async def init_db() -> None:
    database = get_db()
    await database.senders.create_index("phone_number", unique=True)
    await database.senders.create_index([("is_active", -1), ("last_used", 1)])
    await database.config.create_index("user_id", unique=True)
    logger.info("[DB] Index siap.")


# ─────────────────────────────────────────────────────────────
# SENDERS — CRUD
# ─────────────────────────────────────────────────────────────

async def add_sender(phone_number: str, session_string: str) -> bool:
    try:
        database = get_db()
        await database.senders.insert_one({
            "phone_number":   phone_number,
            "session_string": session_string,
            "is_active":      True,
            "last_used":      None,
            "created_at":     datetime.utcnow(),
        })
        logger.info(f"[DB] Sender {phone_number} ditambahkan.")
        return True
    except Exception as e:
        if "duplicate key" in str(e).lower() or "E11000" in str(e):
            return False
        raise


async def get_all_senders() -> list[dict]:
    database = get_db()
    cursor = database.senders.find().sort([("is_active", -1), ("last_used", 1)])
    rows = await cursor.to_list(length=None)
    for row in rows:
        row["id"] = str(row.pop("_id"))
    return rows


async def get_active_sender() -> Optional[dict]:
    """Ambil sender aktif paling lama tidak dipakai."""
    database = get_db()
    # Sort: last_used=None dulu (belum pernah dipakai), lalu yang paling lama
    row = await database.senders.find_one(
        {"is_active": True},
        sort=[("last_used", 1)],
    )
    if row:
        row["id"] = str(row.pop("_id"))
    return row


async def get_next_sender(exclude: set[str] = None) -> Optional[dict]:
    """
    Ambil sender aktif berikutnya untuk round-robin.
    exclude: set phone_number yang sudah dicoba di ronde ini.
    Diurutkan by last_used ASC — yang paling lama idle duluan.
    """
    database = get_db()
    query = {"is_active": True}
    if exclude:
        query["phone_number"] = {"$nin": list(exclude)}

    row = await database.senders.find_one(
        query,
        sort=[("last_used", 1)],
    )
    if row:
        row["id"] = str(row.pop("_id"))
    return row


async def get_sender_by_phone(phone_number: str) -> Optional[dict]:
    database = get_db()
    row = await database.senders.find_one({"phone_number": phone_number})
    if row:
        row["id"] = str(row.pop("_id"))
    return row


async def update_sender_last_used(phone_number: str) -> None:
    database = get_db()
    await database.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"last_used": datetime.utcnow()}},
    )


async def set_sender_inactive(phone_number: str) -> None:
    database = get_db()
    await database.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"is_active": False}},
    )
    logger.warning(f"[DB] Sender {phone_number} dinonaktifkan.")


async def set_sender_active(phone_number: str) -> None:
    database = get_db()
    await database.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"is_active": True}},
    )
    logger.info(f"[DB] Sender {phone_number} diaktifkan.")


async def update_sender_session(phone_number: str, session_string: str) -> None:
    database = get_db()
    await database.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"session_string": session_string}},
    )


async def delete_sender(phone_number: str) -> bool:
    database = get_db()
    result = await database.senders.delete_one({"phone_number": phone_number})
    deleted = result.deleted_count > 0
    if deleted:
        logger.info(f"[DB] Sender {phone_number} dihapus.")
    return deleted


async def count_active_senders() -> int:
    database = get_db()
    return await database.senders.count_documents({"is_active": True})


# ─────────────────────────────────────────────────────────────
# CONFIG — COOLDOWN
# ─────────────────────────────────────────────────────────────

async def get_cooldown(user_id: int) -> int:
    database = get_db()
    row = await database.config.find_one({"user_id": user_id})
    return row["cooldown_seconds"] if row and "cooldown_seconds" in row else 60


async def set_cooldown(user_id: int, seconds: int) -> None:
    database = get_db()
    await database.config.update_one(
        {"user_id": user_id},
        {"$set": {"cooldown_seconds": seconds}},
        upsert=True,
    )
    logger.info(f"[DB] Cooldown user {user_id} = {seconds}s.")


# ─────────────────────────────────────────────────────────────
# CONFIG — REPORT COUNT (/settreport)
# ─────────────────────────────────────────────────────────────

async def get_report_count(user_id: int) -> int:
    database = get_db()
    row = await database.config.find_one({"user_id": user_id})
    return row.get("report_count", 1) if row else 1


async def set_report_count(user_id: int, count: int) -> None:
    database = get_db()
    await database.config.update_one(
        {"user_id": user_id},
        {"$set": {"report_count": count}},
        upsert=True,
    )
    logger.info(f"[DB] Report count user {user_id} = {count}.")
