"""
bot/handlers/report.py
Handler untuk flow /report lengkap:
  Step 1: /report <link_channel>
  Step 2: Minta link pesan spesifik
  Step 3: Parse link pesan → tampilkan keyboard opsi report
  Step 4a: Opsi tanpa komentar → langsung eksekusi
  Step 4b: Opsi dengan komentar → FSM minta teks komentar → eksekusi

FSM States:
  ReportStates.waiting_message_link  → Menunggu link pesan spesifik
  ReportStates.waiting_comment       → Menunggu komentar dari admin
"""

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.report_kb import get_cancel_keyboard, get_report_keyboard
from utils.parsers import is_valid_telegram_link, parse_message_link
from utils.report_executor import (
    NO_COMMENT_OPTIONS,
    REQUIRES_COMMENT_OPTIONS,
    run_report_with_rotation,
)

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Label cantik untuk setiap opsi (untuk ditampilkan di notifikasi)
OPTION_LABELS = {
    "spam":          "🔞 Spam",
    "porn":          "🔞 Pornografi",
    "violence":      "💥 Kekerasan",
    "child_abuse":   "👶 Eksploitasi Anak",
    "harassment":    "🤬 Pelecehan / Ujaran Kebencian",
    "fake":          "👤 Akun Palsu / Impersonasi",
    "copyright":     "📄 Hak Cipta / Pelanggaran HKI",
    "fraud":         "💰 Penipuan / Barang & Jasa Palsu",
    "personal_info": "🛡️ Informasi Pribadi",
    "other":         "❓ Lainnya",
}


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class ReportStates(StatesGroup):
    waiting_message_link = State()   # Menunggu link pesan spesifik
    waiting_comment = State()        # Menunggu komentar dari admin


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ─────────────────────────────────────────────────────────────
# STEP 1: /report <link_channel>
# ─────────────────────────────────────────────────────────────

@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/report t.me/nama_channel</code>\n\n"
            "Contoh:\n"
            "  <code>/report t.me/somechannel</code>",
            parse_mode="HTML",
        )
        return

    channel_link = parts[1].strip()

    if not is_valid_telegram_link(channel_link):
        await message.answer(
            "❌ Link tidak valid. Gunakan format:\n"
            "  <code>t.me/nama_channel</code>\n"
            "  <code>https://t.me/nama_channel</code>",
            parse_mode="HTML",
        )
        return

    # Simpan link channel ke FSM state
    await state.update_data(channel_link=channel_link)
    await state.set_state(ReportStates.waiting_message_link)

    await message.answer(
        f"📌 Target channel: <code>{channel_link}</code>\n\n"
        "Sekarang kirimkan <b>link pesan spesifik</b> yang ingin dilaporkan.\n\n"
        "Contoh: <code>t.me/somechannel/123</code>\n\n"
        "Atau ketik /cancel untuk membatalkan.",
        parse_mode="HTML",
    )
    logger.info(
        f"[REPORT] Owner {message.from_user.id} memulai report untuk: {channel_link}"
    )


# ─────────────────────────────────────────────────────────────
# STEP 2: Terima link pesan spesifik
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_message_link)
async def handle_message_link(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    # Handle /cancel
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Proses report dibatalkan.")
        return

    msg_link = message.text.strip()
    parsed = parse_message_link(msg_link)

    if not parsed:
        await message.answer(
            "❌ Link pesan tidak valid atau tidak mengandung ID pesan.\n\n"
            "Pastikan format link benar, contoh:\n"
            "  <code>t.me/somechannel/123</code>\n"
            "  <code>https://t.me/c/1234567890/123</code>\n\n"
            "Coba lagi atau ketik /cancel untuk membatalkan.",
            parse_mode="HTML",
        )
        return

    peer_str, message_id = parsed

    # Simpan data ke FSM state
    await state.update_data(
        peer_str=peer_str,
        message_ids=[message_id],
        message_link=msg_link,
    )

    # Tampilkan keyboard opsi report
    await message.answer(
        f"🎯 <b>Target Laporan</b>\n"
        f"📌 Peer: <code>{peer_str}</code>\n"
        f"📝 Pesan ID: <code>{message_id}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=get_report_keyboard(),
    )
    logger.info(
        f"[REPORT] Peer={peer_str}, msg_id={message_id}. Menunggu pilihan opsi."
    )


# ─────────────────────────────────────────────────────────────
# STEP 3a: Callback opsi yang TIDAK butuh komentar → Langsung eksekusi
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_"))
async def handle_report_option(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Anda tidak berhak.", show_alert=True)
        return

    callback_data = callback.data  # contoh: "report_spam"

    # Tangani tombol Batal
    if callback_data == "report_cancel":
        await state.clear()
        await callback.message.edit_text("❌ Proses report dibatalkan.")
        await callback.answer()
        return

    # Ekstrak option key dari callback_data (hapus prefix "report_")
    option = callback_data.replace("report_", "", 1)

    # Ambil data dari FSM
    data = await state.get_data()
    peer_str = data.get("peer_str")
    message_ids = data.get("message_ids")

    if not peer_str or not message_ids:
        await callback.answer(
            "⚠️ Data laporan tidak ditemukan. Mulai ulang dengan /report.",
            show_alert=True,
        )
        await state.clear()
        return

    option_label = OPTION_LABELS.get(option, option)

    # ── Opsi yang TIDAK butuh komentar → Langsung eksekusi ──
    if option in NO_COMMENT_OPTIONS:
        await callback.message.edit_text(
            f"⏳ Memproses laporan...\n"
            f"📋 Kategori: {option_label}",
            parse_mode="HTML",
        )
        await callback.answer()

        result = await run_report_with_rotation(
            peer_str=peer_str,
            message_ids=message_ids,
            option=option,
            comment="",
            admin_user_id=callback.from_user.id,
        )

        await callback.message.edit_text(
            result["message"],
            parse_mode="HTML",
        )
        await state.clear()
        logger.info(
            f"[REPORT] Option={option}, success={result['success']}, "
            f"sender={result['phone']}"
        )

    # ── Opsi yang WAJIB komentar → Aktifkan FSM tunggu input ──
    elif option in REQUIRES_COMMENT_OPTIONS:
        # Simpan option yang dipilih ke FSM state
        await state.update_data(selected_option=option, option_label=option_label)
        await state.set_state(ReportStates.waiting_comment)

        await callback.message.edit_text(
            "✏️ <b>Tambah Komentar</b>\n\n"
            "Mohon bantu kami dengan memaparkan keluhan Anda "
            "atas pesan yang dilaporkan.\n\n"
            "Kirimkan teks komentar Anda:",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )
        await callback.answer()
        logger.info(
            f"[REPORT] Option={option} membutuhkan komentar. Menunggu input."
        )

    else:
        await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# STEP 3b: Terima komentar → Eksekusi report
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_comment)
async def handle_comment(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    # Handle /cancel
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Proses report dibatalkan.")
        return

    comment = message.text.strip()

    if not comment:
        await message.answer(
            "❌ Komentar tidak boleh kosong. Kirim teks komentar Anda, "
            "atau ketik /cancel untuk membatalkan."
        )
        return

    # Ambil semua data dari FSM
    data = await state.get_data()
    peer_str = data.get("peer_str")
    message_ids = data.get("message_ids")
    option = data.get("selected_option")
    option_label = data.get("option_label", option)

    if not peer_str or not message_ids or not option:
        await state.clear()
        await message.answer(
            "⚠️ Data laporan tidak ditemukan. Mulai ulang dengan /report."
        )
        return

    status_msg = await message.answer(
        f"⏳ Memproses laporan...\n"
        f"📋 Kategori: {option_label}\n"
        f"💬 Komentar: <i>{comment}</i>",
        parse_mode="HTML",
    )

    result = await run_report_with_rotation(
        peer_str=peer_str,
        message_ids=message_ids,
        option=option,
        comment=comment,
        admin_user_id=message.from_user.id,
    )

    await status_msg.edit_text(
        result["message"],
        parse_mode="HTML",
    )
    await state.clear()

    logger.info(
        f"[REPORT] Option={option}, comment='{comment}', "
        f"success={result['success']}, sender={result['phone']}"
    )
