"""
bot/handlers/start.py
Handler untuk command /start dan /help.
Hanya bisa diakses oleh OWNER_ID.
"""

import logging
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


def is_owner(user_id: int) -> bool:
    """Cek apakah user adalah owner yang berhak menggunakan bot."""
    return user_id == OWNER_ID


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    text = (
        "👋 <b>Selamat datang di Report Bot!</b>\n\n"
        "Bot ini adalah <b>remote control</b> untuk melaporkan konten "
        "di Telegram menggunakan akun-akun user yang sudah ditambahkan.\n\n"
        "<b>📋 Daftar Command:</b>\n\n"
        "🔧 <b>Manajemen Sender</b>\n"
        "  /addsender +628xxx — Tambah akun sender baru\n"
        "  /listsender — Tampilkan semua sender\n"
        "  /delsender +628xxx — Hapus sender\n"
        "  /activatesender +628xxx — Aktifkan sender yang nonaktif\n\n"
        "🚨 <b>Report</b>\n"
        "  /report t.me/channel — Mulai proses report\n\n"
        "⚙️ <b>Pengaturan</b>\n"
        "  /settreport 20 — Set jumlah laporan per /report (loop otomatis)\n"
        "  /setcdrep 60 — Set cooldown antar sender (detik)\n\n"
        "ℹ️ Ketik /help untuk panduan lengkap."
    )
    await message.answer(text, parse_mode="HTML")
    logger.info(f"[START] Owner {message.from_user.id} membuka bot.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    text = (
        "📖 <b>Panduan Penggunaan Report Bot</b>\n\n"

        "<b>1. Tambah Akun Sender</b>\n"
        "   Gunakan <code>/addsender +6281234567890</code>\n"
        "   Bot akan mengirim OTP ke nomor tersebut.\n"
        "   Kirim kode OTP yang diterima untuk menyelesaikan login.\n\n"

        "<b>2. Set Jumlah Laporan (Penting!)</b>\n"
        "   Gunakan <code>/settreport 20</code>\n"
        "   Setiap kali pakai /report, bot otomatis mengirim 20 laporan\n"
        "   secara rotasi ke semua sender aktif — tanpa perlu ulang /report!\n"
        "   Default: 1x\n\n"

        "<b>3. Melakukan Report</b>\n"
        "   Gunakan <code>/report t.me/target_channel</code>\n"
        "   Bot akan memandu langkah demi langkah:\n"
        "   • Kirim link channel/grup target\n"
        "   • Kirim link pesan spesifik yang ingin dilaporkan\n"
        "   • Pilih kategori laporan dari menu\n"
        "   • Isi komentar (jika diperlukan)\n"
        "   • Bot otomatis loop sesuai /settreport\n\n"

        "<b>4. Set Cooldown</b>\n"
        "   Gunakan <code>/setcdrep 60</code>\n"
        "   Atur jeda (detik) antar penggunaan satu sender.\n"
        "   Default: 60 detik\n\n"

        "<b>⚠️ Catatan Penting:</b>\n"
        "   • Akun sender yang kena FloodWait dinonaktifkan sementara.\n"
        "   • Bot otomatis beralih ke sender lain yang tersedia.\n"
        "   • Pastikan ada minimal 1 sender aktif sebelum melakukan report.\n"
        "   • Gunakan /activatesender untuk mengaktifkan kembali sender."
    )
    await message.answer(text, parse_mode="HTML")
