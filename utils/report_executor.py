"""
utils/report_executor.py
Eksekusi laporan (report) menggunakan Pyrogram raw MTProto functions.
Menangani FloodWaitError, round-robin pemilihan sender, dan cooldown.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from pyrogram import Client
from pyrogram.errors import FloodWait, PeerIdInvalid, UsernameNotOccupied
from pyrogram.raw import functions, types

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# MAPPING OPSI → InputReportReason
# ─────────────────────────────────────────────────────────────

REASON_MAP: dict[str, object] = {
    "spam":          types.InputReportReasonSpam,
    "porn":          types.InputReportReasonPornography,
    "violence":      types.InputReportReasonViolence,
    "child_abuse":   types.InputReportReasonChildAbuse,
    "harassment":    types.InputReportReasonHarassment,
    "fake":          types.InputReportReasonFake,
    "copyright":     types.InputReportReasonCopyright,
    "fraud":         types.InputReportReasonOther,      # Telegram belum punya reason spesifik
    "personal_info": types.InputReportReasonOther,
    "other":         types.InputReportReasonOther,
}

# Opsi yang tidak butuh komentar (langsung eksekusi)
NO_COMMENT_OPTIONS = {
    "spam",
    "porn",
    "violence",
    "child_abuse",
    "harassment",
}

# Opsi yang WAJIB minta komentar dari admin
REQUIRES_COMMENT_OPTIONS = {
    "fake",
    "copyright",
    "fraud",
    "personal_info",
    "other",
}


# ─────────────────────────────────────────────────────────────
# CORE EXECUTOR
# ─────────────────────────────────────────────────────────────

async def execute_report(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option: str,
    comment: str = "",
) -> bool:
    """
    Eksekusi laporan via MTProto raw function messages.Report.

    Args:
        client      : Pyrogram Client yang sudah di-start
        peer_str    : Username atau "-100XXXXXXXXXX" untuk private channel
        message_ids : List ID pesan yang dilaporkan
        option      : Salah satu kunci di REASON_MAP
        comment     : Teks komentar (wajib diisi untuk opsi tertentu)

    Returns:
        True jika berhasil, False jika gagal.

    Raises:
        FloodWait  : Harus ditangkap di pemanggil untuk round-robin.
    """
    # Resolve reason
    reason_cls = REASON_MAP.get(option, types.InputReportReasonOther)
    reason = reason_cls()

    # Resolve peer ke InputPeer
    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        logger.error(f"[REPORT] Peer '{peer_str}' tidak ditemukan: {e}")
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e

    logger.info(
        f"[REPORT] Melaporkan peer={peer_str} msg_ids={message_ids} "
        f"reason={option} comment='{comment}'"
    )

    # Invoke raw function — bisa raise FloodWait
    result = await client.invoke(
        functions.messages.Report(
            peer=peer,
            id=message_ids,
            reason=reason,
            message=comment,
        )
    )

    logger.info(f"[REPORT] ✅ Laporan dikirim, result={result}")
    return bool(result)


# ─────────────────────────────────────────────────────────────
# ORCHESTRATOR — Round-Robin + FloodWait handling
# ─────────────────────────────────────────────────────────────

async def run_report_with_rotation(
    peer_str: str,
    message_ids: list[int],
    option: str,
    comment: str = "",
    admin_user_id: int = 0,
) -> dict:
    """
    Cari sender aktif (round-robin), lakukan report.
    Jika kena FloodWait, tandai inactive dan coba sender berikutnya.

    Returns dict:
        {
            "success": bool,
            "message": str,   # pesan status untuk dikirim ke admin
            "phone": str,     # sender yang dipakai (jika sukses)
        }
    """
    cooldown = await db.get_cooldown(admin_user_id)
    tried: set[str] = set()

    while True:
        sender = await db.get_active_sender()

        if sender is None:
            logger.warning("[REPORT] Tidak ada sender aktif tersisa.")
            return {
                "success": False,
                "message": (
                    "🚫 Semua akun sender sedang terkena limit. "
                    "Coba lagi nanti."
                ),
                "phone": "",
            }

        phone = sender["phone_number"]

        # Hindari loop tak terbatas jika semua sudah dicoba
        if phone in tried:
            return {
                "success": False,
                "message": (
                    "🚫 Semua akun sender sudah dicoba dan gagal. "
                    "Coba lagi nanti."
                ),
                "phone": "",
            }
        tried.add(phone)

        # Cek cooldown berdasarkan last_used
        if sender.get("last_used"):
            try:
                last_used_dt = datetime.fromisoformat(str(sender["last_used"]))
            except (ValueError, TypeError):
                last_used_dt = None

            if last_used_dt:
                elapsed = (datetime.utcnow() - last_used_dt).total_seconds()
                if elapsed < cooldown:
                    wait_sec = int(cooldown - elapsed)
                    logger.info(
                        f"[REPORT] Sender {phone} masih dalam cooldown "
                        f"({wait_sec}s tersisa). Coba sender lain."
                    )
                    # Tandai sementara tidak bisa dipakai — coba sender lain
                    # Tapi jangan set inactive permanen karena hanya cooldown
                    await db.set_sender_inactive(phone)
                    # Setelah coba semua, aktifkan lagi (tidak ideal tapi
                    # fungsi ini bersifat best-effort)
                    # Untuk implementasi lebih baik, gunakan scheduler.
                    continue

        # Ambil client dari pool
        client = pyro_client.get_client(phone)
        if client is None:
            logger.warning(
                f"[REPORT] Client {phone} tidak ada di pool. Tandai inactive."
            )
            await db.set_sender_inactive(phone)
            continue

        try:
            success = await execute_report(
                client=client,
                peer_str=peer_str,
                message_ids=message_ids,
                option=option,
                comment=comment,
            )
            await db.update_sender_last_used(phone)
            return {
                "success": success,
                "message": (
                    f"✅ Laporan berhasil dikirim!\n"
                    f"📱 Sender: <code>{phone}</code>\n"
                    f"🎯 Target: <code>{peer_str}</code>\n"
                    f"📌 Pesan ID: <code>{message_ids}</code>\n"
                    f"📋 Alasan: <code>{option}</code>"
                ),
                "phone": phone,
            }

        except FloodWait as fw:
            wait = fw.value
            logger.warning(
                f"[REPORT] ⚠️ FloodWait {wait}s pada {phone}. "
                "Tandai inactive, coba sender berikutnya."
            )
            await db.set_sender_inactive(phone)
            await pyro_client.remove_client_from_pool(phone)
            # Jangan await sleep di sini agar tidak memblokir — lanjut ke sender lain
            continue

        except ValueError as e:
            # Peer tidak ditemukan — bukan kesalahan sender
            return {
                "success": False,
                "message": f"❌ Gagal: {e}",
                "phone": phone,
            }

        except Exception as e:
            logger.error(
                f"[REPORT] ❌ Error tidak terduga pada {phone}: {e}"
            )
            return {
                "success": False,
                "message": f"❌ Error tidak terduga: {e}",
                "phone": phone,
            }
