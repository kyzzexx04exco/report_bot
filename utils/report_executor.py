"""
utils/report_executor.py

Implementasi PERSIS seperti Telegram official client berdasarkan:
  https://core.telegram.org/api/views

Sequence per sender (sama persis dengan buka channel manual):

  1. account.UpdateStatus(offline=False)       → set online
  2. channels.JoinChannel / messages.ImportChatInvite → join
  3. messages.GetHistory                        → fetch pesan (buka channel)
  4. channels.ReadHistory(max_id)               → mark read (channel/supergroup)
     ATAU messages.ReadHistory(max_id)          → mark read (basic group/user)
  5. messages.GetMessagesViews(increment=True)  → INCREMENT VIEW COUNTER
     ↑ INI SATU-SATUNYA yang nambah view, konfirmasi dari official docs:
     "to increment the view counter... invoke messages.getMessagesViews
      with increment set to boolTrue"
     ReadHistory dan ReadMessageContents TIDAK nambah view counter.
  6. messages.Report(peer, ids, reason)         → report dengan IDs
     ATAU account.ReportPeer(peer, reason)      → fallback tanpa IDs

Untuk reportbot:
  1. account.UpdateStatus(offline=False)        → set online
  2. messages.StartBot                          → sender ketuk /start di bot target (1x saja)
  3. account.ReportPeer(peer, reason)           → report user/bot (repeat x)

Catatan view counter dari Telethon docs:
  "you can only do this once or twice a day per account"
  → tidak perlu dipanggil berkali-kali per sender, cukup 1x
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
    ChatAdminRequired,
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
# STEP 1 — SET ONLINE + KEEPALIVE
#
# account.UpdateStatus(offline=False) expire ~15 detik di sisi
# Telegram. Kalau tidak di-refresh, sender langsung keliatan
# offline lagi padahal masih lagi proses report.
#
# Solusi: background task _online_keepalive() yang refresh
# UpdateStatus(offline=False) setiap 10 detik selama sender
# aktif. Task ini di-cancel setelah sender selesai.
# ─────────────────────────────────────────────────────────────

async def _set_online(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=False))
        logger.info(f"[ONLINE] ✅ {phone} set online")
    except Exception as e:
        logger.warning(f"[ONLINE] {phone} gagal set online (non-fatal): {e}")


async def _set_offline(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=True))
    except Exception:
        pass


async def _online_keepalive(client: Client, phone: str, interval: float = 10.0) -> None:
    """
    Background task: refresh UpdateStatus(offline=False) setiap `interval` detik.
    Jalan terus sampai di-cancel dari luar (asyncio.Task.cancel()).
    Interval 10 detik aman — Telegram expire status ~15 detik.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await client.invoke(functions.account.UpdateStatus(offline=False))
                logger.debug(f"[KEEPALIVE] {phone} refresh online")
            except Exception as e:
                logger.debug(f"[KEEPALIVE] {phone} refresh gagal (non-fatal): {e}")
    except asyncio.CancelledError:
        pass  # Normal — di-cancel setelah sender selesai


# ─────────────────────────────────────────────────────────────
# GET ACTIVE CLIENTS — ensure_connected per sender
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
# STEP 2 — JOIN
# ─────────────────────────────────────────────────────────────

async def _join(client: Client, peer_str: str, phone: str) -> bool:
    try:
        await client.join_chat(peer_str)
        logger.info(f"[JOIN] ✅ {phone} join {peer_str}")
        return True
    except UserAlreadyParticipant:
        return True
    except (InviteHashExpired, InviteHashInvalid):
        logger.warning(f"[JOIN] ❌ {phone} invite invalid: {peer_str}")
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
        logger.error(f"[JOIN] ❌ {phone} gagal: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# STEP 3+4 — FETCH + READ HISTORY
# ─────────────────────────────────────────────────────────────

async def _fetch_and_read(
    client: Client,
    peer_str: str,
    phone: str,
    limit: int = 100,
) -> list[int]:
    ids = []
    try:
        async for msg in client.get_chat_history(peer_str, limit=limit):
            if msg.id:
                ids.append(msg.id)

        if not ids:
            return []

        logger.info(f"[FETCH] {phone} dapat {len(ids)} IDs dari {peer_str}")

        peer = await client.resolve_peer(peer_str)
        max_id = max(ids)

        try:
            await client.invoke(
                functions.channels.ReadHistory(
                    channel=peer,
                    max_id=max_id,
                )
            )
            logger.info(f"[READ] {phone} channels.ReadHistory max_id={max_id}")
        except (AttributeError, Exception):
            try:
                await client.invoke(
                    functions.messages.ReadHistory(
                        peer=peer,
                        max_id=max_id,
                    )
                )
                logger.info(f"[READ] {phone} messages.ReadHistory max_id={max_id}")
            except Exception as e:
                logger.warning(f"[READ] {phone} ReadHistory gagal (non-fatal): {e}")

        return ids

    except ChannelPrivate:
        logger.warning(f"[FETCH] {phone} channel private: {peer_str}")
        return []
    except Exception as e:
        logger.warning(f"[FETCH] {phone} gagal: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# STEP 5 — INCREMENT VIEW COUNTER
# ─────────────────────────────────────────────────────────────

async def _increment_views(
    client: Client,
    peer_str: str,
    phone: str,
    message_ids: list[int],
) -> None:
    if not message_ids:
        return
    try:
        peer = await client.resolve_peer(peer_str)
        for i in range(0, len(message_ids), 100):
            batch = message_ids[i:i + 100]
            await client.invoke(
                functions.messages.GetMessagesViews(
                    peer=peer,
                    id=batch,
                    increment=True,
                )
            )
            logger.info(f"[VIEW] ✅ {phone} increment {len(batch)} views @ {peer_str}")
            if i + 100 < len(message_ids):
                await asyncio.sleep(_rnd(1.0, 2.0))
    except ChannelPrivate:
        logger.warning(f"[VIEW] {phone} channel private, skip.")
    except Exception as e:
        logger.warning(f"[VIEW] {phone} gagal increment views (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────
# STEP 2b — START BOT (khusus reportbot)
# Pastikan sender sudah pernah kirim /start ke bot target.
# Hanya dipanggil 1x per sender, BUKAN per repeat iteration.
#
# Flow:
#   1. Coba client.start_bot() → pakai messages.StartBot (MTProto)
#      Ini gagal kalau sender SUDAH pernah /start bot ini sebelumnya.
#   2. Fallback: send_message("/start") manual — ini selalu works,
#      baik sender baru maupun yang sudah pernah chat.
# ─────────────────────────────────────────────────────────────

async def _start_bot(client: Client, peer_str: str, phone: str) -> None:
    try:
        await client.start_bot(peer_str)
        logger.info(f"[STARTBOT] ✅ {phone} start_bot() berhasil → /start terkirim")
    except Exception as e:
        # start_bot() gagal kalau sender sudah pernah chat dengan bot ini.
        # Fallback: kirim "/start" manual lewat send_message — selalu works.
        logger.warning(f"[STARTBOT] {phone} start_bot() gagal ({e}), fallback send_message /start")
        try:
            await client.send_message(peer_str, "/start")
            logger.info(f"[STARTBOT] ✅ {phone} fallback /start terkirim ke @{peer_str}")
        except Exception as e2:
            # Non-fatal: kalau /start juga gagal, lanjut report saja
            logger.warning(f"[STARTBOT] {phone} fallback /start juga gagal (non-fatal): {e2}")
    await asyncio.sleep(_rnd(1.5, 3.0))


# ─────────────────────────────────────────────────────────────
# STEP 6 — REPORT
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
    except KeyError as e:
        # FIX: Pyrogram KeyError 'ID not found: <id>' → peer cache miss
        # Ini terjadi kalau client baru reconnect dan cache belum terisi.
        raise ValueError(f"Peer '{peer_str}' belum di-cache, coba lagi sebentar.") from e
    result = await client.invoke(
        functions.messages.Report(
            peer=peer,
            id=message_ids,
            reason=reason_cls(),
            message=comment,
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
    except KeyError as e:
        # FIX: Pyrogram KeyError 'ID not found: <id>' → peer cache miss setelah reconnect
        raise ValueError(f"Peer '{peer_str}' belum di-cache, coba lagi sebentar.") from e
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
    seed_message_ids: list[int],
    do_join: bool = True,
    do_view: bool = True,
    do_start_bot: bool = False,
    label: str = "REPORT",
) -> dict:
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
        f"repeat={repeat}x, target={peer_str}"
    )

    total_sent = 0
    success_senders = []
    failed_senders = []
    last_effective_ids = seed_message_ids

    for phone, client in clients:

        # ── 1. Set online + mulai keepalive background task ──
        await _set_online(client, phone)
        keepalive_task = asyncio.create_task(
            _online_keepalive(client, phone, interval=10.0)
        )
        sender_sent = 0

        try:
            # ── 2a. Start bot (khusus reportbot) — 1x per sender, sebelum loop repeat ──
            if do_start_bot:
                await _start_bot(client, peer_str, phone)

            # ── 2b. Join channel ──
            if do_join:
                await _join(client, peer_str, phone)
                await asyncio.sleep(_rnd(0.5, 1.5))

            # ── 3+4. Fetch IDs + ReadHistory ──
            fresh_ids: list[int] = []
            if do_view:
                fresh_ids = await _fetch_and_read(client, peer_str, phone, limit=100)

            # Gabung fresh + seed, deduplikasi
            if fresh_ids:
                effective_ids = list(dict.fromkeys(fresh_ids + seed_message_ids))[:100]
            else:
                effective_ids = seed_message_ids

            last_effective_ids = effective_ids

            # ── 5. Increment views — GetMessagesViews(increment=True) ──
            if do_view and effective_ids:
                await _increment_views(client, peer_str, phone, effective_ids)
                await asyncio.sleep(_rnd(0.5, 1.5))

            use_msg = len(effective_ids) > 0
            method = "messages.Report" if use_msg else "account.ReportPeer (fallback)"
            logger.info(f"[{label}] {phone} → {method} ({len(effective_ids)} IDs)")

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
                        await asyncio.sleep(_rnd(2.0, 6.0))

                except FloodWait as fw:
                    logger.warning(f"[{label}] FloodWait {fw.value}s — {phone} dinonaktifkan.")
                    await db.set_sender_inactive(phone)
                    await pyro_client.remove_client_from_pool(phone)
                    break

                except ValueError as e:
                    logger.error(f"[{label}] Peer error: {e}")
                    keepalive_task.cancel()
                    await _set_offline(client, phone)
                    return {
                        "success": False,
                        "message": f"❌ {e}",
                        "total_sent": total_sent,
                        "total_senders": len(clients),
                    }

                except Exception as e:
                    logger.error(f"[{label}] Error {phone}: {e}")
                    break

        finally:
            # ── Cancel keepalive, set offline ──
            keepalive_task.cancel()
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
        f"📨 messages.Report ({len(last_effective_ids)} pesan)"
        if last_effective_ids
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
    """/report — user pilih pesan spesifik."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=message_ids,
        do_join=True, do_view=True, do_start_bot=False,
        label="REPORT",
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
        do_join=True, do_view=True, do_start_bot=False,
        label="REPORT-PEER",
    )


async def run_report_bot(
    peer_str: str,
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """/reportbot — start bot 1x dulu (per sender), lalu report user/bot."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=[],
        do_join=False, do_view=False, do_start_bot=True,
        label="REPORT-BOT",
    )
