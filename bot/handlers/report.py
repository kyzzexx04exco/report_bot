"""
bot/handlers/report.py

Flow:
  /report t.me/channel
    → kirim link pesan
    → pilih opsi (keyboard dari REPORT_OPTIONS)
    → kalau needs_comment: bot minta komentar
    → invoke messages.Report N kali (sesuai settreport), rotasi sender
"""

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.report_kb import get_cancel_keyboard, get_report_keyboard
from database import db
from utils.parsers import is_valid_telegram_link, parse_message_link
from utils.report_executor import REPORT_OPTIONS, get_option_meta, run_report_with_rotation

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Lookup cepat: key → label
_LABEL = {key: label for label, key, _, _ in REPORT_OPTIONS}


class ReportStates(StatesGroup):
    waiting_message_link = State()
    waiting_comment      = State()


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ─────────────────────────────────────────────────────────────
# STEP 1: /report <link>
# ─────────────────────────────────────────────────────────────

@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\nGunakan: <code>/report t.me/nama_channel</code>",
            parse_mode="HTML",
        )
        return

    channel_link = parts[1].strip()
    if not is_valid_telegram_link(channel_link):
        await message.answer(
            "❌ Link tidak valid.\n"
            "Contoh: <code>t.me/nama_channel</code>",
            parse_mode="HTML",
        )
        return

    repeat = await db.get_report_count(message.from_user.id)
    await state.update_data(
        channel_link=channel_link,
        admin_user_id=message.from_user.id,
    )
    await state.set_state(ReportStates.waiting_message_link)

    await message.answer(
        f"📌 Channel: <code>{channel_link}</code>\n"
        f"🔁 Report count: <b>{repeat}x</b>\n\n"
        "Kirim link pesan yang ingin dilaporkan.\n"
        "Contoh: <code>t.me/somechannel/123</code>\n\n"
        "/cancel untuk batal.",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────
# STEP 2: Terima link pesan → tampilkan keyboard opsi
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_message_link)
async def handle_message_link(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Dibatalkan.")
        return

    parsed = parse_message_link(message.text.strip())
    if not parsed:
        await message.answer(
            "❌ Link tidak valid atau tidak ada message ID.\n"
            "Contoh: <code>t.me/somechannel/123</code>\n\n"
            "Coba lagi atau /cancel.",
            parse_mode="HTML",
        )
        return

    peer_str, message_id = parsed
    repeat = await db.get_report_count(message.from_user.id)

    await state.update_data(
        peer_str=peer_str,
        message_ids=[message_id],
    )

    await message.answer(
        f"🎯 Target: <code>{peer_str}</code>  |  msg <code>{message_id}</code>\n"
        f"🔁 Akan dikirim <b>{repeat}x</b>\n\n"
        "Pilih alasan laporan:",
        parse_mode="HTML",
        reply_markup=get_report_keyboard(),
    )


# ─────────────────────────────────────────────────────────────
# STEP 3: User pilih opsi dari keyboard
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ropt_"))
async def handle_option(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    key = callback.data[len("ropt_"):]

    if key == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Dibatalkan.")
        await callback.answer()
        return

    data = await state.get_data()
    peer_str    = data.get("peer_str")
    message_ids = data.get("message_ids")
    admin_id    = data.get("admin_user_id", callback.from_user.id)

    if not peer_str or not message_ids:
        await callback.answer("⚠️ Sesi habis, mulai ulang dengan /report.", show_alert=True)
        await state.clear()
        return

    await callback.answer()

    _, needs_comment = get_option_meta(key)
    label = _LABEL.get(key, key)

    if not needs_comment:
        # ── Langsung kirim tanpa komentar ──
        repeat = await db.get_report_count(admin_id)
        await callback.message.edit_text(
            f"⏳ Mengirim laporan...\n"
            f"Alasan: <b>{label}</b>  |  Count: <b>{repeat}x</b>",
            parse_mode="HTML",
        )

        result = await run_report_with_rotation(
            peer_str=peer_str,
            message_ids=message_ids,
            option_key=key,
            comment="",
            admin_user_id=admin_id,
            repeat=repeat,
        )
        await callback.message.edit_text(result["message"], parse_mode="HTML")
        await state.clear()

    else:
        # ── Perlu komentar — tanya dulu ──
        await state.update_data(selected_option_key=key, option_label=label)
        await state.set_state(ReportStates.waiting_comment)

        await callback.message.edit_text(
            f"<b>{label}</b>\n\n"
            "Tambahkan komentar untuk laporan ini:",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )


# ─────────────────────────────────────────────────────────────
# STEP 4: Terima komentar → kirim laporan
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_comment)
async def handle_comment(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Dibatalkan.")
        return

    comment = message.text.strip() if message.text else ""
    if not comment:
        await message.answer("❌ Komentar tidak boleh kosong. Ketik /cancel untuk batal.")
        return

    data = await state.get_data()
    peer_str    = data.get("peer_str")
    message_ids = data.get("message_ids")
    option_key  = data.get("selected_option_key")
    label       = data.get("option_label", option_key)
    admin_id    = data.get("admin_user_id", message.from_user.id)

    if not peer_str or not message_ids or not option_key:
        await state.clear()
        await message.answer("⚠️ Sesi habis, mulai ulang dengan /report.")
        return

    repeat = await db.get_report_count(admin_id)

    status = await message.answer(
        f"⏳ Mengirim laporan...\n"
        f"Alasan: <b>{label}</b>  |  Count: <b>{repeat}x</b>",
        parse_mode="HTML",
    )
    await state.clear()

    result = await run_report_with_rotation(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_id,
        repeat=repeat,
    )
    await status.edit_text(result["message"], parse_mode="HTML")
