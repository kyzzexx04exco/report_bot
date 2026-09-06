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

# Client & DB global
_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


# ─────────────────────────────────────────────────────────────
# CONNECTION MANAGEMENT
# ─────────────────────────────────────────────────────────────

def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    """Ambil database instance."""
    global _client, _db
    if _db is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        _db = _client[DB_NAME]
        logger.info(f"[DB] Terhubung ke MongoDB: {MONGO_URI}")
    return _db


async def close_pool() -> None:
    """Tutup koneksi MongoDB saat shutdown."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("[DB] Koneksi MongoDB ditutup.")


# ─────────────────────────────────────────────────────────────
# INIT DATABASE
# ─────────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Inisialisasi collection & index.
    MongoDB membuat collection otomatis saat data pertama diinsert,
    tapi index perlu dibuat eksplisit.
    """
    db = get_db()

    # Index unique untuk phone_number di collection senders
    await db.senders.create_index("phone_number", unique=True)

    # Index untuk query round-robin (is_active + last_used)
    await db.senders.create_index([("is_active", -1), ("last_used", 1)])

    # Index unique untuk user_id di collection config
    await db.config.create_index("user_id", unique=True)

    logger.info("[DB] MongoDB diinisialisasi, index siap.")


# ─────────────────────────────────────────────────────────────
# SENDERS — CRUD
# ─────────────────────────────────────────────────────────────

async def add_sender(phone_number: str, session_string: str) -> bool:
    """Tambah sender baru. Return True jika berhasil."""
    try:
        db = get_db()
        await db.senders.insert_one({
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
            logger.warning(f"[DB] Sender {phone_number} sudah ada.")
            return False
        raise


async def get_all_senders() -> list[dict]:
    """Ambil semua sender."""
    db = get_db()
    cursor = db.senders.find().sort([("is_active", -1), ("last_used", 1)])
    rows = await cursor.to_list(length=None)
    # Konversi ObjectId ke string biar aman
    for row in rows:
        row["id"] = str(row.pop("_id"))
    return rows


async def get_active_sender() -> Optional[dict]:
    """Ambil sender aktif paling lama tidak dipakai (Round-Robin)."""
    db = get_db()
    row = await db.senders.find_one(
        {"is_active": True},
        sort=[("last_used", 1)],
    )
    if row:
        row["id"] = str(row.pop("_id"))
        return row
    return None


async def get_sender_by_phone(phone_number: str) -> Optional[dict]:
    """Ambil sender berdasarkan nomor HP."""
    db = get_db()
    row = await db.senders.find_one({"phone_number": phone_number})
    if row:
        row["id"] = str(row.pop("_id"))
        return row
    return None


async def update_sender_last_used(phone_number: str) -> None:
    """Update last_used sender."""
    db = get_db()
    await db.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"last_used": datetime.utcnow()}},
    )
    logger.debug(f"[DB] last_used {phone_number} diperbarui.")


async def set_sender_inactive(phone_number: str) -> None:
    """Nonaktifkan sender."""
    db = get_db()
    await db.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"is_active": False}},
    )
    logger.warning(f"[DB] Sender {phone_number} dinonaktifkan.")


async def set_sender_active(phone_number: str) -> None:
    """Aktifkan kembali sender."""
    db = get_db()
    await db.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"is_active": True}},
    )
    logger.info(f"[DB] Sender {phone_number} diaktifkan.")


async def update_sender_session(phone_number: str, session_string: str) -> None:
    """Update session_string sender."""
    db = get_db()
    await db.senders.update_one(
        {"phone_number": phone_number},
        {"$set": {"session_string": session_string}},
    )
    logger.info(f"[DB] Session {phone_number} diperbarui.")


async def delete_sender(phone_number: str) -> bool:
    """Hapus sender. Return True jika berhasil."""
    db = get_db()
    result = await db.senders.delete_one({"phone_number": phone_number})
    deleted = result.deleted_count > 0
    if deleted:
        logger.info(f"[DB] Sender {phone_number} dihapus.")
    return deleted


async def count_active_senders() -> int:
    """Hitung jumlah sender aktif."""
    db = get_db()
    return await db.senders.count_documents({"is_active": True})


# ─────────────────────────────────────────────────────────────
# CONFIG — COOLDOWN
# ─────────────────────────────────────────────────────────────

async def get_cooldown(user_id: int) -> int:
    """Ambil cooldown_seconds. Default 60."""
    db = get_db()
    row = await db.config.find_one({"user_id": user_id})
    return row["cooldown_seconds"] if row else 60


async def set_cooldown(user_id: int, seconds: int) -> None:
    """Set atau update cooldown_seconds."""
    db = get_db()
    await db.config.update_one(
        {"user_id": user_id},
        {"$set": {"cooldown_seconds": seconds}},
        upsert=True,  # Insert jika belum ada, update jika sudah ada
    )
    logger.info(f"[DB] Cooldown user {user_id} diset ke {seconds}s.")
