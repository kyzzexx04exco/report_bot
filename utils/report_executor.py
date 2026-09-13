"""
utils/report_executor.py
"""

import asyncio
import logging
import random

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
    return random.uniform(min_s, max_s)


# ─────────────────────────────────────────────────────────────
# FIX #3: cek koneksi tanpa pakai is_connected (bukan property publik)
# ─────────────────────────────────────────────────────────────

async def _ensure_connected(phone: str, session_string: str) -> "Client | None":
    """
    Pyrogram 2.0.106: is_connected bukan property publik.
    Cara aman: coba invoke GetConfig ringan, kalau gagal → reconnect.
    """
    client = pyro_client.get_client(phone)

    if client is not None:
        try:
            # Ping ringan — kalau konek ini berhasil
            await client.invoke(raw_types.help.GetConfig())
            return client
        except Exception:
            # Disconnect atau error apapun → reconnect
            await pyro_client.remove_client_from_pool(phone)

    # Reconnect
    try:
        logger.info(f"[RECONNECT] {phone} reconnecting...")
        new_client = pyro_client.create_client_from_session(phone, session_string)
        await new_client.start()
        await pyro_client.add_client_to_pool(phone, new_client)
        logger.info(f"[RECONNECT] ✅ {phone} reconnected")
        return new_client
    except Exception as e:
        logger.error(f"[RECONNECT] ❌ {phone} gagal reconnect: {e}")
        return None


async def _get_active_clients() -> list[tuple[str, "Client"]]:
    """
    Return list (phone, client) dari semua sender aktif yang connected.
    Auto-reconnect kalau putus.
    """
    all_senders = await db.get_all_senders()
    result = []
    for sender in all_senders:
        if not sender.get("is_active"):
            continue
        phone = sender["phone_number"]
        session = sender.get("session_string", "")
        if not session:
            continue
        client = await _ensure_connected(phone, session)
        if client:
            result.append((phone, client))
        else:
            logger.warning(f"[POOL] {phone} tidak bisa konek, skip.")
    return result


# ─────────────────────────────────────────────────────────────
# AUTO JOIN
# ─────────────────────────────────────────────────────────────

async def auto_join(client: "Client", peer_str: str, phone: str) -> bool:
    try:
        await client.join_chat(peer_str)
        logger.info(f"[JOIN] ✅ {phone} join ke {peer_str}")
        return True
    except UserAlreadyParticipant:
        return True
    except (InviteHashExpired, InviteHashInvalid):
        logger.warning(f"[JOIN] ❌ {phone} link invite tidak valid: {peer_str}")
        return False
    except ChannelPrivate:
        logger.warning(f"[JOIN] ❌ {phone} channel private: {peer_str}")
        return False
    except FloodWait as fw:
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
    clients = await _get_active_clients()
    results = {}
    for phone, client in clients:
        success = await auto_join(client, peer_str, phone)
        results[phone] = success
        await asyncio.sleep(random.uniform(1.0, 2.5))
    joined = sum(1 for v in results.values() if v)
    logger.info(f"[JOIN] Total {joined}/{len(results)} sender join ke {peer_str}")
    return results


# ─────────────────────────────────────────────────────────────
# FIX #2: fetch message IDs sebanyak mungkin (limit tinggi)
# ─────────────────────────────────────────────────────────────

async def fetch_recent_message_ids(
    client: "Client",
    peer_str: str,
    limit: int = 100,
) -> list[int]:
    """
    Ambil message ID terbaru dari channel/group.
    Limit 100 — Telegram messages.Report bisa terima list banyak ID sekaligus,
    ini setara manual select all messages lalu report.
    """
    try:
        ids = []
        async for msg in client.get_chat_history(peer_str, limit=limit):
            if msg.id:
                ids.append(msg.id)
        logger.info(f"[FETCH] Dapat {len(ids)} message ID dari {peer_str}")
        return ids
    except ChannelPrivate:
        logger.warning(f"[FETCH] Channel private: {peer_str}")
        return []
    except Exception as e:
        logger.warning(f"[FETCH] Gagal fetch dari {peer_str}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# SINGLE REPORT — messages.Report (dengan message IDs)
# ─────────────────────────────────────────────────────────────

async def do_report(
    client: "Client",
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
# SINGLE REPORT — account.ReportPeer (untuk bot/user/fallback)
# ─────────────────────────────────────────────────────────────

async def do_report_peer(
    client: "Client",
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
# CORE ENGINE
# ─────────────────────────────────────────────────────────────

async def _run_engine(
    peer_str: str,
    option_key: str,
    comment: str,
    admin_user_id: int,
    repeat: int,
    message_ids: list[int],
    label_prefix: str = "REPORT",
) -> dict:
    clients = await _get_active_clients()

    if not clients:
        return {
            "success": False,
            "message": "🚫 Tidak ada sender aktif / semua offline.",
            "total_sent": 0,
            "total_senders": 0,
        }

    use_msg_report = len(message_ids) > 0
    method = "messages.Report" if use_msg_report else "account.ReportPeer"
    logger.info(
        f"[{label_prefix}] Mulai — method: {method}, "
        f"{len(clients)} sender, repeat: {repeat}x, "
        f"msg_ids: {len(message_ids)}"
    )

    total_sent = 0
    failed_senders = []
    success_senders = []

    for phone, client in clients:
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
                logger.info(
                    f"[{label_prefix}] ✅ {phone} — {i+1}/{repeat} "
                    f"(total: {total_sent})"
                )

                if i < repeat - 1:
                    delay = _random_delay(3.0, 10.0)
                    await asyncio.sleep(delay)

            except FloodWait as fw:
                logger.warning(
                    f"[{label_prefix}] FloodWait {fw.value}s pada {phone}. Skip."
                )
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                break

            except ValueError as e:
                logger.error(f"[{label_prefix}] Peer error: {e}")
                return {
                    "success": False,
                    "message": f"❌ {e}",
                    "total_sent": total_sent,
                    "total_senders": len(clients),
                }

            except Exception as e:
                logger.error(f"[{label_prefix}] Error {phone}: {e}")
                break

        if sender_sent > 0:
            success_senders.append(phone)
            await db.update_sender_last_used(phone)
        else:
            failed_senders.append(phone)

        await asyncio.sleep(_random_delay(2.0, 5.0))

    if total_sent == 0:
        return {
            "success": False,
            "message": "🚫 Semua sender gagal mengirim laporan.",
            "total_sent": 0,
            "total_senders": len(clients),
        }

    method_note = (
        f"📨 messages.Report ({len(message_ids)} pesan)"
        if use_msg_report
        else "📡 account.ReportPeer"
    )
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
        "total_senders": len(clients),
        "success_senders": success_senders,
        "failed_senders": failed_senders,
    }


# ─────────────────────────────────────────────────────────────
# PUBLIC API
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
    /report — message IDs sudah diketahui dari link pesan.
    Auto-join dulu, lalu messages.Report.
    """
    await join_all_senders(peer_str)
    await asyncio.sleep(2)
    return await _run_engine(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        message_ids=message_ids,
        label_prefix="REPORT",
    )


async def run_report_peer(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """
    /reportv2, /reportpriv — auto-fetch 100 message IDs terbaru.
    Kalau berhasil → messages.Report (lebih efektif, setara manual select all).
    Kalau gagal fetch → fallback account.ReportPeer.
    """
    await join_all_senders(peer_str)
    await asyncio.sleep(2)

    # Fetch dari sender pertama yang aktif
    message_ids: list[int] = []
    clients = await _get_active_clients()
    for phone, client in clients:
        message_ids = await fetch_recent_message_ids(client, peer_str, limit=100)
        if message_ids:
            logger.info(
                f"[REPORT-PEER] Pakai messages.Report "
                f"dengan {len(message_ids)} message IDs"
            )
            break

    if not message_ids:
        logger.info("[REPORT-PEER] Fallback ke account.ReportPeer")

    return await _run_engine(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
        message_ids=message_ids,
        label_prefix="REPORT-PEER",
    )


async def run_report_bot(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """
    /reportbot — report user/bot langsung.
    Skip join (user/bot tidak bisa di-join).
    Pakai account.ReportPeer.
    """
    clients = await _get_active_clients()

    if not clients:
        return {
            "success": False,
            "message": "🚫 Tidak ada sender aktif / semua offline.",
            "total_sent": 0,
        }

    logger.info(
        f"[REPORT-BOT] Mulai report @{peer_str} — "
        f"{len(clients)} sender, repeat: {repeat}x"
    )

    total_sent = 0
    success_senders = []
    failed_senders = []

    for phone, client in clients:
        sender_sent = 0

        for i in range(repeat):
            try:
                await do_report_peer(
                    client=client,
                    peer_str=peer_str,
                    option_key=option_key,
                    comment=comment,
                )
                sender_sent += 1
                total_sent += 1
                logger.info(
                    f"[REPORT-BOT] ✅ {phone} — {i+1}/{repeat} "
                    f"(total: {total_sent})"
                )

                if i < repeat - 1:
                    await asyncio.sleep(_random_delay(3.0, 10.0))

            except FloodWait as fw:
                logger.warning(
                    f"[REPORT-BOT] FloodWait {fw.value}s pada {phone}. Skip."
                )
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                break

            except ValueError as e:
                logger.error(f"[REPORT-BOT] Peer error: {e}")
                return {
                    "success": False,
                    "message": (
                        f"❌ {e}\n\n"
                        "Pastikan username bot benar dan tanpa @."
                    ),
                    "total_sent": total_sent,
                }

            except Exception as e:
                logger.error(f"[REPORT-BOT] Error {phone}: {e}")
                break

        if sender_sent > 0:
            success_senders.append(phone)
            await db.update_sender_last_used(phone)
        else:
            failed_senders.append(phone)

        await asyncio.sleep(_random_delay(2.0, 5.0))

    if total_sent == 0:
        return {
            "success": False,
            "message": "🚫 Semua sender gagal report bot.",
            "total_sent": 0,
        }

    return {
        "success": True,
        "message": (
            f"✅ <b>Report Bot Selesai!</b>\n\n"
            f"📊 Total terkirim: <b>{total_sent}</b> laporan\n"
            f"👥 Sender sukses: <b>{len(success_senders)}</b> akun\n"
            f"❌ Sender gagal: <b>{len(failed_senders)}</b> akun\n"
            f"🤖 Target: <code>@{peer_str}</code>\n"
            f"🔁 Loop per sender: <b>{repeat}x</b>"
        ),
        "total_sent": total_sent,
        "success_senders": success_senders,
        "failed_senders": failed_senders,
    }
