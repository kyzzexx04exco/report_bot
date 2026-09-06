"""
bot/handlers/settings.py
Handler untuk:
  /setcdrep <detik>  — Set cooldown antar penggunaan sender
  /listsender        — Tampilkan semua sender beserta statusnya
  /delsender <phone> — Hapus sender dari database
"""

import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def _format_time_ago(last_used_raw) -> str:
    """
    Format last_used timestamp menjadi string 'X menit lalu' / 'X jam lalu' / dll.
    """
    if last_used_raw is None:
        return "Belum pernah digunakan"

    try:
        if isinstance(last_used_raw, datetime):
            last_used = last_used_raw
        else:
            last_used = datetime.fromisoformat(str(last_used_raw))

        # Pastikan timezone-aware
        if last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=timezone.utc)

        now = datetime.now(tz=timezone.utc)
        diff = now - last_used
        total_seconds = int(diff.total_seconds())

        if total_seconds < 60:
            return f"{total_seconds} detik lalu"
        elif total_seconds < 3600:
            return f"{total_seconds // 60} menit lalu"
        elif total_seconds < 86400:
            return f"{total_seconds // 3600} jam lalu"
        else:
            return f"{total_seconds // 86400} hari lalu"

    except (ValueError, TypeError):
        return "Waktu tidak diketahui"


# ─────────────────────────────────────────────────────────────
# /setcdrep <detik>
# ─────────────────────────────────────────────────────────────

@router.message(Command("setcdrep"))
async def cmd_setcdrep(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        # Tampilkan nilai sekarang
        current = await db.get_cooldown(message.from_user.id)
        await message.answer(
            f"⚙️ <b>Cooldown Sender</b>\n\n"
            f"Nilai saat ini: <b>{current} detik</b>\n\n"
            f"Untuk mengubah: <code>/setcdrep 60</code>",
            parse_mode="HTML",
        )
        return

    try:
        seconds = int(parts[1].strip())
    except ValueError:
        await message.answer(
            "❌ Nilai harus berupa angka (detik).\n"
            "Contoh: <code>/setcdrep 60</code>",
            parse_mode="HTML",
        )
        return

    if seconds < 0:
        await message.answer("❌ Cooldown tidak boleh negatif.")
        return

    if seconds > 86400:
        await message.answer(
            "❌ Cooldown terlalu besar. Maksimal 86400 detik (24 jam)."
        )
        return

    await db.set_cooldown(message.from_user.id, seconds)
    logger.info(
        f"[SETTINGS] Owner {message.from_user.id} set cooldown ke {seconds}s."
    )

    await message.answer(
        f"✅ Cooldown berhasil diset ke <b>{seconds} detik</b>.\n\n"
        "Setiap sender akan menunggu minimal "
        f"<b>{seconds} detik</b> sebelum digunakan lagi.",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────
# /listsender
# ─────────────────────────────────────────────────────────────

@router.message(Command("listsender"))
async def cmd_listsender(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    senders = await db.get_all_senders()

    if not senders:
        await message.answer(
            "📋 <b>Daftar Sender</b>\n\n"
            "Belum ada sender yang ditambahkan.\n"
            "Gunakan /addsender untuk menambah sender.",
            parse_mode="HTML",
        )
        return

    lines = ["📋 <b>Daftar Sender:</b>\n"]

    for i, sender in enumerate(senders, start=1):
        phone = sender["phone_number"]
        is_active = bool(sender["is_active"])
        last_used = sender.get("last_used")
        created_at = sender.get("created_at")

        # Status dan emoji
        if is_active:
            # Cek apakah client-nya ada di pool
            client = pyro_client.get_client(phone)
            if client:
                status_icon = "🟢"
                status_text = "Active"
            else:
                status_icon = "🟡"
                status_text = "Active (offline pool)"
        else:
            status_icon = "🔴"
            status_text = "Inactive (Flood/Banned)"

        time_ago = _format_time_ago(last_used)

        line = (
            f"{i}. <code>{phone}</code>\n"
            f"   {status_icon} {status_text}\n"
            f"   🕒 Last used: {time_ago}"
        )
        lines.append(line)

    # Statistik ringkas
    active_count = sum(1 for s in senders if s["is_active"])
    total_count = len(senders)
    lines.append(
        f"\n📊 Total: <b>{total_count}</b> sender "
        f"(<b>{active_count}</b> aktif, "
        f"<b>{total_count - active_count}</b> inactive)"
    )

    await message.answer("\n".join(lines), parse_mode="HTML")
    logger.info(f"[SETTINGS] /listsender dipanggil oleh owner {message.from_user.id}.")


# ─────────────────────────────────────────────────────────────
# /delsender <phone>
# ─────────────────────────────────────────────────────────────

@router.message(Command("delsender"))
async def cmd_delsender(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/delsender +6281234567890</code>",
            parse_mode="HTML",
        )
        return

    phone = parts[1].strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    # Hapus dari pool dulu
    await pyro_client.remove_client_from_pool(phone)

    # Hapus dari database
    deleted = await db.delete_sender(phone)

    if deleted:
        await message.answer(
            f"✅ Sender <code>{phone}</code> berhasil dihapus.",
            parse_mode="HTML",
        )
        logger.info(f"[SETTINGS] Sender {phone} dihapus oleh owner.")
    else:
        await message.answer(
            f"❌ Sender <code>{phone}</code> tidak ditemukan.",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────
# /activatesender <phone> — Aktifkan kembali sender yang nonaktif
# ─────────────────────────────────────────────────────────────

@router.message(Command("activatesender"))
async def cmd_activatesender(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/activatesender +6281234567890</code>",
            parse_mode="HTML",
        )
        return

    phone = parts[1].strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    sender = await db.get_sender_by_phone(phone)
    if not sender:
        await message.answer(
            f"❌ Sender <code>{phone}</code> tidak ditemukan.",
            parse_mode="HTML",
        )
        return

    # Aktifkan di database
    await db.set_sender_active(phone)

    # Coba reload ke pool jika belum ada
    client = pyro_client.get_client(phone)
    if not client:
        try:
            new_client = pyro_client.create_client_from_session(
                phone, sender["session_string"]
            )
            await new_client.start()
            await pyro_client.add_client_to_pool(phone, new_client)
            pool_status = "dan berhasil diload ke pool"
        except Exception as e:
            pool_status = f"tapi gagal diload ke pool: {e}"
    else:
        pool_status = "sudah ada di pool"

    await message.answer(
        f"✅ Sender <code>{phone}</code> diaktifkan kembali — {pool_status}.",
        parse_mode="HTML",
    )
    logger.info(f"[SETTINGS] Sender {phone} diaktifkan kembali oleh owner.")
