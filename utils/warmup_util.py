"""
utils/warmup.py
Warmup sender — interaksi ringan biar trust score naik di mata Telegram.

Yang dilakukan per sender:
1. Join beberapa channel populer (baca aja, gak spam)
2. Fetch history channel (simulasi "baca pesan")
3. Jeda random antar aksi biar natural

Ini bukan spamming — ini simulasi user biasa yang lagi scroll Telegram.
"""

import asyncio
import logging
import random

from pyrogram import Client
from pyrogram.errors import (
    ChannelPrivate,
    FloodWait,
    UserAlreadyParticipant,
)

from utils import pyro_client
from database import db

logger = logging.getLogger(__name__)

# Channel populer publik — aman buat join/baca
WARMUP_CHANNELS = [
    "telegram",
    "durov",
    "TelegramTips",
    "telegramwallpapers",
]


async def _warmup_one(client: Client, phone: str) -> dict:
    """Warmup 1 sender — join + baca history."""
    actions = 0

    for ch in WARMUP_CHANNELS:
        try:
            await client.join_chat(ch)
            logger.info(f"[WARMUP] {phone} join @{ch}")
            actions += 1
            await asyncio.sleep(random.uniform(2.0, 5.0))

            # Fetch beberapa pesan (simulasi baca)
            count = 0
            async for _ in client.get_chat_history(ch, limit=random.randint(5, 15)):
                count += 1
            logger.info(f"[WARMUP] {phone} baca {count} pesan di @{ch}")
            actions += 1
            await asyncio.sleep(random.uniform(3.0, 8.0))

        except UserAlreadyParticipant:
            # Udah member — tetap baca history
            try:
                count = 0
                async for _ in client.get_chat_history(ch, limit=random.randint(5, 15)):
                    count += 1
                logger.info(f"[WARMUP] {phone} baca {count} pesan di @{ch} (sudah member)")
                actions += 1
                await asyncio.sleep(random.uniform(2.0, 5.0))
            except Exception:
                pass

        except FloodWait as fw:
            logger.warning(f"[WARMUP] FloodWait {fw.value}s pada {phone}")
            await asyncio.sleep(fw.value)

        except ChannelPrivate:
            logger.warning(f"[WARMUP] @{ch} private, skip.")

        except Exception as e:
            logger.error(f"[WARMUP] Error {phone} di @{ch}: {e}")

    return {"phone": phone, "actions": actions}


async def run_warmup(admin_user_id: int = 0) -> dict:
    """
    Warmup semua sender aktif.
    Return dict hasil.
    """
    all_senders = await db.get_all_senders()
    active = [s for s in all_senders if s.get("is_active")]

    if not active:
        return {
            "success": False,
            "message": "🚫 Tidak ada sender aktif.",
            "total_senders": 0,
        }

    logger.info(f"[WARMUP] Mulai warmup {len(active)} sender...")
    results = []

    for sender in active:
        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)
        if client is None:
            logger.warning(f"[WARMUP] {phone} tidak di pool, skip.")
            results.append({"phone": phone, "actions": 0})
            continue

        res = await _warmup_one(client, phone)
        results.append(res)

        # Jeda antar sender
        await asyncio.sleep(random.uniform(5.0, 12.0))

    total_actions = sum(r["actions"] for r in results)
    success_count = sum(1 for r in results if r["actions"] > 0)

    msg = (
        f"✅ <b>Warmup Selesai!</b>\n\n"
        f"👥 Sender diproses: <b>{len(active)}</b>\n"
        f"✅ Sender sukses: <b>{success_count}</b>\n"
        f"⚡ Total aksi: <b>{total_actions}</b>\n\n"
        f"<i>Sender sudah lebih siap untuk report.</i>"
    )

    return {
        "success": True,
        "message": msg,
        "total_senders": len(active),
        "success_count": success_count,
        "total_actions": total_actions,
    }
