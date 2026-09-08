"""
utils/report_executor.py

Fitur:
1. Auto-join semua sender ke channel/grup target sebelum report
2. Semua sender aktif nge-report (bukan round-robin 1 akun)
3. Per sender: loop sebanyak repeat kali
4. FloodWait handling — skip sender, lanjut ke berikutnya
"""

import asyncio
import logging
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
    UsernameNotOccupied,
    UserAlreadyParticipant,
    InviteHashExpired,
    InviteHashInvalid,
    ChannelPrivate,
)
from pyrogram.raw import functions, types as raw_types

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# REASON MAP
# ─────────────────────────────────────────────────────────────

REASON_MAP = {
    "spam":          raw_types.InputReportReasonSpam,
    "violence":      raw_types.InputReportReasonViolence,
    "porn":          raw_types.InputReportReasonPornography,
    "child_abuse":   raw_types.InputReportReasonChildAbuse,
    "copyright":     raw_types.InputReportReasonCopyright,
    "fake":          raw_types.InputReportReasonFake,
    "other":         raw_types.InputReportReasonOther,
    "personal_info": raw_types.InputReportReasonOther,
    "fraud":         raw_types.InputReportReasonOther,
}


def get_reason_cls(option_key: str):
    return REASON_MAP.get(option_key, raw_types.InputReportReasonOther)


# ─────────────────────────────────────────────────────────────
# AUTO JOIN
# ─────────────────────────────────────────────────────────────

async def auto_join(client: Client, peer_str: str, phone: str) -> bool:
    """
    Sender join ke channel/grup target.
    Return True jika berhasil atau sudah member.
    """
    try:
        await client.join_chat(peer_str)
        logger.info(f"[JOIN] ✅ {phone} join ke {peer_str}")
        return True
    except UserAlreadyParticipant:
        logger.info(f"[JOIN] {phone} sudah member di {peer_str}")
        return True
    except (InviteHashExpired, InviteHashInvalid):
        logger.warning(f"[JOIN] ❌ {phone} link invite tidak valid: {peer_str}")
        return False
    except ChannelPrivate:
        logger.warning(f"[JOIN] ❌ {phone} channel private, tidak bisa join: {peer_str}")
        return False
    except FloodWait as fw:
        logger.warning(f"[JOIN] FloodWait {fw.value}s pada {phone} saat join.")
        await asyncio.sleep(fw.value)
        try:
            await client.join_chat(peer_str)
            return True
        except Exception:
            return False
    except Exception as e:
        logger.error(f"[JOIN] ❌ {phone} gagal join {peer_str}: {e}")
        return False


async def join_all_senders(peer_str: str) -> dict:
    """
    Semua sender aktif join ke peer_str.
    Return dict hasil: { phone: True/False }
    """
    all_senders = await db.get_all_senders()
    results = {}

    for sender in all_senders:
        if not sender.get("is_active"):
            continue

        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)

        if client is None:
            logger.warning(f"[JOIN] {phone} tidak ada di pool, skip.")
            results[phone] = False
            continue

        success = await auto_join(client, peer_str, phone)
        results[phone] = success
        await asyncio.sleep(1)  # Jeda kecil antar join biar tidak spam

    joined = sum(1 for v in results.values() if v)
    logger.info(f"[JOIN] Total {joined}/{len(results)} sender berhasil join ke {peer_str}")
    return results


# ─────────────────────────────────────────────────────────────
# SINGLE REPORT (1 sender, 1 kali)
# ─────────────────────────────────────────────────────────────

async def do_report(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
) -> bool:
    """
    Invoke messages.Report untuk 1 sender.
    Raise FloodWait jika kena limit.
    Raise ValueError jika peer tidak ditemukan.
    """
    reason_cls = get_reason_cls(option_key)

    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e

    result = await client.invoke(
        functions.messages.Report(
            peer=peer,
            id=message_ids,
            reason=reason_cls(),
            message=comment,
        )
    )
    return bool(result)


# ─────────────────────────────────────────────────────────────
# ALL SENDER REPORT — Semua sender nge-report
# ─────────────────────────────────────────────────────────────

async def run_report_all_senders(
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
    auto_join_first: bool = True,
) -> dict:
    """
    Semua sender aktif nge-report peer_str.
    Per sender: loop sebanyak `repeat` kali.

    Flow per sender:
      1. Auto-join dulu (jika auto_join_first=True)
      2. Loop report sebanyak repeat kali
      3. Kena FloodWait → catat & skip sender itu
    """
    all_senders = await db.get_all_senders()
    active_senders = [s for s in all_senders if s.get("is_active")]

    if not active_senders:
        return {
            "success": False,
            "message": "🚫 Tidak ada sender aktif. Tambah dulu via /addsender.",
            "total_sent": 0,
            "total_senders": 0,
        }

    # Step 1: Auto-join semua sender dulu
    if auto_join_first:
        logger.info(f"[REPORT-ALL] Auto-join {len(active_senders)} sender ke {peer_str}...")
        await join_all_senders(peer_str)
        await asyncio.sleep(2)  # Jeda setelah semua join

    # Step 2: Semua sender nge-report
    total_sent = 0
    failed_senders = []
    success_senders = []

    for sender in active_senders:
        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)

        if client is None:
            logger.warning(f"[REPORT-ALL] {phone} tidak di pool, skip.")
            failed_senders.append(phone)
            continue

        sender_sent = 0

        for i in range(repeat):
            try:
                await do_report(
                    client=client,
                    peer_str=peer_str,
                    message_ids=message_ids,
                    option_key=option_key,
                    comment=comment,
                )
                sender_sent += 1
                total_sent += 1
                logger.info(
                    f"[REPORT-ALL] ✅ {phone} — {i+1}/{repeat} "
                    f"(total: {total_sent})"
                )
                # Jeda kecil antar report dari sender yang sama
                if i < repeat - 1:
                    await asyncio.sleep(0.5)

            except FloodWait as fw:
                logger.warning(
                    f"[REPORT-ALL] ⚠️ FloodWait {fw.value}s pada {phone} "
                    f"setelah {sender_sent} report. Skip sender ini."
                )
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                break

            except ValueError as e:
                logger.error(f"[REPORT-ALL] Peer error: {e}")
                return {
                    "success": False,
                    "message": f"❌ {e}",
                    "total_sent": total_sent,
                    "total_senders": len(active_senders),
                }

            except Exception as e:
                logger.error(f"[REPORT-ALL] Error {phone}: {e}")
                break

        if sender_sent > 0:
            success_senders.append(phone)
            await db.update_sender_last_used(phone)
        else:
            failed_senders.append(phone)

        # Jeda kecil antar sender
        await asyncio.sleep(1)

    if total_sent == 0:
        return {
            "success": False,
            "message": "🚫 Semua sender gagal mengirim laporan.",
            "total_sent": 0,
            "total_senders": len(active_senders),
        }

    msg = (
        f"✅ <b>Laporan Selesai!</b>\n\n"
        f"📊 Total terkirim: <b>{total_sent}</b> laporan\n"
        f"👥 Sender sukses: <b>{len(success_senders)}</b> akun\n"
        f"❌ Sender gagal: <b>{len(failed_senders)}</b> akun\n"
        f"🎯 Target: <code>{peer_str}</code>\n"
        f"🔁 Loop per sender: <b>{repeat}x</b>"
    )

    return {
        "success": True,
        "message": msg,
        "total_sent": total_sent,
        "total_senders": len(active_senders),
        "success_senders": success_senders,
        "failed_senders": failed_senders,
    }


# ─────────────────────────────────────────────────────────────
# ROUND-ROBIN (tetap ada untuk kompatibilitas)
# ─────────────────────────────────────────────────────────────

async def run_report_with_rotation(
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """
    Wrapper — langsung pakai run_report_all_senders.
    Semua sender nge-report dengan auto-join.
    """
    return await run_report_all_senders(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        auto_join_first=True,
    )
