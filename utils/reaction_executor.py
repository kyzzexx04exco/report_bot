"""
utils/reaction_executor.py

Kirim reaction emoji ke pesan channel/group menggunakan semua sender aktif
secara paralel. Setiap sender reaction ke message_id yang sama sebanyak
`repeat` kali (default 1 per sender, bisa dikonfigurasi).

Flow:
  1. Resolve peer → get InputPeer
  2. Semua sender aktif auto-join ke channel/group
  3. Paralel: setiap sender kirim SendReaction
  4. Collect result → summary
"""

import asyncio
import logging

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
    UsernameNotOccupied,
    UserAlreadyParticipant,
    InviteHashExpired,
    InviteHashInvalid,
    ChannelPrivate,
    ChatAdminRequired,
    ReactionInvalid,
)
from pyrogram.raw import functions, types as raw_types

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# AUTO JOIN
# ─────────────────────────────────────────────────────────────

async def _auto_join(client: Client, peer_str: str, phone: str) -> bool:
    try:
        await client.join_chat(peer_str)
        logger.info(f"[REACT-JOIN] ✅ {phone} join ke {peer_str}")
        return True
    except UserAlreadyParticipant:
        return True
    except (InviteHashExpired, InviteHashInvalid):
        logger.warning(f"[REACT-JOIN] ❌ {phone} invite link invalid.")
        return False
    except ChannelPrivate:
        logger.warning(f"[REACT-JOIN] ❌ {phone} channel private.")
        return False
    except FloodWait as fw:
        logger.warning(f"[REACT-JOIN] FloodWait {fw.value}s pada {phone}.")
        await asyncio.sleep(min(fw.value, 10))
        try:
            await client.join_chat(peer_str)
            return True
        except Exception:
            return False
    except Exception as e:
        logger.warning(f"[REACT-JOIN] ❌ {phone} gagal join: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# SINGLE SENDER REACTION
# ─────────────────────────────────────────────────────────────

async def _send_reaction_once(
    client: Client,
    peer,           # InputPeer resolved
    message_id: int,
    emoji: str,
    phone: str,
) -> bool:
    """Kirim 1 reaction dari 1 sender. Return True jika sukses."""
    try:
        await client.invoke(
            functions.messages.SendReaction(
                peer=peer,
                msg_id=message_id,
                add_to_recent=True,
                reaction=[raw_types.ReactionEmoji(emoticon=emoji)],
            )
        )
        logger.info(f"[REACT] ✅ {phone} → {emoji} on msg {message_id}")
        return True
    except FloodWait as fw:
        logger.warning(f"[REACT] FloodWait {fw.value}s pada {phone}, skip.")
        return False
    except ReactionInvalid:
        logger.warning(f"[REACT] ❌ {phone} emoji '{emoji}' tidak valid di channel ini.")
        return False
    except (PeerIdInvalid, UsernameNotOccupied):
        logger.warning(f"[REACT] ❌ {phone} peer tidak ditemukan.")
        return False
    except Exception as e:
        logger.warning(f"[REACT] ❌ {phone} gagal react: {e}")
        return False


async def _sender_task(
    client: Client,
    phone: str,
    peer_str: str,
    message_id: int,
    emoji: str,
    repeat: int,
    semaphore: asyncio.Semaphore,
) -> dict:
    """
    Task untuk 1 sender:
    - join channel
    - resolve peer
    - kirim reaction sebanyak `repeat` kali
    """
    result = {"phone": phone, "success": 0, "failed": 0}

    async with semaphore:
        # 1. Auto join
        joined = await _auto_join(client, peer_str, phone)
        if not joined:
            result["failed"] = repeat
            return result

        # 2. Resolve peer
        try:
            peer = await client.resolve_peer(peer_str)
        except Exception as e:
            logger.warning(f"[REACT] ❌ {phone} resolve peer gagal: {e}")
            result["failed"] = repeat
            return result

        # 3. Kirim reaction sebanyak repeat kali
        for i in range(repeat):
            ok = await _send_reaction_once(client, peer, message_id, emoji, phone)
            if ok:
                result["success"] += 1
            else:
                result["failed"] += 1
            # Jeda kecil antar repeat agar tidak flood
            if repeat > 1 and i < repeat - 1:
                await asyncio.sleep(0.5)

    return result


# ─────────────────────────────────────────────────────────────
# MAIN EXECUTOR
# ─────────────────────────────────────────────────────────────

async def run_reaction(
    peer_str: str,
    message_id: int,
    emoji: str,
    admin_user_id: int,
    repeat: int = 1,
    max_concurrent: int = 10,
) -> dict:
    """
    Kirim reaction dari semua sender aktif secara paralel.

    Args:
        peer_str      : username / link channel target
        message_id    : ID pesan yang direact
        emoji         : emoji string, contoh "👍" atau "❤"
        admin_user_id : ID owner untuk ambil repeat count
        repeat        : berapa kali tiap sender react (default 1)
        max_concurrent: max sender jalan paralel (default 10)

    Returns:
        dict dengan "message" (teks hasil) dan stats
    """
    all_senders = await db.get_all_senders()
    active_senders = [s for s in all_senders if s.get("is_active")]

    if not active_senders:
        return {
            "message": "❌ Tidak ada sender aktif. Tambah sender dulu dengan /addsender.",
            "total": 0,
            "success": 0,
            "failed": 0,
        }

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = []

    for sender in active_senders:
        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)
        if not client:
            logger.warning(f"[REACT] {phone} tidak ada di pool, skip.")
            continue

        task = asyncio.create_task(
            _sender_task(
                client=client,
                phone=phone,
                peer_str=peer_str,
                message_id=message_id,
                emoji=emoji,
                repeat=repeat,
                semaphore=semaphore,
            )
        )
        tasks.append(task)

    if not tasks:
        return {
            "message": "❌ Tidak ada sender yang siap di pool.",
            "total": 0,
            "success": 0,
            "failed": 0,
        }

    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_success = 0
    total_failed = 0
    sender_ok = 0

    for r in results:
        if isinstance(r, Exception):
            total_failed += repeat
        else:
            total_success += r.get("success", 0)
            total_failed  += r.get("failed", 0)
            if r.get("success", 0) > 0:
                sender_ok += 1

    total_sent = total_success + total_failed

    message = (
        f"✅ <b>Reaction selesai!</b>\n\n"
        f"😀 Emoji     : <b>{emoji}</b>\n"
        f"🔗 Target    : <code>{peer_str}</code>\n"
        f"📩 Message ID: <code>{message_id}</code>\n\n"
        f"📊 <b>Hasil:</b>\n"
        f"├ Sender aktif  : <b>{len(tasks)}</b>\n"
        f"├ Sender sukses : <b>{sender_ok}</b>\n"
        f"├ Total sukses  : <b>{total_success}</b> react\n"
        f"└ Total gagal   : <b>{total_failed}</b> react\n"
    )

    return {
        "message": message,
        "total": total_sent,
        "success": total_success,
        "failed": total_failed,
    }
