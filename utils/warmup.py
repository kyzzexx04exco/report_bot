"""
utils/warmup.py
Warmup sender — simulasi user biasa buka & baca Telegram.

Per sender:
1. Set online → keliatan online di profil
2. Join channel warmup (jika belum)
3. GetHistory → baca pesan (simulasi scroll)
4. ReadHistory → mark as read
5. Set offline setelah selesai
"""

import asyncio
import logging
import random

from pyrogram import Client
from pyrogram.errors import ChannelPrivate, FloodWait, UserAlreadyParticipant
from pyrogram.raw import functions

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)

WARMUP_CHANNELS = [
    "telegram",
    "durov",
    "TelegramTips",
    "telegramwallpapers",
]


async def _set_online(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=False))
        logger.info(f"[WARMUP] ✅ {phone} set online")
    except Exception as e:
        logger.warning(f"[WARMUP] {phone} gagal set online: {e}")


async def _set_offline(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=True))
    except Exception:
        pass


async def _warmup_one(client: Client, phone: str) -> dict:
    actions = 0

    # Set online sebelum mulai — ini yang bikin profil keliatan aktif
    await _set_online(client, phone)

    for ch in WARMUP_CHANNELS:
        try:
            # Join
            try:
                await client.join_chat(ch)
                logger.info(f"[WARMUP] {phone} join @{ch}")
                actions += 1
                await asyncio.sleep(random.uniform(2.0, 5.0))
            except UserAlreadyParticipant:
                pass

            # GetHistory — simulasi buka & scroll channel
            count = 0
            peer = None
            async for msg in client.get_chat_history(ch, limit=random.randint(10, 20)):
                count += 1
                if peer is None and msg.id:
                    peer = msg  # simpan untuk resolve
            logger.info(f"[WARMUP] {phone} baca {count} pesan di @{ch}")
            actions += 1

            # ReadHistory — mark as read
            try:
                resolved = await client.resolve_peer(ch)
                async for msg in client.get_chat_history(ch, limit=1):
                    await client.invoke(
                        functions.messages.ReadHistory(
                            peer=resolved,
                            max_id=msg.id,
                        )
                    )
                    logger.info(f"[WARMUP] {phone} ReadHistory @{ch}")
                    break
            except Exception as e:
                logger.warning(f"[WARMUP] {phone} ReadHistory @{ch} gagal: {e}")

            await asyncio.sleep(random.uniform(3.0, 8.0))

        except FloodWait as fw:
            logger.warning(f"[WARMUP] FloodWait {fw.value}s pada {phone}")
            await asyncio.sleep(fw.value)
        except ChannelPrivate:
            logger.warning(f"[WARMUP] @{ch} private, skip.")
        except Exception as e:
            logger.error(f"[WARMUP] Error {phone} di @{ch}: {e}")

    # Set offline setelah warmup selesai
    await _set_offline(client, phone)

    return {"phone": phone, "actions": actions}


async def run_warmup(admin_user_id: int = 0) -> dict:
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
        session = sender.get("session_string", "")

        # Ensure connected sebelum warmup
        client = await pyro_client.ensure_connected(phone, session)
        if client is None:
            logger.warning(f"[WARMUP] {phone} tidak bisa konek, skip.")
            results.append({"phone": phone, "actions": 0})
            continue

        res = await _warmup_one(client, phone)
        results.append(res)
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
