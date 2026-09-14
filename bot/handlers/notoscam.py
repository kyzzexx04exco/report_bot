"""
bot/handlers/notoscam.py
/notoscam <target> <deskripsi> — semua sender kirim laporan ke @notoscam.

Cara kerja @notoscam (berdasarkan dokumentasi resmi Telegram):
- @notoscam adalah bot resmi Telegram untuk laporan impersonasi/scam
- Format: tag username target + deskripsi masalah
- Contoh manual: "@scammer123 This account is scamming people selling fake items"
- Bot akan review dan beri label SCAM jika valid

Flow otomatis (sama persis manual):
1. Sender langsung kirim pesan laporan ke @notoscam dengan format: @target [deskripsi]
   (TIDAK perlu /start dulu — @notoscam adalah bot publik yang bisa menerima pesan langsung)
2. Jeda natural antar sender

NOTE: /start TIDAK dikirim karena @notoscam tidak memerlukan itu untuk menerima laporan.
Mengirim /start malah bikin noise dan tidak diperlukan.
"""

import asyncio
import logging
import os
import random
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from utils.report_executor import _get_active_clients, _set_online, _set_offline, _online_keepalive

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
NOTOSCAM_USERNAME = "notoscam"


def _parse_notoscam_input(text: str) -> tuple[str, str] | None:
    """
    Parse input /notoscam.
    Format: /notoscam <target> <deskripsi>
    target bisa: @username, t.me/username, username
    Return (target_mention, deskripsi) atau None jika invalid.
    """
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        return None

    raw_target = parts[1].strip()
    deskripsi = parts[2].strip()

    # Normalisasi target ke @username
    if raw_target.startswith("@"):
        mention = raw_target
    elif "t.me/" in raw_target.lower():
        m = re.search(r"t(?:elegram)?\.me/([A-Za-z0-9_]{3,})", raw_target, re.IGNORECASE)
        mention = f"@{m.group(1)}" if m else raw_target
    else:
        mention = f"@{raw_target}"

    return mention, deskripsi


@router.message(Command("notoscam"))
async def cmd_notoscam(message: Message) -> None:
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parsed = _parse_notoscam_input(message.text)
    if not parsed:
        await message.answer(
            "❌ Format salah.\n\n"
            "Gunakan: <code>/notoscam @target deskripsi masalah</code>\n\n"
            "Contoh:\n"
            "<code>/notoscam @scammer123 This account is impersonating "
            "official crypto exchange and scamming users</code>",
            parse_mode="HTML",
        )
        return

    mention, deskripsi = parsed
    # Format pesan yang dikirim ke @notoscam (sesuai cara manual)
    report_text = f"{mention} {deskripsi}"

    status = await message.answer(
        f"⏳ <b>Mengirim laporan ke @notoscam...</b>\n\n"
        f"🎯 Target: <code>{mention}</code>\n"
        f"📝 Deskripsi: <i>{deskripsi}</i>",
        parse_mode="HTML",
    )

    clients = await _get_active_clients()
    if not clients:
        await status.edit_text("🚫 Tidak ada sender aktif / semua offline.")
        return

    total_sent = 0
    failed = []
    blocked = []

    for phone, client in clients:
        await _set_online(client, phone)
        keepalive_task = asyncio.create_task(_online_keepalive(client, phone, interval=10.0))

        try:
            # Langsung kirim laporan ke @notoscam tanpa /start
            # @notoscam adalah bot publik — tidak perlu /start dulu
            await client.send_message(NOTOSCAM_USERNAME, report_text)
            total_sent += 1
            logger.info(f"[NOTOSCAM] ✅ {phone} kirim laporan ke @notoscam")

            # Jeda natural antar sender
            await asyncio.sleep(random.uniform(3.0, 6.0))

        except Exception as e:
            err_str = str(e).lower()
            logger.error(f"[NOTOSCAM] ❌ {phone} gagal: {e}")

            # Deteksi bot diblokir oleh sender
            if "user_privacy_restricted" in err_str or "bot_blocked" in err_str or "user_blocked" in err_str:
                blocked.append(phone)
            else:
                failed.append(phone)

            await asyncio.sleep(1.0)

        finally:
            keepalive_task.cancel()
            await _set_offline(client, phone)

    if total_sent == 0:
        detail = ""
        if blocked:
            detail += f"\n🚫 Diblok sender: {len(blocked)} akun (unblock @notoscam dulu)"
        await status.edit_text(f"🚫 Semua sender gagal kirim ke @notoscam.{detail}")
        return

    result_text = (
        f"✅ <b>Notoscam Selesai!</b>\n\n"
        f"📊 Terkirim: <b>{total_sent}</b> sender\n"
        f"❌ Gagal: <b>{len(failed)}</b> sender\n"
    )
    if blocked:
        result_text += f"🚫 Diblok: <b>{len(blocked)}</b> sender\n"

    result_text += (
        f"🎯 Target: <code>{mention}</code>\n"
        f"📝 Pesan: <i>{report_text}</i>"
    )

    await status.edit_text(result_text, parse_mode="HTML")
