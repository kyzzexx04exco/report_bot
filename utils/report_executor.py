"""
utils/report_executor.py

Improvement v2:
1. Auto-fetch message IDs terbaru dari channel target
2. messages.Report (bukan account.ReportPeer) — lebih efektif
3. Jeda antar report random 3-10 detik (natural, hindari deteksi spam)
4. Jeda antar sender random 2-5 detik
5. account.ReportPeer tetap ada sebagai fallback
"""

import asyncio
import logging
import random
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import (
    ChannelPrivate,
    FloodWait,
    InviteHashExpired,
    InviteHashInvalid,
    PeerIdInvalid,
    UserAlreadyParticipant,
    UsernameNotOccupied,
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


def _random_delay(min_s: float = 3.0, max_s: float = 10.0) -> float:
    """Jeda random biar keliatan natural."""
    return random.uniform(min_s, max_s)


# ─────────────────────────────────────────────────────────────
# AUTO JOIN
# ─────────────────────────────────────────────────────────────

async def auto_join(client: Client, peer_str: str, phone: str) -> bool:
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
        logger.warning(f"[JOIN] ❌ {phone} channel private: {peer_str}")
        return False
    except FloodWait as fw:
        logger.warning(f"[JOIN] FloodWait {fw.value}s pada {phone}")
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
    all_senders = await db.get_all_senders()
    results = {}
    for sender in all_senders:
        if not sender.get("is_active"):
            continue
        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)
        if client is None:
            results[phone] = False
            continue
        success = await auto_join(client, peer_str, phone)
        results[phone] = success
        await asyncio.sleep(random.uniform(1.0, 2.5))
    joined = sum(1 for v in results.values() if v)
    logger.info(f"[JOIN] Total {joined}/{len(results)} sender join ke {peer_str}")
    return results


# ─────────────────────────────────────────────────────────────
# AUTO-FETCH MESSAGE IDs
# ─────────────────────────────────────────────────────────────

async def fetch_recent_message_ids(
    client: Client,
    peer_str: str,
    limit: int = 10,
) -> list[int]:
    """
    Ambil message ID terbaru dari channel/group.
    Dipakai sebagai context untuk messages.Report — jauh lebih
    efektif daripada report tanpa message ID.
    Return list kosong jika gagal (fallback ke ReportPeer).
    """
    try:
        ids = []
        async for msg in client.get_chat_history(peer_str, limit=limit):
            if msg.id:
                ids.append(msg.id)
        logger.info(f"[FETCH] Dapat {len(ids)} message ID dari {peer_str}")
        return ids
    except ChannelPrivate:
        logger.warning(f"[FETCH] Channel private, tidak bisa fetch messages: {peer_str}")
        return []
    except Exception as e:
        logger.warning(f"[FETCH] Gagal fetch message IDs dari {peer_str}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# SINGLE REPORT — messages.Report (dengan message IDs)
# ─────────────────────────────────────────────────────────────

async def do_report(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
) -> bool:
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
# SINGLE REPORT — account.ReportPeer (fallback, tanpa message IDs)
# ─────────────────────────────────────────────────────────────

async def do_report_peer(
    client: Client,
    peer_str: str,
    option_key: str,
    comment: str = "",
) -> bool:
    reason_cls = get_reason_cls(option_key)
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e

    result = await client.invoke(
        functions.account.ReportPeer(
            peer=peer,
            reason=reason_cls(),
            message=comment,
        )
    )
    return bool(result)


# ─────────────────────────────────────────────────────────────
# CORE ENGINE — dipakai semua fungsi run_report_*
# ─────────────────────────────────────────────────────────────

async def _run_engine(
    peer_str: str,
    option_key: str,
    comment: str,
    admin_user_id: int,
    repeat: int,
    message_ids: list[int],  # kosong = pakai ReportPeer
    label_prefix: str = "REPORT",
) -> dict:
    """
    Core engine: semua sender aktif nge-report.
    Kalau message_ids ada → pakai messages.Report (lebih efektif).
    Kalau kosong → fallback ke account.ReportPeer.
    Jeda antar report: random 3-10 detik (natural).
    Jeda antar sender: random 2-5 detik.
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

    use_msg_report = len(message_ids) > 0
    method = "messages.Report" if use_msg_report else "account.ReportPeer"
    logger.info(f"[{label_prefix}] Mulai — method: {method}, {len(active_senders)} sender, repeat: {repeat}x")

    total_sent = 0
    failed_senders = []
    success_senders = []

    for sender in active_senders:
        phone = sender["phone_number"]
        client = pyro_client.get_client(phone)

        if client is None:
            logger.warning(f"[{label_prefix}] {phone} tidak di pool, skip.")
            failed_senders.append(phone)
            continue

        sender_sent = 0

        for i in range(repeat):
            try:
                if use_msg_report:
                    await do_report(
                        client=client,
                        peer_str=peer_str,
                        message_ids=message_ids,
                        option_key=option_key,
                        comment=comment,
                    )
                else:
                    await do_report_peer(
                        client=client,
                        peer_str=peer_str,
                        option_key=option_key,
                        comment=comment,
                    )

                sender_sent += 1
                total_sent += 1
                logger.info(f"[{label_prefix}] ✅ {phone} — {i+1}/{repeat} (total: {total_sent})")

                if i < repeat - 1:
                    delay = _random_delay(3.0, 10.0)
                    logger.info(f"[{label_prefix}] Jeda {delay:.1f}s sebelum report berikutnya...")
                    await asyncio.sleep(delay)

            except FloodWait as fw:
                logger.warning(f"[{label_prefix}] ⚠️ FloodWait {fw.value}s pada {phone}. Skip sender.")
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                break

            except ValueError as e:
                logger.error(f"[{label_prefix}] Peer error: {e}")
                return {
                    "success": False,
                    "message": f"❌ {e}",
                    "total_sent": total_sent,
                    "total_senders": len(active_senders),
                }

            except Exception as e:
                logger.error(f"[{label_prefix}] Error {phone}: {e}")
                break

        if sender_sent > 0:
            success_senders.append(phone)
            await db.update_sender_last_used(phone)
        else:
            failed_senders.append(phone)

        # Jeda antar sender — random biar natural
        sender_delay = _random_delay(2.0, 5.0)
        logger.info(f"[{label_prefix}] Jeda {sender_delay:.1f}s sebelum sender berikutnya...")
        await asyncio.sleep(sender_delay)

    if total_sent == 0:
        return {
            "success": False,
            "message": "🚫 Semua sender gagal mengirim laporan.",
            "total_sent": 0,
            "total_senders": len(active_senders),
        }

    method_note = "📨 messages.Report (dengan pesan)" if use_msg_report else "📡 account.ReportPeer"
    msg = (
        f"✅ <b>Laporan Selesai!</b>\n\n"
        f"📊 Total terkirim: <b>{total_sent}</b> laporan\n"
        f"👥 Sender sukses: <b>{len(success_senders)}</b> akun\n"
        f"❌ Sender gagal: <b>{len(failed_senders)}</b> akun\n"
        f"🎯 Target: <code>{peer_str}</code>\n"
        f"🔁 Loop per sender: <b>{repeat}x</b>\n"
        f"🔧 Method: {method_note}"
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
# PUBLIC API
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
    Report dengan message IDs yang sudah diketahui (dari /report command).
    Auto-join dulu, lalu messages.Report.
    """
    if auto_join_first:
        logger.info(f"[REPORT-ALL] Auto-join sender ke {peer_str}...")
        await join_all_senders(peer_str)
        await asyncio.sleep(2)

    return await _run_engine(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        message_ids=message_ids,
        label_prefix="REPORT-ALL",
    )


async def run_report_with_rotation(
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """Wrapper kompatibilitas — pakai run_report_all_senders."""
    return await run_report_all_senders(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        auto_join_first=True,
    )


async def run_report_peer(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """
    Report channel/group/bot tanpa message IDs yang diketahui.
    Auto-join dulu, fetch message IDs terbaru, lalu:
    - Kalau berhasil fetch → messages.Report (lebih efektif)
    - Kalau gagal fetch (private/error) → fallback account.ReportPeer
    """
    # Auto-join dulu
    logger.info(f"[REPORT-PEER] Auto-join sender ke {peer_str}...")
    await join_all_senders(peer_str)
    await asyncio.sleep(2)

    # Coba fetch message IDs dari sender pertama yang aktif
    message_ids: list[int] = []
    all_senders = await db.get_all_senders()
    for s in all_senders:
        if not s.get("is_active"):
            continue
        client = pyro_client.get_client(s["phone_number"])
        if client:
            message_ids = await fetch_recent_message_ids(client, peer_str, limit=10)
            break

    if message_ids:
        logger.info(f"[REPORT-PEER] Pakai messages.Report dengan {len(message_ids)} message IDs")
    else:
        logger.info(f"[REPORT-PEER] Fallback ke account.ReportPeer (tidak bisa fetch messages)")

    return await _run_engine(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        message_ids=message_ids,
        label_prefix="REPORT-PEER",
    )
