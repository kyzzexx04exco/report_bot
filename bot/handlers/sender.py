"""
bot/handlers/sender.py
Handler untuk /addsender — login akun Telegram via MTProto (Pyrogram),
tangani OTP, simpan session_string ke database.

FSM States:
  AddSenderStates.waiting_otp   → Menunggu kode OTP dari admin
  AddSenderStates.waiting_2fa   → Menunggu password 2FA (jika ada)
"""

import logging
import os
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from pyrogram import Client
from pyrogram.errors import (
    BadRequest,
    FloodWait,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)

from database import db
from utils import pyro_client
from utils.parsers import normalize_phone

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Penyimpanan sementara client yang sedang proses login
# { user_id (admin): { "client": Client, "phone": str, "phone_code_hash": str } }
_pending_logins: dict[int, dict] = {}


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class AddSenderStates(StatesGroup):
    waiting_otp = State()
    waiting_2fa = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def _cleanup_pending(admin_id: int) -> None:
    """Hentikan dan hapus client pending login."""
    pending = _pending_logins.pop(admin_id, None)
    if pending and pending.get("client"):
        try:
            await pending["client"].stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# /addsender
# ─────────────────────────────────────────────────────────────

@router.message(Command("addsender"))
async def cmd_addsender(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/addsender +6281234567890</code>",
            parse_mode="HTML",
        )
        return

    phone_raw = parts[1].strip()
    phone = normalize_phone(phone_raw)

    # Cek apakah nomor sudah terdaftar
    existing = await db.get_sender_by_phone(phone)
    if existing:
        await message.answer(
            f"⚠️ Nomor <code>{phone}</code> sudah terdaftar sebagai sender.\n"
            "Gunakan /listsender untuk melihat daftar.",
            parse_mode="HTML",
        )
        return

    # Buat client sementara untuk login
    client = pyro_client.create_temp_client(phone)

    status_msg = await message.answer(
        f"⏳ Menghubungkan ke Telegram untuk <code>{phone}</code>...",
        parse_mode="HTML",
    )

    try:
        await client.connect()
        sent = await client.send_code(phone)
        phone_code_hash = sent.phone_code_hash

        # Simpan state pending login
        _pending_logins[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": phone_code_hash,
        }

        await state.set_state(AddSenderStates.waiting_otp)
        await status_msg.edit_text(
            f"📲 Kode OTP telah dikirim ke <code>{phone}</code>.\n\n"
            "Kirim kodenya di sini (contoh: <code>12345</code>) "
            "atau ketik /cancel untuk membatalkan.",
            parse_mode="HTML",
        )
        logger.info(f"[ADDSENDER] OTP dikirim ke {phone}.")

    except PhoneNumberInvalid:
        await status_msg.edit_text(
            f"❌ Nomor <code>{phone}</code> tidak valid.\n"
            "Pastikan format nomor benar (contoh: +6281234567890).",
            parse_mode="HTML",
        )
        try:
            await client.disconnect()
        except Exception:
            pass

    except FloodWait as fw:
        await status_msg.edit_text(
            f"⏳ Terlalu banyak permintaan. Coba lagi dalam {fw.value} detik."
        )
        try:
            await client.disconnect()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[ADDSENDER] Error saat send_code ke {phone}: {e}")
        await status_msg.edit_text(
            f"❌ Terjadi kesalahan: <code>{e}</code>",
            parse_mode="HTML",
        )
        try:
            await client.disconnect()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Terima OTP
# ─────────────────────────────────────────────────────────────

@router.message(AddSenderStates.waiting_otp)
async def handle_otp(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    # Tangani /cancel
    if message.text and message.text.strip().lower() == "/cancel":
        await _cleanup_pending(message.from_user.id)
        await state.clear()
        await message.answer("❌ Proses penambahan sender dibatalkan.")
        return

    otp_raw = message.text.strip().replace(" ", "")  # "1 2 3 4 5" → "12345"
    pending = _pending_logins.get(message.from_user.id)

    if not pending:
        await state.clear()
        await message.answer(
            "⚠️ Sesi login sudah kedaluwarsa. Mulai ulang dengan /addsender."
        )
        return

    client: Client = pending["client"]
    phone: str = pending["phone"]
    phone_code_hash: str = pending["phone_code_hash"]

    try:
        await client.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=otp_raw,
        )

        # Login berhasil — ekspor session
        session_string = await client.export_session_string()
        await client.stop()

        # Simpan ke database
        added = await db.add_sender(phone, session_string)

        if added:
            # Load ke pool agar langsung bisa dipakai
            new_client = pyro_client.create_client_from_session(phone, session_string)
            await new_client.start()
            await pyro_client.add_client_to_pool(phone, new_client)

            await message.answer(
                f"✅ Akun <code>{phone}</code> berhasil ditambahkan sebagai sender!",
                parse_mode="HTML",
            )
            logger.info(f"[ADDSENDER] ✅ {phone} berhasil ditambahkan.")
        else:
            await message.answer(
                f"⚠️ Nomor <code>{phone}</code> sudah ada di database "
                "(mungkin ditambahkan oleh proses lain).",
                parse_mode="HTML",
            )

        _pending_logins.pop(message.from_user.id, None)
        await state.clear()

    except SessionPasswordNeeded:
        # Akun punya 2FA
        await state.set_state(AddSenderStates.waiting_2fa)
        await message.answer(
            "🔐 Akun ini menggunakan <b>verifikasi 2 langkah (2FA)</b>.\n\n"
            "Masukkan password 2FA Anda, atau ketik /cancel untuk membatalkan.",
            parse_mode="HTML",
        )

    except PhoneCodeInvalid:
        await message.answer(
            "❌ Kode OTP salah. Coba lagi, atau ketik /cancel untuk membatalkan."
        )

    except PhoneCodeExpired:
        await _cleanup_pending(message.from_user.id)
        await state.clear()
        await message.answer(
            "⏰ Kode OTP sudah kedaluwarsa.\n"
            "Mulai ulang dengan /addsender."
        )

    except Exception as e:
        logger.error(f"[ADDSENDER] Error saat sign_in {phone}: {e}")
        await _cleanup_pending(message.from_user.id)
        await state.clear()
        await message.answer(
            f"❌ Terjadi kesalahan: <code>{e}</code>\n"
            "Mulai ulang dengan /addsender.",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────
# Terima Password 2FA
# ─────────────────────────────────────────────────────────────

@router.message(AddSenderStates.waiting_2fa)
async def handle_2fa(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await _cleanup_pending(message.from_user.id)
        await state.clear()
        await message.answer("❌ Proses penambahan sender dibatalkan.")
        return

    password = message.text.strip()
    pending = _pending_logins.get(message.from_user.id)

    if not pending:
        await state.clear()
        await message.answer(
            "⚠️ Sesi login sudah kedaluwarsa. Mulai ulang dengan /addsender."
        )
        return

    client: Client = pending["client"]
    phone: str = pending["phone"]

    try:
        await client.check_password(password)

        session_string = await client.export_session_string()
        await client.stop()

        added = await db.add_sender(phone, session_string)

        if added:
            new_client = pyro_client.create_client_from_session(phone, session_string)
            await new_client.start()
            await pyro_client.add_client_to_pool(phone, new_client)

            await message.answer(
                f"✅ Akun <code>{phone}</code> berhasil ditambahkan sebagai sender!",
                parse_mode="HTML",
            )
            logger.info(f"[ADDSENDER] ✅ {phone} (2FA) berhasil ditambahkan.")
        else:
            await message.answer(
                f"⚠️ Nomor <code>{phone}</code> sudah ada di database.",
                parse_mode="HTML",
            )

        _pending_logins.pop(message.from_user.id, None)
        await state.clear()

    except BadRequest as e:
        await message.answer(
            f"❌ Password 2FA salah: <code>{e}</code>\n"
            "Coba lagi, atau ketik /cancel.",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"[ADDSENDER] Error saat check_password {phone}: {e}")
        await _cleanup_pending(message.from_user.id)
        await state.clear()
        await message.answer(
            f"❌ Terjadi kesalahan: <code>{e}</code>\n"
            "Mulai ulang dengan /addsender.",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────
# /cancel (global cancel untuk FSM)
# ─────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ Tidak ada proses yang berjalan untuk dibatalkan.")
        return

    await _cleanup_pending(message.from_user.id)
    await state.clear()
    await message.answer("❌ Proses dibatalkan.")
    logger.info(f"[CANCEL] Owner {message.from_user.id} membatalkan state: {current_state}.")
