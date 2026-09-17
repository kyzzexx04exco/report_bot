"""
utils/report_executor.py

═══════════════════════════════════════════════════════════════
  STATUS API — Dikonfirmasi dari core.telegram.org (Layer 223)
═══════════════════════════════════════════════════════════════

  account.reportPeer#c5ba3d86
    peer:InputPeer reason:ReportReason message:string = Bool
  → MASIH VALID Layer 223. Dipakai /reportbot, /reportv2, /reportpriv.
  → Return Bool langsung. Tidak ada iterasi.

  messages.report#fc78af9b  (BERUBAH di Layer 189+)
    peer:InputPeer id:Vector<int> option:bytes message:string = ReportResult
  → Schema LAMA (≤Layer 188): reason:ReportReason → Bool
  → Schema BARU (≥Layer 189): option:bytes → ReportResult (iteratif)
  → Dipakai /report (report pesan spesifik).

═══════════════════════════════════════════════════════════════
  KEPUTUSAN LIBRARY
═══════════════════════════════════════════════════════════════

  Pyrogram 2.0.106 (PyPI) = Layer 158 → SUDAH DISCONTINUE.
  Schema messages.Report di Layer 158 masih LAMA (reason:ReportReason → Bool).
  Menggunakan schema baru (option:bytes) di Pyrogram 2.0.106 akan CRASH.

  Solusi: pakai pyrofork (fork aktif, Layer 224+) yang sudah support
  schema baru messages.report dengan option:bytes → ReportResult.
  requirements.txt harus diganti: pyrogram → pyrofork

  Jika masih pakai Pyrogram 2.0.106:
  → Semua report (channel/group) fallback ke account.ReportPeer.
  → messages.Report TIDAK dipakai (schema tidak kompatibel).

═══════════════════════════════════════════════════════════════
  FLOW PER CMD
═══════════════════════════════════════════════════════════════

  /reportbot  → _ensure_started() [skip /start jika sudah pernah chat]
              → account.ReportPeer (reason + message → Bool) ← SELALU

  /reportv2,  → join → fetch IDs → GetMessagesViews
  /reportpriv → messages.report iteratif (option flow) jika ada IDs
              → fallback account.ReportPeer jika IDs kosong

  /report     → fetch IDs dari link pesan
              → messages.report iteratif (option flow)

═══════════════════════════════════════════════════════════════
  FIX /reportbot (BUG UTAMA)
═══════════════════════════════════════════════════════════════

  Bug lama: start_bot() gagal → fallback send_message("/start") SELALU
  jalan → bot target dibombardir /start tiap kali report.

  Fix: _ensure_started() — cek history dulu, skip /start jika sudah chat.
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
# DETEKSI LAYER — apakah Pyrogram support schema baru (≥189)?
# ─────────────────────────────────────────────────────────────

def _detect_new_report_api() -> bool:
    """
    Cek apakah Pyrogram yang terinstall support messages.Report
    schema baru (option:bytes, return ReportResult) dari Layer 189+.

    Pyrogram 2.0.106 (Layer 158) = TIDAK support → return False
    pyrofork / pyrogram fork baru (Layer 189+) = support → return True
    """
    try:
        # Schema baru: messages.Report menerima keyword 'option'
        import inspect
        sig = inspect.signature(functions.messages.Report.__init__)
        return "option" in sig.parameters
    except Exception:
        return False


NEW_REPORT_API = _detect_new_report_api()
logger.info(
    f"[API] messages.Report schema: "
    f"{'BARU (option:bytes → ReportResult)' if NEW_REPORT_API else 'LAMA (reason:ReportReason → Bool)'}"
)


# ─────────────────────────────────────────────────────────────
# REASON MAP  (account.ReportPeer & messages.Report schema lama)
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
    # PersonalData: ada di Layer 158+ dengan nama berbeda
    for attr in ("InputReportReasonPersonalData", "InputReportReasonPersonalDetails"):
        if hasattr(raw_types, attr):
            base["personal_info"] = getattr(raw_types, attr)
            break
    else:
        base["personal_info"] = raw_types.InputReportReasonOther
    return base


REASON_MAP = _build_reason_map()


def get_reason_cls(option_key: str):
    return REASON_MAP.get(option_key, raw_types.InputReportReasonOther)


def _rnd(a: float, b: float) -> float:
    return random.uniform(a, b)


# ─────────────────────────────────────────────────────────────
# OPTION KEYWORD MAP  (untuk messages.Report baru — pilih dari menu Telegram)
# Digunakan _find_best_option() untuk cocokkan option_key ke teks menu.
# ─────────────────────────────────────────────────────────────

OPTION_KEY_TO_TEXTS = {
    "spam":        ["spam", "promoting other content", "insults or false information",
                    "promoting illegal content"],
    "violence":    ["violence", "graphic", "disturbing", "extreme", "hate speech",
                    "calling for violence", "organized crime", "terrorism",
                    "animal abuse", "insults or false information"],
    "porn":        ["pornography", "sexual", "non-consensual", "illegal sexual"],
    "child_abuse": ["child abuse", "child sexual", "child physical"],
    "copyright":   ["copyright"],
    "fake":        ["fake", "scam", "fraud", "impersonation", "phishing",
                    "malware", "deceptive", "fraudulent"],
    "personal_info": ["personal data", "private images", "phone number",
                      "address", "stolen data", "credentials"],
    "other":       ["other", "illegal goods", "weapons", "drugs",
                    "counterfeit", "hacking"],
}

MAX_REPORT_ITERATIONS = 5


def _find_best_option(option_key: str, menu_options: list) -> bytes | None:
    """Cocokkan option_key ke teks menu yang diberikan Telegram."""
    keywords = OPTION_KEY_TO_TEXTS.get(option_key, [option_key])
    for opt in menu_options:
        opt_text = opt.text.lower()
        for kw in keywords:
            if kw.lower() in opt_text:
                logger.debug(f"[MATCH] '{opt_text}' → keyword '{kw}'")
                return opt.option
    if menu_options:
        logger.warning(
            f"[MATCH] Tidak ada match untuk '{option_key}', "
            f"pakai opsi pertama: '{menu_options[0].text}'"
        )
        return menu_options[0].option
    return None


# ─────────────────────────────────────────────────────────────
# STEP 1 — SET ONLINE + KEEPALIVE
# ─────────────────────────────────────────────────────────────

async def _set_online(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=False))
        logger.info(f"[ONLINE] ✅ {phone} set online")
    except Exception as e:
        logger.warning(f"[ONLINE] {phone} non-fatal: {e}")


async def _set_offline(client: Client, phone: str) -> None:
    try:
        await client.invoke(functions.account.UpdateStatus(offline=True))
    except Exception:
        pass


async def _online_keepalive(client: Client, phone: str, interval: float = 10.0) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await client.invoke(functions.account.UpdateStatus(offline=False))
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


# ─────────────────────────────────────────────────────────────
# GET ACTIVE CLIENTS
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
        logger.warning(f"[JOIN] ❌ {phone} invite invalid")
        return False
    except ChannelPrivate:
        logger.warning(f"[JOIN] ❌ {phone} channel private")
        return False
    except FloodWait as fw:
        await asyncio.sleep(fw.value)
        try:
            await client.join_chat(peer_str)
            return True
        except Exception:
            return False
    except Exception as e:
        logger.error(f"[JOIN] ❌ {phone}: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# STEP 3+4 — FETCH + READ HISTORY
# ─────────────────────────────────────────────────────────────

async def _fetch_and_read(
    client: Client, peer_str: str, phone: str, limit: int = 100,
) -> list[int]:
    ids = []
    try:
        async for msg in client.get_chat_history(peer_str, limit=limit):
            if msg.id:
                ids.append(msg.id)
        if not ids:
            return []

        logger.info(f"[FETCH] {phone} dapat {len(ids)} IDs")
        peer = await client.resolve_peer(peer_str)
        max_id = max(ids)

        try:
            await client.invoke(
                functions.channels.ReadHistory(channel=peer, max_id=max_id)
            )
        except Exception:
            try:
                await client.invoke(
                    functions.messages.ReadHistory(peer=peer, max_id=max_id)
                )
            except Exception as e:
                logger.warning(f"[READ] {phone} ReadHistory non-fatal: {e}")

        return ids
    except ChannelPrivate:
        logger.warning(f"[FETCH] {phone} channel private")
        return []
    except Exception as e:
        logger.warning(f"[FETCH] {phone}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# STEP 5 — INCREMENT VIEW COUNTER
# ─────────────────────────────────────────────────────────────

async def _increment_views(
    client: Client, peer_str: str, phone: str, message_ids: list[int],
) -> None:
    if not message_ids:
        return
    try:
        peer = await client.resolve_peer(peer_str)
        for i in range(0, len(message_ids), 100):
            batch = message_ids[i:i + 100]
            await client.invoke(
                functions.messages.GetMessagesViews(peer=peer, id=batch, increment=True)
            )
            logger.info(f"[VIEW] ✅ {phone} +{len(batch)} views")
            if i + 100 < len(message_ids):
                await asyncio.sleep(_rnd(1.0, 2.0))
    except Exception as e:
        logger.warning(f"[VIEW] {phone} non-fatal: {e}")


# ─────────────────────────────────────────────────────────────
# STEP 2b — ENSURE STARTED  ← FIX UTAMA /reportbot
#
# BUG LAMA: start_bot() gagal (sudah pernah chat) → fallback
# send_message("/start") SELALU jalan → bot target banjir /start.
#
# FIX BARU: cek history dulu. Jika ada pesan → sudah pernah
# chat → SKIP /start sepenuhnya. Hanya /start jika benar-benar baru.
# ─────────────────────────────────────────────────────────────

async def _ensure_started(client: Client, peer_str: str, phone: str) -> None:
    try:
        has_history = False
        try:
            async for _ in client.get_chat_history(peer_str, limit=1):
                has_history = True
                break
        except Exception:
            has_history = False  # Anggap belum ada, coba /start

        if has_history:
            logger.info(f"[STARTBOT] ✅ {phone} sudah pernah chat → skip /start")
            return

        # Benar-benar belum pernah chat → kirim /start
        try:
            await client.start_bot(peer_str)
            logger.info(f"[STARTBOT] ✅ {phone} /start via start_bot()")
        except Exception as e1:
            logger.warning(f"[STARTBOT] {phone} start_bot() gagal: {e1} → fallback")
            try:
                await client.send_message(peer_str, "/start")
                logger.info(f"[STARTBOT] ✅ {phone} /start via send_message()")
            except Exception as e2:
                logger.warning(f"[STARTBOT] {phone} /start gagal total (non-fatal): {e2}")

        await asyncio.sleep(_rnd(1.5, 3.0))
    except Exception as e:
        logger.warning(f"[STARTBOT] {phone} _ensure_started error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────
# STEP 6a — REPORT PEER  (account.ReportPeer)
#
# Dikonfirmasi dari core.telegram.org Layer 223:
#   account.reportPeer#c5ba3d86
#   peer:InputPeer reason:ReportReason message:string = Bool
# Schema TIDAK BERUBAH dari Layer 158 sampai 223. Selalu valid.
# ─────────────────────────────────────────────────────────────

async def _report_peer(
    client: Client, peer_str: str, option_key: str, comment: str,
) -> bool:
    reason_cls = get_reason_cls(option_key)
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e
    except KeyError as e:
        raise ValueError(f"Peer '{peer_str}' belum di-cache, coba lagi.") from e

    result = await client.invoke(
        functions.account.ReportPeer(
            peer=peer,
            reason=reason_cls(),
            message=comment,
        )
    )
    return bool(result)


# ─────────────────────────────────────────────────────────────
# STEP 6b — REPORT MESSAGES schema BARU  (Layer 189+, pyrofork)
#
# messages.report#fc78af9b
#   peer:InputPeer id:Vector<int> option:bytes message:string = ReportResult
#
# Flow iteratif:
#   1. option=b"" → Telegram beri menu (reportResultChooseOption)
#   2. Pilih option → panggil lagi
#   3. Isi comment jika diminta (reportResultAddComment)
#   4. Selesai (reportResultReported)
# ─────────────────────────────────────────────────────────────

async def _report_messages_new_api(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str,
) -> bool:
    """Dipakai jika NEW_REPORT_API=True (pyrofork / fork Layer 189+)."""
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e
    except KeyError as e:
        raise ValueError(f"Peer '{peer_str}' belum di-cache.") from e

    current_option: bytes = b""
    current_comment: str = ""

    for iteration in range(MAX_REPORT_ITERATIONS):
        try:
            result = await client.invoke(
                functions.messages.Report(
                    peer=peer,
                    id=message_ids,
                    option=current_option,
                    message=current_comment,
                )
            )
        except Exception as e:
            logger.error(f"[REPORT-NEW] messages.Report gagal iter {iteration+1}: {e}")
            raise

        result_type = type(result).__name__

        if result_type == "ReportResultReported":
            logger.info(f"[REPORT-NEW] ✅ Terkirim (iter {iteration+1})")
            return True
        elif result_type == "ReportResultChooseOption":
            opts = result.options
            if not opts:
                return False
            chosen = _find_best_option(option_key, opts)
            if chosen is None:
                return False
            logger.info(f"[REPORT-NEW] Pilih dari menu '{result.title}' (iter {iteration+1})")
            current_option = chosen
        elif result_type == "ReportResultAddComment":
            logger.info(f"[REPORT-NEW] Diminta comment optional={result.optional} (iter {iteration+1})")
            current_option = result.option
            current_comment = comment
        else:
            logger.warning(f"[REPORT-NEW] Result tidak dikenal: {result_type}")
            return False

        await asyncio.sleep(_rnd(0.5, 1.5))

    logger.warning(f"[REPORT-NEW] Max iterasi ({MAX_REPORT_ITERATIONS}) tanpa reportResultReported")
    return False


# ─────────────────────────────────────────────────────────────
# STEP 6b (fallback) — REPORT MESSAGES schema LAMA (Layer 158)
#
# messages.Report#8953AB4E
#   peer:InputPeer id:Vector<int> reason:ReportReason message:string = Bool
#
# Dipakai jika NEW_REPORT_API=False (Pyrogram 2.0.106 / Layer 158).
# Return Bool langsung, tidak ada iterasi.
# ─────────────────────────────────────────────────────────────

async def _report_messages_old_api(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str,
) -> bool:
    """Dipakai jika NEW_REPORT_API=False (Pyrogram 2.0.106)."""
    reason_cls = get_reason_cls(option_key)
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e
    except KeyError as e:
        raise ValueError(f"Peer '{peer_str}' belum di-cache.") from e

    result = await client.invoke(
        functions.messages.Report(
            peer=peer,
            id=message_ids,
            reason=reason_cls(),
            message=comment,
        )
    )
    return bool(result)


async def _report_messages(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str,
) -> bool:
    """Router: pilih schema lama atau baru berdasarkan deteksi layer."""
    if NEW_REPORT_API:
        return await _report_messages_new_api(
            client, peer_str, message_ids, option_key, comment
        )
    else:
        logger.warning(
            "[REPORT] Pyrogram Layer 158 terdeteksi — pakai schema lama (reason). "
            "Upgrade ke pyrofork untuk schema baru."
        )
        return await _report_messages_old_api(
            client, peer_str, message_ids, option_key, comment
        )


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

    logger.info(f"[{label}] Mulai — {len(clients)} sender, repeat={repeat}x, target={peer_str}")

    total_sent = 0
    success_senders = []
    failed_senders = []
    last_effective_ids = seed_message_ids

    for phone, client in clients:
        await _set_online(client, phone)
        keepalive_task = asyncio.create_task(
            _online_keepalive(client, phone, interval=10.0)
        )
        sender_sent = 0

        try:
            # 2a. Ensure started (reportbot) — hanya /start jika belum pernah chat
            if do_start_bot:
                await _ensure_started(client, peer_str, phone)

            # 2b. Join channel
            if do_join:
                await _join(client, peer_str, phone)
                await asyncio.sleep(_rnd(0.5, 1.5))

            # 3+4. Fetch IDs + ReadHistory
            fresh_ids: list[int] = []
            if do_view:
                fresh_ids = await _fetch_and_read(client, peer_str, phone, limit=100)

            if fresh_ids:
                effective_ids = list(dict.fromkeys(fresh_ids + seed_message_ids))[:100]
            else:
                effective_ids = seed_message_ids

            last_effective_ids = effective_ids

            # 5. Increment views
            if do_view and effective_ids:
                await _increment_views(client, peer_str, phone, effective_ids)
                await asyncio.sleep(_rnd(0.5, 1.5))

            # 6. Pilih method report
            # reportbot: selalu account.ReportPeer (IDs tidak relevan untuk user/bot)
            # report/reportv2/reportpriv dengan IDs: messages.report (auto-detect schema)
            # report/reportv2/reportpriv tanpa IDs: fallback account.ReportPeer
            if do_start_bot:
                use_peer_report = True
                method_desc = "account.ReportPeer"
            elif effective_ids:
                use_peer_report = False
                method_desc = (
                    f"messages.report [{'new option flow' if NEW_REPORT_API else 'old reason fallback'}, "
                    f"{len(effective_ids)} IDs]"
                )
            else:
                use_peer_report = True
                method_desc = "account.ReportPeer (fallback - no IDs)"

            logger.info(f"[{label}] {phone} → {method_desc}")

            for i in range(repeat):
                try:
                    if use_peer_report:
                        await _report_peer(client, peer_str, option_key, comment)
                    else:
                        await _report_messages(
                            client, peer_str, effective_ids, option_key, comment
                        )

                    sender_sent += 1
                    total_sent += 1
                    logger.info(f"[{label}] ✅ {phone} {i+1}/{repeat} (total: {total_sent})")

                    if i < repeat - 1:
                        await asyncio.sleep(_rnd(2.0, 6.0))

                except FloodWait as fw:
                    logger.warning(f"[{label}] FloodWait {fw.value}s — {phone} nonaktif.")
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

    if do_start_bot or not last_effective_ids:
        method_note = "📡 account.ReportPeer"
    elif NEW_REPORT_API:
        method_note = f"📨 messages.report new flow ({len(last_effective_ids)} pesan)"
    else:
        method_note = f"📨 messages.report legacy ({len(last_effective_ids)} pesan) ⚠️ upgrade pyrofork"

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
    """/report — user pilih pesan spesifik via link."""
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
    """/reportbot — ensure started 1x (skip jika sudah), lalu account.ReportPeer."""
    return await _run_engine(
        peer_str=peer_str, option_key=option_key, comment=comment,
        admin_user_id=admin_user_id, repeat=repeat,
        seed_message_ids=[],
        do_join=False, do_view=False, do_start_bot=True,
        label="REPORT-BOT",
    )
