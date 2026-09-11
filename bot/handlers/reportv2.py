"""
bot/handlers/reportv2.py
Report langsung ke channel/group — tanpa butuh link pesan spesifik.

Pakai functions.account.ReportPeer (bukan messages.Report),
yang memang dirancang untuk report peer (channel/group/user) secara langsung.

Flow:
  /reportv2 <link_channel>
    → pilih kategori (Level 1)
    → pilih sub-kategori (Level 2 / Level 3, jika ada)
    → isi komentar opsional / wajib (sesuai opsi)
    → pilih pesan: [All Messages] ← step ini yang baru
    → eksekusi

FSM States:
  ReportV2States.waiting_comment         → Menunggu komentar (opsional/wajib)
  ReportV2States.waiting_select_messages → Menunggu pilih pesan (All / batal)
"""

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from utils.parsers import parse_channel_link
from utils.report_executor import run_report_peer

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


# ─────────────────────────────────────────────────────────────
# OPTION MAP
# ─────────────────────────────────────────────────────────────

OPTION_MAP = {
    "v2_r1_idontlikeit":               ("other",         False,  "👎 I don't like it"),
    "v2_r2_childabuse_sexualabuse":    ("child_abuse",   "opt",  "👶 Child sexual abuse"),
    "v2_r2_childabuse_physicalabuse":  ("child_abuse",   "opt",  "👶 Child physical abuse"),
    "v2_r2_violence_insults":          ("violence",      "opt",  "🗣️ Insults or false information"),
    "v2_r2_violence_graphic":          ("violence",      "opt",  "😱 Graphic or disturbing content"),
    "v2_r2_violence_extreme":          ("violence",      "opt",  "💀 Extreme violence, dismemberment"),
    "v2_r2_violence_hatespeech":       ("violence",      "opt",  "🤬 Hate speech or symbols"),
    "v2_r2_violence_callingviolence":  ("violence",      "opt",  "⚔️ Calling for violence"),
    "v2_r2_violence_organizedcrime":   ("violence",      "opt",  "🕵️ Organized crime"),
    "v2_r2_violence_terrorism":        ("violence",      "req",  "💣 Terrorism"),
    "v2_r2_violence_animalabuse":      ("violence",      "opt",  "🐾 Animal abuse"),
    "v2_r2_illegalgoods_fakedocs":     ("other",         "opt",  "📄 Fake documents"),
    "v2_r2_illegalgoods_counterfeit":  ("other",         "opt",  "💵 Counterfeit money"),
    "v2_r2_illegalgoods_hacking":      ("other",         "opt",  "💻 Hacking tools and malware"),
    "v2_r2_illegalgoods_countermerch": ("other",         "opt",  "👜 Counterfeit merchandise"),
    "v2_r2_illegalgoods_other":        ("other",         "opt",  "📦 Other goods and services"),
    "v2_r3_weapons_firearms":          ("other",         "opt",  "🔫 Firearms and accessories"),
    "v2_r3_weapons_melee":             ("other",         "opt",  "🗡️ Melee weapons"),
    "v2_r3_weapons_nonlethal":         ("other",         "opt",  "⚡ Non-lethal weapons"),
    "v2_r3_weapons_other":             ("other",         "opt",  "❓ Other weapons"),
    "v2_r3_drugs_nicotine":            ("other",         "opt",  "🚬 Nicotine products"),
    "v2_r3_drugs_illegaldrugs":        ("other",         "opt",  "💊 Illegal drugs"),
    "v2_r3_drugs_other":               ("other",         "opt",  "❓ Other drugs"),
    "v2_r2_illegaladult_childabuse":   ("child_abuse",   "opt",  "👶 Child abuse"),
    "v2_r2_illegaladult_sexualservices":("porn",         "opt",  "🚫 Illegal sexual services"),
    "v2_r2_illegaladult_animalabuse":  ("violence",      "opt",  "🐾 Animal abuse"),
    "v2_r2_illegaladult_nonconsensual":("porn",          "opt",  "📸 Non-consensual sexual imagery"),
    "v2_r2_illegaladult_pornography":  ("porn",          "opt",  "🔞 Pornography"),
    "v2_r2_illegaladult_other":        ("porn",          "opt",  "❓ Other illegal sexual content"),
    "v2_r2_personaldata_privateimages":("personal_info", "opt",  "🖼️ Private images"),
    "v2_r2_personaldata_phonenumber":  ("personal_info", "opt",  "📱 Phone number"),
    "v2_r2_personaldata_address":      ("personal_info", "opt",  "🏠 Address"),
    "v2_r2_personaldata_stolendata":   ("personal_info", "opt",  "🔑 Stolen data or credentials"),
    "v2_r2_personaldata_other":        ("personal_info", "opt",  "❓ Other personal information"),
    "v2_r2_scamfraud_impersonation":   ("fake",          "opt",  "🎭 Impersonation"),
    "v2_r2_scamfraud_financial":       ("fake",          "opt",  "💸 Deceptive financial claims"),
    "v2_r2_scamfraud_malware":         ("fake",          "opt",  "🎣 Malware, phishing"),
    "v2_r2_scamfraud_fraudseller":     ("fake",          "opt",  "🛍️ Fraudulent seller"),
    "v2_r2_spam_insults":              ("spam",          "opt",  "🗣️ Insults or false information"),
    "v2_r2_spam_illegalcontent":       ("spam",          "opt",  "🚫 Promoting illegal content"),
    "v2_r2_spam_othercontent":         ("spam",          "opt",  "📢 Promoting other content"),
    "v2_r1_copyright":                 ("copyright",     "opt",  "©️ Copyright"),
}


# ─────────────────────────────────────────────────────────────
# KEYBOARD FACTORY
# ─────────────────────────────────────────────────────────────

def _kb(*rows) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*row)
    return b.as_markup()


def _btn(text, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def v2_kb_level1() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👎 I don't like it",           "v2_r1_idontlikeit")],
        [_btn("👶 Child abuse",                "v2_r1_childabuse")],
        [_btn("💥 Violence",                   "v2_r1_violence")],
        [_btn("🚫 Illegal goods and services", "v2_r1_illegalgoods")],
        [_btn("🔞 Illegal adult content",      "v2_r1_illegaladult")],
        [_btn("🛡️ Personal data",              "v2_r1_personaldata")],
        [_btn("💰 Scam or fraud",              "v2_r1_scamfraud")],
        [_btn("📢 Spam",                       "v2_r1_spam")],
        [_btn("©️ Copyright",                  "v2_r1_copyright")],
        [_btn("❌ Batal",                      "v2_cancel")],
    )


def v2_kb_child_abuse() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child sexual abuse",  "v2_r2_childabuse_sexualabuse")],
        [_btn("👶 Child physical abuse","v2_r2_childabuse_physicalabuse")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_violence() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information",  "v2_r2_violence_insults")],
        [_btn("😱 Graphic or disturbing content", "v2_r2_violence_graphic")],
        [_btn("💀 Extreme violence, dismemberment","v2_r2_violence_extreme")],
        [_btn("🤬 Hate speech or symbols",        "v2_r2_violence_hatespeech")],
        [_btn("⚔️ Calling for violence",          "v2_r2_violence_callingviolence")],
        [_btn("🕵️ Organized crime",               "v2_r2_violence_organizedcrime")],
        [_btn("💣 Terrorism",                     "v2_r2_violence_terrorism")],
        [_btn("🐾 Animal abuse",                  "v2_r2_violence_animalabuse")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_illegal_goods() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Weapons",                 "v2_r2_illegalgoods_weapons")],
        [_btn("💊 Drugs",                   "v2_r2_illegalgoods_drugs")],
        [_btn("📄 Fake documents",          "v2_r2_illegalgoods_fakedocs")],
        [_btn("💵 Counterfeit money",       "v2_r2_illegalgoods_counterfeit")],
        [_btn("💻 Hacking tools/malware",   "v2_r2_illegalgoods_hacking")],
        [_btn("👜 Counterfeit merchandise", "v2_r2_illegalgoods_countermerch")],
        [_btn("📦 Other goods/services",    "v2_r2_illegalgoods_other")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_weapons() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Firearms and accessories", "v2_r3_weapons_firearms")],
        [_btn("🗡️ Melee weapons",            "v2_r3_weapons_melee")],
        [_btn("⚡ Non-lethal weapons",        "v2_r3_weapons_nonlethal")],
        [_btn("❓ Other weapons",            "v2_r3_weapons_other")],
        [_btn("◀️ Kembali", "v2_back_illegalgoods"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_drugs() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🚬 Nicotine products", "v2_r3_drugs_nicotine")],
        [_btn("💊 Illegal drugs",     "v2_r3_drugs_illegaldrugs")],
        [_btn("❓ Other drugs",       "v2_r3_drugs_other")],
        [_btn("◀️ Kembali", "v2_back_illegalgoods"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_illegal_adult() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child abuse",                    "v2_r2_illegaladult_childabuse")],
        [_btn("🚫 Illegal sexual services",        "v2_r2_illegaladult_sexualservices")],
        [_btn("🐾 Animal abuse",                   "v2_r2_illegaladult_animalabuse")],
        [_btn("📸 Non-consensual sexual imagery",  "v2_r2_illegaladult_nonconsensual")],
        [_btn("🔞 Pornography",                    "v2_r2_illegaladult_pornography")],
        [_btn("❓ Other illegal sexual content",   "v2_r2_illegaladult_other")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_personal_data() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🖼️ Private images",           "v2_r2_personaldata_privateimages")],
        [_btn("📱 Phone number",             "v2_r2_personaldata_phonenumber")],
        [_btn("🏠 Address",                  "v2_r2_personaldata_address")],
        [_btn("🔑 Stolen data/credentials", "v2_r2_personaldata_stolendata")],
        [_btn("❓ Other personal info",      "v2_r2_personaldata_other")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_scam_fraud() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🎭 Impersonation",             "v2_r2_scamfraud_impersonation")],
        [_btn("💸 Deceptive financial claims","v2_r2_scamfraud_financial")],
        [_btn("🎣 Malware, phishing",         "v2_r2_scamfraud_malware")],
        [_btn("🛍️ Fraudulent seller",        "v2_r2_scamfraud_fraudseller")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_spam() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information", "v2_r2_spam_insults")],
        [_btn("🚫 Promoting illegal content",    "v2_r2_spam_illegalcontent")],
        [_btn("📢 Promoting other content",      "v2_r2_spam_othercontent")],
        [_btn("◀️ Kembali", "v2_back_l1"), _btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_comment_optional(cb_data: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Kirim Tanpa Komentar", f"v2_submit_nocomment_{cb_data}")],
        [_btn("❌ Batal", "v2_cancel")],
    )


def v2_kb_comment_required() -> InlineKeyboardMarkup:
    return _kb([_btn("❌ Batal", "v2_cancel")])


def v2_kb_select_messages() -> InlineKeyboardMarkup:
    """Keyboard pilih pesan — step terakhir sebelum eksekusi."""
    return _kb(
        [_btn("📨 All Messages", "v2_select_all_messages")],
        [_btn("❌ Batal", "v2_cancel")],
    )


# Level 1 → keyboard level 2
V2_L1_TO_KB = {
    "v2_r1_childabuse":   v2_kb_child_abuse,
    "v2_r1_violence":     v2_kb_violence,
    "v2_r1_illegalgoods": v2_kb_illegal_goods,
    "v2_r1_illegaladult": v2_kb_illegal_adult,
    "v2_r1_personaldata": v2_kb_personal_data,
    "v2_r1_scamfraud":    v2_kb_scam_fraud,
    "v2_r1_spam":         v2_kb_spam,
}

V2_L2_TO_KB = {
    "v2_r2_illegalgoods_weapons": v2_kb_weapons,
    "v2_r2_illegalgoods_drugs":   v2_kb_drugs,
}

V2_L1_LABELS = {
    "v2_r1_childabuse":   "👶 Child abuse",
    "v2_r1_violence":     "💥 Violence",
    "v2_r1_illegalgoods": "🚫 Illegal goods and services",
    "v2_r1_illegaladult": "🔞 Illegal adult content",
    "v2_r1_personaldata": "🛡️ Personal data",
    "v2_r1_scamfraud":    "💰 Scam or fraud",
    "v2_r1_spam":         "📢 Spam",
}


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class ReportV2States(StatesGroup):
    waiting_comment         = State()
    waiting_select_messages = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def _show_select_messages(target_msg, state: FSMContext, label: str, comment: str = "") -> None:
    """Tampilkan step 'pilih pesan' sebelum eksekusi final."""
    comment_line = f"\n💬 Komentar: <i>{comment}</i>" if comment else ""
    await target_msg.edit_text(
        f"📋 Alasan: <b>{label}</b>{comment_line}\n\n"
        "📨 Pilih pesan yang ingin dilaporkan:",
        parse_mode="HTML",
        reply_markup=v2_kb_select_messages(),
    )
    await state.set_state(ReportV2States.waiting_select_messages)


async def _do_execute_v2(
    peer_str: str,
    option_key: str,
    comment: str,
    admin_user_id: int,
    label: str,
    target_msg,
) -> None:
    """Eksekusi report peer dan update pesan status."""
    repeat = await db.get_report_count(admin_user_id)

    await target_msg.edit_text(
        f"⏳ Mengirim laporan ke channel/group...\n"
        f"📋 Alasan: <b>{label}</b>\n"
        f"🔁 Jumlah: <b>{repeat}x</b>",
        parse_mode="HTML",
    )

    result = await run_report_peer(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=admin_user_id,
        repeat=repeat,
    )

    await target_msg.edit_text(result["message"], parse_mode="HTML")


# ─────────────────────────────────────────────────────────────
# COMMAND: /reportv2 <link_channel>
# ─────────────────────────────────────────────────────────────

@router.message(Command("reportv2"))
async def cmd_reportv2(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    # Clear FSM state sebelumnya biar tidak konflik
    await state.clear()

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/reportv2 t.me/nama_channel</code>",
            parse_mode="HTML",
        )
        return

    raw_link = parts[1].strip()
    peer_str = parse_channel_link(raw_link)

    if not peer_str:
        await message.answer(
            "❌ Link tidak valid.\n"
            "Contoh: <code>t.me/somechannel</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(peer_str=peer_str)

    await message.answer(
        f"🎯 <b>Target Report (Channel/Group)</b>\n"
        f"📌 Peer: <code>{peer_str}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=v2_kb_level1(),
    )


# ─────────────────────────────────────────────────────────────
# CALLBACK: Batal
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "v2_cancel")
async def cb_v2_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Proses report v2 dibatalkan.")
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kembali ke Level 1
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "v2_back_l1")
async def cb_v2_back_l1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.set_state(None)
    data = await state.get_data()
    peer_str = data.get("peer_str", "")
    await callback.message.edit_text(
        f"🎯 <b>Target Report (Channel/Group)</b>\n"
        f"📌 Peer: <code>{peer_str}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=v2_kb_level1(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kembali ke Level 2 Illegal Goods
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "v2_back_illegalgoods")
async def cb_v2_back_illegalgoods(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 <b>Illegal goods and services</b>\n\nPilih sub-kategori:",
        parse_mode="HTML",
        reply_markup=v2_kb_illegal_goods(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 1
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("v2_r1_"))
async def cb_v2_level1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    # Langsung ke pilih pesan (I don't like it — tanpa komentar)
    if cb_data in OPTION_MAP and OPTION_MAP[cb_data][1] is False:
        option_key, _, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportv2", show_alert=True)
            await state.clear()
            return
        await state.update_data(selected_option=option_key, option_label=label, pending_comment="")
        await callback.answer()
        await _show_select_messages(callback.message, state, label)
        return

    # Copyright → komentar opsional → lalu pilih pesan
    if cb_data == "v2_r1_copyright":
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )
        await callback.message.edit_text(
            "©️ <b>Copyright</b>\n\nTambah komentar (opsional):\n\n"
            "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
            parse_mode="HTML",
            reply_markup=v2_kb_comment_optional(cb_data),
        )
        await state.set_state(ReportV2States.waiting_comment)
        await callback.answer()
        return

    # Navigasi ke Level 2
    if cb_data in V2_L1_TO_KB:
        label = V2_L1_LABELS.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=V2_L1_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 2
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("v2_r2_"))
async def cb_v2_level2(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    # Navigasi ke Level 3
    if cb_data in V2_L2_TO_KB:
        label_map = {
            "v2_r2_illegalgoods_weapons": "🔫 Weapons",
            "v2_r2_illegalgoods_drugs":   "💊 Drugs",
        }
        label = label_map.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=V2_L2_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    # Lanjut ke komentar / pilih pesan
    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportv2", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )

        if needs_comment == "req":
            # Komentar wajib (Terrorism) → tunggu komentar dulu
            await state.set_state(ReportV2States.waiting_comment)
            await callback.message.edit_text(
                "💣 <b>Terrorism</b>\n\n✏️ <b>Tambah Komentar (Wajib)</b>\n\n"
                "Kirimkan teks komentar Anda:",
                parse_mode="HTML",
                reply_markup=v2_kb_comment_required(),
            )
        else:
            # Komentar opsional → tawarkan skip
            await state.set_state(ReportV2States.waiting_comment)
            await callback.message.edit_text(
                f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):\n\n"
                "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
                parse_mode="HTML",
                reply_markup=v2_kb_comment_optional(cb_data),
            )

        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 3
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("v2_r3_"))
async def cb_v2_level3(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportv2", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )
        await state.set_state(ReportV2States.waiting_comment)
        await callback.message.edit_text(
            f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):\n\n"
            "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
            parse_mode="HTML",
            reply_markup=v2_kb_comment_optional(cb_data),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kirim Tanpa Komentar → lanjut ke pilih pesan
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("v2_submit_nocomment_"))
async def cb_v2_submit_nocomment(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    data = await state.get_data()
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)

    if not peer_str or not option_key:
        await callback.answer("⚠️ Sesi habis, mulai ulang /reportv2", show_alert=True)
        await state.clear()
        return

    # Simpan komentar kosong, lanjut ke pilih pesan
    await state.update_data(pending_comment="")
    await callback.answer()
    await _show_select_messages(callback.message, state, label, comment="")


# ─────────────────────────────────────────────────────────────
# MESSAGE: Terima Komentar → lanjut ke pilih pesan
# ─────────────────────────────────────────────────────────────

@router.message(ReportV2States.waiting_comment)
async def handle_v2_comment(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    # Command apapun saat waiting_comment → clear state, biarkan command diproses normal
    if message.text and message.text.strip().startswith("/"):
        await state.clear()
        await message.answer(
            "⚠️ Sesi report dibatalkan karena ada command baru. "
            "Silakan ulangi command yang kamu kirim."
        )
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Proses report v2 dibatalkan.")
        return

    comment = message.text.strip() if message.text else ""
    data = await state.get_data()
    needs_comment = data.get("needs_comment")

    if needs_comment == "req" and not comment:
        await message.answer("❌ Komentar tidak boleh kosong. Ketik komentar atau /cancel.")
        return

    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)

    if not peer_str or not option_key:
        await state.clear()
        await message.answer("⚠️ Sesi habis. Mulai ulang dengan /reportv2.")
        return

    # Simpan komentar, tampilkan pilih pesan
    await state.update_data(pending_comment=comment)
    status_msg = await message.answer(
        f"📋 Alasan: <b>{label}</b>\n"
        f"💬 Komentar: <i>{comment}</i>\n\n"
        "📨 Pilih pesan yang ingin dilaporkan:",
        parse_mode="HTML",
        reply_markup=v2_kb_select_messages(),
    )
    await state.update_data(status_msg_id=status_msg.message_id)
    await state.set_state(ReportV2States.waiting_select_messages)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Pilih All Messages → Eksekusi
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "v2_select_all_messages")
async def cb_v2_select_all_messages(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    data = await state.get_data()
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)
    comment = data.get("pending_comment", "")

    if not peer_str or not option_key:
        await callback.answer("⚠️ Sesi habis, mulai ulang /reportv2", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    await _do_execute_v2(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=callback.from_user.id,
        label=label,
        target_msg=callback.message,
    )
    await state.clear()
