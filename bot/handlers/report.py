"""
bot/handlers/report.py
Handler untuk flow /report lengkap dengan sistem 3 level menu
sesuai struktur report Telegram versi terbaru 2026.

Flow:
  /report <link> → minta link pesan → tampil Level 1
  → Level 2 (sub-opsi) → Level 3 (jika ada)
  → Komentar opsional/wajib → Eksekusi report

FSM States:
  ReportStates.waiting_message_link → Menunggu link pesan
  ReportStates.waiting_comment      → Menunggu komentar
"""

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.report_kb import (
    kb_cancel_only,
    kb_child_abuse,
    kb_comment_optional,
    kb_comment_required,
    kb_drugs,
    kb_illegal_adult,
    kb_illegal_goods,
    kb_level1,
    kb_personal_data,
    kb_scam_fraud,
    kb_spam,
    kb_violence,
    kb_weapons,
)
from database import db
from utils.parsers import is_valid_telegram_link, parse_message_link
from utils.report_executor import run_report_with_rotation
from pyrogram.raw import types as raw_types

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


# ─────────────────────────────────────────────────────────────
# MAPPING CALLBACK → (reason_class, needs_comment, label)
# ─────────────────────────────────────────────────────────────

# Format: "callback_key": (option_key_for_executor, needs_comment, label)
# needs_comment:
#   False  = langsung eksekusi tanpa komentar (I don't like it)
#   "opt"  = komentar opsional (bisa skip)
#   "req"  = komentar WAJIB (terrorism)

OPTION_MAP = {
    # ── Level 1 langsung eksekusi ──
    "r1_idontlikeit": ("other", False, "👎 I don't like it"),

    # ── Level 1 yang punya sub-opsi ──
    # (ditangani di handler navigasi, bukan di sini)

    # ── Level 2: Child abuse ──
    "r2_childabuse_sexualabuse":   ("child_abuse", "opt", "👶 Child sexual abuse"),
    "r2_childabuse_physicalabuse": ("child_abuse", "opt", "👶 Child physical abuse"),

    # ── Level 2: Violence ──
    "r2_violence_insults":         ("violence", "opt", "🗣️ Insults or false information"),
    "r2_violence_graphic":         ("violence", "opt", "😱 Graphic or disturbing content"),
    "r2_violence_extreme":         ("violence", "opt", "💀 Extreme violence, dismemberment"),
    "r2_violence_hatespeech":      ("violence", "opt", "🤬 Hate speech or symbols"),
    "r2_violence_callingviolence": ("violence", "opt", "⚔️ Calling for violence"),
    "r2_violence_organizedcrime":  ("violence", "opt", "🕵️ Organized crime"),
    "r2_violence_terrorism":       ("violence", "req", "💣 Terrorism"),
    "r2_violence_animalabuse":     ("violence", "opt", "🐾 Animal abuse"),

    # ── Level 2: Illegal goods (non-weapon/drug) ──
    "r2_illegalgoods_fakedocs":    ("other", "opt", "📄 Fake documents"),
    "r2_illegalgoods_counterfeit": ("other", "opt", "💵 Counterfeit money"),
    "r2_illegalgoods_hacking":     ("other", "opt", "💻 Hacking tools and malware"),
    "r2_illegalgoods_countermerch":("other", "opt", "👜 Counterfeit merchandise"),
    "r2_illegalgoods_other":       ("other", "opt", "📦 Other goods and services"),

    # ── Level 3: Weapons ──
    "r3_weapons_firearms":  ("other", "opt", "🔫 Firearms and accessories"),
    "r3_weapons_melee":     ("other", "opt", "🗡️ Melee weapons"),
    "r3_weapons_nonlethal": ("other", "opt", "⚡ Non-lethal weapons"),
    "r3_weapons_other":     ("other", "opt", "❓ Other weapons"),

    # ── Level 3: Drugs ──
    "r3_drugs_nicotine":    ("other", "opt", "🚬 Nicotine products"),
    "r3_drugs_illegaldrugs":("other", "opt", "💊 Illegal drugs"),
    "r3_drugs_other":       ("other", "opt", "❓ Other drugs"),

    # ── Level 2: Illegal adult content ──
    "r2_illegaladult_childabuse":     ("child_abuse",  "opt", "👶 Child abuse"),
    "r2_illegaladult_sexualservices": ("porn",         "opt", "🚫 Illegal sexual services"),
    "r2_illegaladult_animalabuse":    ("violence",     "opt", "🐾 Animal abuse"),
    "r2_illegaladult_nonconsensual":  ("porn",         "opt", "📸 Non-consensual sexual imagery"),
    "r2_illegaladult_pornography":    ("porn",         "opt", "🔞 Pornography"),
    "r2_illegaladult_other":          ("porn",         "opt", "❓ Other illegal sexual content"),

    # ── Level 2: Personal data ──
    "r2_personaldata_privateimages": ("personal_info", "opt", "🖼️ Private images"),
    "r2_personaldata_phonenumber":   ("personal_info", "opt", "📱 Phone number"),
    "r2_personaldata_address":       ("personal_info", "opt", "🏠 Address"),
    "r2_personaldata_stolendata":    ("personal_info", "opt", "🔑 Stolen data or credentials"),
    "r2_personaldata_other":         ("personal_info", "opt", "❓ Other personal information"),

    # ── Level 2: Scam or fraud ──
    "r2_scamfraud_impersonation": ("fake", "opt", "🎭 Impersonation"),
    "r2_scamfraud_financial":     ("fake", "opt", "💸 Deceptive financial claims"),
    "r2_scamfraud_malware":       ("fake", "opt", "🎣 Malware, phishing"),
    "r2_scamfraud_fraudseller":   ("fake", "opt", "🛍️ Fraudulent seller"),

    # ── Level 2: Spam ──
    "r2_spam_insults":       ("spam", "opt", "🗣️ Insults or false information"),
    "r2_spam_illegalcontent":("spam", "opt", "🚫 Promoting illegal content"),
    "r2_spam_othercontent":  ("spam", "opt", "📢 Promoting other content"),

    # ── Level 1: Copyright (langsung ke komentar) ──
    "r1_copyright": ("copyright", "opt", "©️ Copyright"),
}

# Mapping Level 1 → keyboard Level 2
L1_TO_KB = {
    "r1_childabuse":   kb_child_abuse,
    "r1_violence":     kb_violence,
    "r1_illegalgoods": kb_illegal_goods,
    "r1_illegaladult": kb_illegal_adult,
    "r1_personaldata": kb_personal_data,
    "r1_scamfraud":    kb_scam_fraud,
    "r1_spam":         kb_spam,
}

# Mapping Level 2 → keyboard Level 3
L2_TO_KB = {
    "r2_illegalgoods_weapons": kb_weapons,
    "r2_illegalgoods_drugs":   kb_drugs,
}

# Label Level 1
L1_LABELS = {
    "r1_childabuse":   "👶 Child abuse",
    "r1_violence":     "💥 Violence",
    "r1_illegalgoods": "🚫 Illegal goods and services",
    "r1_illegaladult": "🔞 Illegal adult content",
    "r1_personaldata": "🛡️ Personal data",
    "r1_scamfraud":    "💰 Scam or fraud",
    "r1_spam":         "📢 Spam",
}


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class ReportStates(StatesGroup):
    waiting_message_link = State()
    waiting_comment      = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def _do_execute(
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str,
    admin_user_id: int,
    label: str,
    target_msg,  # Message atau callback.message untuk edit
) -> None:
    """Helper: eksekusi report dan update pesan status."""
    repeat = await db.get_report_count(admin_user_id)

    await target_msg.edit_text(
        f"⏳ Mengirim laporan...\n"
        f"📋 Alasan: <b>{label}</b>\n"
        f"🔁 Jumlah: <b>{repeat}x</b>",
        parse_mode="HTML",
    )

    result = await run_report_with_rotation(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
    )

    await target_msg.edit_text(result["message"], parse_mode="HTML")


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
            "Gunakan: <code>/report t.me/nama_channel</code>",
            parse_mode="HTML",
        )
        return

    channel_link = parts[1].strip()
    if not is_valid_telegram_link(channel_link):
        await message.answer(
            "❌ Link tidak valid.\n"
            "Contoh: <code>t.me/somechannel</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(channel_link=channel_link)
    await state.set_state(ReportStates.waiting_message_link)

    await message.answer(
        f"📌 Target: <code>{channel_link}</code>\n\n"
        "Kirimkan <b>link pesan spesifik</b> yang ingin dilaporkan.\n"
        "Contoh: <code>t.me/somechannel/123</code>\n\n"
        "Atau ketik /cancel untuk membatalkan.",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────
# STEP 2: Terima link pesan → tampil Level 1
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_message_link)
async def handle_message_link(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Proses report dibatalkan.")
        return

    parsed = parse_message_link(message.text.strip())
    if not parsed:
        await message.answer(
            "❌ Link pesan tidak valid.\n"
            "Contoh: <code>t.me/somechannel/123</code>\n\n"
            "Coba lagi atau ketik /cancel.",
            parse_mode="HTML",
        )
        return

    peer_str, message_id = parsed
    await state.update_data(
        peer_str=peer_str,
        message_ids=[message_id],
    )

    await message.answer(
        f"🎯 <b>Target Laporan</b>\n"
        f"📌 Peer: <code>{peer_str}</code>\n"
        f"📝 Pesan ID: <code>{message_id}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=kb_level1(),
    )


# ─────────────────────────────────────────────────────────────
# CALLBACK: Batal
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "r_cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Proses report dibatalkan.")
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kembali ke Level 1
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "r_back_l1")
async def cb_back_l1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    data = await state.get_data()
    peer_str = data.get("peer_str", "")
    msg_ids = data.get("message_ids", [])
    await callback.message.edit_text(
        f"🎯 <b>Target Laporan</b>\n"
        f"📌 Peer: <code>{peer_str}</code>\n"
        f"📝 Pesan ID: <code>{msg_ids}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=kb_level1(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kembali ke Level 2 Illegal Goods
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "r_back_illegalgoods")
async def cb_back_illegalgoods(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 <b>Illegal goods and services</b>\n\nPilih sub-kategori:",
        parse_mode="HTML",
        reply_markup=kb_illegal_goods(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 1 → Navigasi atau Langsung Eksekusi
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("r1_"))
async def cb_level1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    # Cek apakah langsung eksekusi (I don't like it)
    if cb_data in OPTION_MAP and OPTION_MAP[cb_data][1] is False:
        option_key, _, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        message_ids = data.get("message_ids")

        if not peer_str or not message_ids:
            await callback.answer("⚠️ Sesi habis, mulai ulang /report", show_alert=True)
            await state.clear()
            return

        await callback.answer()
        await _do_execute(
            peer_str=peer_str,
            message_ids=message_ids,
            option_key=option_key,
            comment="",
            admin_user_id=callback.from_user.id,
            label=label,
            target_msg=callback.message,
        )
        await state.clear()
        return

    # Cek apakah Copyright (langsung ke komentar opsional)
    if cb_data == "r1_copyright":
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )
        await callback.message.edit_text(
            f"©️ <b>Copyright</b>\n\n"
            "Tambah komentar (opsional):\n\n"
            "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
            parse_mode="HTML",
            reply_markup=kb_comment_optional(cb_data),
        )
        await state.set_state(ReportStates.waiting_comment)
        await callback.answer()
        return

    # Navigasi ke Level 2
    if cb_data in L1_TO_KB:
        label = L1_LABELS.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=L1_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 2 → Navigasi Level 3 atau Komentar
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("r2_"))
async def cb_level2(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    # Cek apakah ada Level 3 (Weapons/Drugs)
    if cb_data in L2_TO_KB:
        label_map = {
            "r2_illegalgoods_weapons": "🔫 Weapons",
            "r2_illegalgoods_drugs":   "💊 Drugs",
        }
        label = label_map.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=L2_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    # Langsung ke komentar
    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        message_ids = data.get("message_ids")

        if not peer_str or not message_ids:
            await callback.answer("⚠️ Sesi habis, mulai ulang /report", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )

        if needs_comment == "req":
            # Komentar WAJIB (Terrorism)
            await state.set_state(ReportStates.waiting_comment)
            await callback.message.edit_text(
                f"💣 <b>Terrorism</b>\n\n"
                "✏️ <b>Tambah Komentar (Wajib)</b>\n\n"
                "Mohon bantu kami dengan memaparkan keluhan Anda "
                "atas pesan yang dilaporkan.\n\n"
                "Kirimkan teks komentar Anda:",
                parse_mode="HTML",
                reply_markup=kb_comment_required(),
            )
        else:
            # Komentar opsional
            await state.set_state(ReportStates.waiting_comment)
            await callback.message.edit_text(
                f"<b>{label}</b>\n\n"
                "✏️ Tambah komentar (opsional):\n\n"
                "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
                parse_mode="HTML",
                reply_markup=kb_comment_optional(cb_data),
            )

        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 3 → Komentar
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("r3_"))
async def cb_level3(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        message_ids = data.get("message_ids")

        if not peer_str or not message_ids:
            await callback.answer("⚠️ Sesi habis, mulai ulang /report", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )
        await state.set_state(ReportStates.waiting_comment)

        await callback.message.edit_text(
            f"<b>{label}</b>\n\n"
            "✏️ Tambah komentar (opsional):\n\n"
            "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
            parse_mode="HTML",
            reply_markup=kb_comment_optional(cb_data),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kirim Tanpa Komentar (opsional skip)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("r_submit_nocomment_"))
async def cb_submit_nocomment(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    data = await state.get_data()
    peer_str = data.get("peer_str")
    message_ids = data.get("message_ids")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)

    if not peer_str or not message_ids or not option_key:
        await callback.answer("⚠️ Sesi habis, mulai ulang /report", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    await _do_execute(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment="",
        admin_user_id=callback.from_user.id,
        label=label,
        target_msg=callback.message,
    )
    await state.clear()


# ─────────────────────────────────────────────────────────────
# MESSAGE: Terima Komentar → Eksekusi
# ─────────────────────────────────────────────────────────────

@router.message(ReportStates.waiting_comment)
async def handle_comment(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Proses report dibatalkan.")
        return

    comment = message.text.strip()
    if not comment:
        await message.answer("❌ Komentar tidak boleh kosong. Ketik komentar atau /cancel.")
        return

    data = await state.get_data()
    peer_str = data.get("peer_str")
    message_ids = data.get("message_ids")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)

    if not peer_str or not message_ids or not option_key:
        await state.clear()
        await message.answer("⚠️ Sesi habis. Mulai ulang dengan /report.")
        return

    status_msg = await message.answer(
        f"⏳ Memproses laporan...\n"
        f"📋 Alasan: <b>{label}</b>\n"
        f"💬 Komentar: <i>{comment}</i>",
        parse_mode="HTML",
    )

    repeat = await db.get_report_count(message.from_user.id)
    result = await run_report_with_rotation(
        peer_str=peer_str,
        message_ids=message_ids,
        option_key=option_key,
        comment=comment,
        admin_user_id=message.from_user.id,
        repeat=repeat,
    )

    await status_msg.edit_text(result["message"], parse_mode="HTML")
    await state.clear()

    logger.info(
        f"[REPORT] option={option_key} comment='{comment}' "
        f"success={result['success']} sent={result.get('sent')}"
    )
