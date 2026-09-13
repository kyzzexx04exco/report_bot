"""
utils/report_executor.py

Setiap sender:
1. Ensure connected (reconnect jika putus)
2. Set online status (UpdateStatus offline=False) → keliatan online
3. Join channel/group target
4. GetHistory + ReadHistory + ReadMessageContents → nambah view, persis buka manual
5. messages.Report dengan IDs (atau account.ReportPeer sebagai fallback)
"""

import asyncio
import logging
import random

from pyrogram import Client
from pyrogram.errors import (
    ChannelPrivate,
    ChatAdminRequired,
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

def _build_reason_map() -> dict:
    base = {
        "spam":        raw_types.InputReportReasonSpam,
        "violence":    raw_types.InputReportReasonViolence,
        "porn":        raw_types.InputReportReasonPornography,
        "child_abuse": raw_types.InputReportReasonChildAbuse,
        "copyright":   raw_types.InputReportReasonCopyright,
        "fake":        raw_types.InputReportReasonFake,
        "other":       raw_types.InputReportReasonOther,
        "fraud":       raw_types.InputReportReasonOther,
    }
    try:
        base["personal_info"] = raw_types.InputReportReasonPersonalData
    except AttributeError:
        base["personal_info"] = raw_types.InputReportReasonOther
    return base


REASON_MAP = _build_reason_map()


def get_reason_cls(option_key: str):
    return REASON_MAP.get(option_key, raw_types.InputReportReasonOther)


def _rnd(a: float, b: float) -> float:
    return random.uniform(a, b)


# ─────────────────────────────────────────────────────────────
# SET ONLINE — kirim sinyal online ke Telegram server
# Ini yang bikin profil sender keliatan "online" saat aktif
# ─────────────────────────────────────────────────────────────

async def _set_online(client: Client, phone: str) -> None:
    try:
        await client.invoke(
            functions.account.UpdateStatus(offline=False)
        )
        logger.info(f"[ONLINE] ✅ {phone} set online")
    except Exception as e:
        logger.warning(f"[ONLINE] {phone} gagal set online (non-fatal): {e}")


async def _set_offline(client: Client, phone: str) -> None:
    try:
        await client.invoke(
            functions.account.UpdateStatus(offline=True)
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# GET ACTIVE CLIENTS — dengan ensure_connected per sender
# ─────────────────────────────────────────────────────────────

async def _get_active_clients() -> list[tuple[str, Client]]:
    all_senders = await db.get_all_senders()
    result = []
    for sender in all_senders:
        if not sender.get("is_active"):
            continue
        phone = sender["phone_number"]
        session = sender.get("session_string", "")
        if not session:
            continue
        client = await pyro_client.ensure_connected(phone, session)
        if client:
            result.append((phone, client))
        else:
            logger.warning(f"[POOL] {phone} tidak bisa konek, skip.")
    return result


# ─────────────────────────────────────────────────────────────
# AUTO JOIN
# ─────────────────────────────────────────────────────────────

async def _join(client: Client, peer_str: str, phone: str) -> bool:
    try:
        await client.join_chat(peer_str)
        logger.info(f"[JOIN] ✅ {phone} join {peer_str}")
        return True
    except UserAlreadyParticipant:
        return True
    except (InviteHashExpired, InviteHashInvalid):
        logger.warning(f"[JOIN] ❌ {phone} link invite invalid: {peer_str}")
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


async def join_all_senders(peer_str: str) -> None:
    clients = await _get_active_clients()
    joined = 0
    for phone, client in clients:
        await _set_online(client, phone)
        ok = await _join(client, peer_str, phone)
        if ok:
            joined += 1
        await asyncio.sleep(_rnd(1.0, 2.5))
    logger.info(f"[JOIN] {joined}/{len(clients)} sender join {peer_str}")


# ─────────────────────────────────────────────────────────────
# SIMULATE OPEN CHANNEL — GetHistory + ReadHistory + ReadMessageContents
# Persis yang dilakukan Telegram client saat user buka channel manual:
#   → view counter nambah
#   → sender keliatan aktif di channel
# ─────────────────────────────────────────────────────────────

async def _simulate_open(
    client: Client,
    peer_str: str,
    phone: str,
    limit: int = 200,
) -> list[int]:
    ids = []
    try:
        # GetHistory — fetch pesan (trigger view di Telegram)
        async for msg in client.get_chat_history(peer_str, limit=limit):
            if msg.id:
                ids.append(msg.id)

        if not ids:
            return []

        logger.info(f"[OPEN] {phone} fetch {len(ids)} pesan dari {peer_str}")
        peer = await client.resolve_peer(peer_str)
        max_id = max(ids)

        # ReadHistory — mark as read (group & channel)
        try:
            await client.invoke(
                functions.messages.ReadHistory(peer=peer, max_id=max_id)
            )
            logger.info(f"[READ] {phone} ReadHistory max_id={max_id}")
        except Exception as e:
            logger.warning(f"[READ] {phone} ReadHistory gagal (non-fatal): {e}")

        # ReadMessageContents — nambah view counter di channel post
        try:
            await client.invoke(
                functions.channels.ReadMessageContents(
                    channel=peer,
                    id=ids[:100],
                )
            )
            logger.info(f"[VIEW] {phone} ReadMessageContents {len(ids[:100])} pesan")
        except (ChatAdminRequired, AttributeError):
            pass  # group biasa, skip
        except Exception as e:
            logger.warning(f"[VIEW] {phone} ReadMessageContents gagal (non-fatal): {e}")

        return ids

    except ChannelPrivate:
        logger.warning(f"[OPEN] {phone} channel private: {peer_str}")
        return []
    except Exception as e:
        logger.warning(f"[OPEN] {phone} gagal fetch {peer_str}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# REPORT — messages.Report (dengan IDs) & account.ReportPeer (fallback)
# ─────────────────────────────────────────────────────────────

async def _report_messages(
    client: Client, peer_str: str,
    message_ids: list[int], option_key: str, comment: str,
) -> bool:
    reason_cls = get_reason_cls(option_key)
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e
    result = await client.invoke(
        functions.messages.Report(
            peer=peer, id=message_ids,
            reason=reason_cls(), message=comment,
        )
    )
    return bool(result)


async def _report_peer(
    client: Client, peer_str: str,
    option_key: str, comment: str,
) -> bool:
    reason_cls = get_reason_cls(option_key)
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e
    result = await client.invoke(
        functions.account.ReportPeer(
            peer=peer, reason=reason_cls(), message=comment,
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
    seed_message_ids: list[int],
    do_join: bool = True,
    do_open: bool = True,
    label: str = "REPORT",
) -> dict:
    """
    Core engine — per sender:
      1. ensure_connected
      2. set online
      3. join (opsional)
      4. simulate open → GetHistory + ReadHistory + ReadMessageContents
         (view nambah, persis buka manual)
      5. messages.Report dengan fresh IDs per sender
         (atau seed_message_ids jika fetch gagal)
         (atau account.ReportPeer jika semua gagal)
      6. repeat sesuai setting
    """
    clients = await _get_active_clients()

    if not clients:
        return {
            "success": False,
            "message": "🚫 Tidak ada sender aktif / semua offline.",
            "total_sent": 0,
            "total_senders": 0,
        }

    logger.info(
        f"[{label}] Mulai — {len(clients)} sender, "
        f"repeat: {repeat}x, target: {peer_str}"
    )

    total_sent = 0
    success_senders = []
    failed_senders = []

    for phone, client in clients:

        # ── 1. Set online ──
        await _set_online(client, phone)

        # ── 2. Join ──
        if do_join:
            await _join(client, peer_str, phone)
            await asyncio.sleep(_rnd(1.0, 2.0))

        # ── 3. Simulate open — fetch IDs fresh per sender ──
        fresh_ids: list[int] = []
        if do_open:
            fresh_ids = await _simulate_open(client, peer_str, phone, limit=200)
            if fresh_ids:
                logger.info(f"[{label}] {phone} fresh IDs: {len(fresh_ids)}")

        # Gabung fresh + seed, deduplikasi, cap 200
        if fresh_ids:
            effective_ids = fresh_ids + [i for i in seed_message_ids if i not in fresh_ids]
            effective_ids = effective_ids[:200]
        else:
            effective_ids = seed_message_ids

        use_msg = len(effective_ids) > 0
        method = "messages.Report" if use_msg else "account.ReportPeer (fallback)"
        logger.info(f"[{label}] {phone} → {method} ({len(effective_ids)} IDs)")

        sender_sent = 0

        for i in range(repeat):
            try:
                if use_msg:
                    await _report_messages(client, peer_str, effective_ids, option_key, comment)
                else:
                    await _report_peer(client, peer_str, option_key, comment)

                sender_sent += 1
                total_sent += 1
                logger.info(f"[{label}] ✅ {phone} {i+1}/{repeat} (total: {total_sent})")

                if i < repeat - 1:
                    await asyncio.sleep(_rnd(2.0, 7.0))

            except FloodWait as fw:
                logger.warning(f"[{label}] FloodWait {fw.value}s pada {phone} — sender dinonaktifkan.")
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                break

            except ValueError as e:
                logger.error(f"[{label}] Peer error: {e}")
                return {
                    "success": False,
                    "message": f"❌ {e}",
                    "total_sent": total_sent,
                    "total_senders": len(clients),
                }

            except Exception as e:
                logger.error(f"[{label}] Error {phone}: {e}")
                break

        # Set offline setelah sender selesai
        await _set_offline(client, phone)

        if sender_sent > 0:
            success_senders.append(phone)
            await db.update_sender_last_used(phone)
        else:
            failed_senders.append(phone)

        await asyncio.sleep(_rnd(1.5, 4.0))

    if total_sent == 0:
        return {
            "success": False,
            "message": "🚫 Semua sender gagal mengirim laporan.",
            "total_sent": 0,
            "total_senders": len(clients),
        }

    method_note = (
        f"📨 messages.Report ({len(seed_message_ids or effective_ids)} pesan)"
        if seed_message_ids or fresh_ids
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
    """/report — user sudah pilih pesan spesifik."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=message_ids,
        do_join=True, do_open=True, label="REPORT",
    )


async def run_report_peer(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """/reportv2, /reportpriv — fetch IDs otomatis per sender."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=[],
        do_join=True, do_open=True, label="REPORT-PEER",
    )


async def run_report_bot(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """/reportbot — report user/bot, skip join & open channel."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=[],
        do_join=False, do_open=False, label="REPORT-BOT",
    )
