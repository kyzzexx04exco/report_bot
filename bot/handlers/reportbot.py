"""
bot/handlers/reportbot.py
Report langsung ke user/bot Telegram.

Flow sama persis dengan reportv2 (termasuk pilih pesan di akhir),
tapi target adalah user/bot — bukan channel/group.
Tidak ada step pilih pesan karena report user/bot pakai account.ReportPeer
yang tidak butuh message_id.

  /reportbot <username_or_link>
    → pilih kategori (Level 1)
    → pilih sub-kategori (Level 2 / Level 3, jika ada)
    → isi komentar opsional / wajib
    → pilih pesan: [All Messages]
    → eksekusi
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
# OPTION MAP  (prefix "rb_")
# ─────────────────────────────────────────────────────────────

OPTION_MAP = {
    "rb_r1_idontlikeit":               ("other",         False,  "👎 I don't like it"),
    "rb_r2_childabuse_sexualabuse":    ("child_abuse",   "opt",  "👶 Child sexual abuse"),
    "rb_r2_childabuse_physicalabuse":  ("child_abuse",   "opt",  "👶 Child physical abuse"),
    "rb_r2_violence_insults":          ("violence",      "opt",  "🗣️ Insults or false information"),
    "rb_r2_violence_graphic":          ("violence",      "opt",  "😱 Graphic or disturbing content"),
    "rb_r2_violence_extreme":          ("violence",      "opt",  "💀 Extreme violence, dismemberment"),
    "rb_r2_violence_hatespeech":       ("violence",      "opt",  "🤬 Hate speech or symbols"),
    "rb_r2_violence_callingviolence":  ("violence",      "opt",  "⚔️ Calling for violence"),
    "rb_r2_violence_organizedcrime":   ("violence",      "opt",  "🕵️ Organized crime"),
    "rb_r2_violence_terrorism":        ("violence",      "req",  "💣 Terrorism"),
    "rb_r2_violence_animalabuse":      ("violence",      "opt",  "🐾 Animal abuse"),
    "rb_r2_illegalgoods_fakedocs":     ("other",         "opt",  "📄 Fake documents"),
    "rb_r2_illegalgoods_counterfeit":  ("other",         "opt",  "💵 Counterfeit money"),
    "rb_r2_illegalgoods_hacking":      ("other",         "opt",  "💻 Hacking tools and malware"),
    "rb_r2_illegalgoods_countermerch": ("other",         "opt",  "👜 Counterfeit merchandise"),
    "rb_r2_illegalgoods_other":        ("other",         "opt",  "📦 Other goods and services"),
    "rb_r3_weapons_firearms":          ("other",         "opt",  "🔫 Firearms and accessories"),
    "rb_r3_weapons_melee":             ("other",         "opt",  "🗡️ Melee weapons"),
    "rb_r3_weapons_nonlethal":         ("other",         "opt",  "⚡ Non-lethal weapons"),
    "rb_r3_weapons_other":             ("other",         "opt",  "❓ Other weapons"),
    "rb_r3_drugs_nicotine":            ("other",         "opt",  "🚬 Nicotine products"),
    "rb_r3_drugs_illegaldrugs":        ("other",         "opt",  "💊 Illegal drugs"),
    "rb_r3_drugs_other":               ("other",         "opt",  "❓ Other drugs"),
    "rb_r2_illegaladult_childabuse":   ("child_abuse",   "opt",  "👶 Child abuse"),
    "rb_r2_illegaladult_sexualservices":("porn",         "opt",  "🚫 Illegal sexual services"),
    "rb_r2_illegaladult_animalabuse":  ("violence",      "opt",  "🐾 Animal abuse"),
    "rb_r2_illegaladult_nonconsensual":("porn",          "opt",  "📸 Non-consensual sexual imagery"),
    "rb_r2_illegaladult_pornography":  ("porn",          "opt",  "🔞 Pornography"),
    "rb_r2_illegaladult_other":        ("porn",          "opt",  "❓ Other illegal sexual content"),
    "rb_r2_personaldata_privateimages":("personal_info", "opt",  "🖼️ Private images"),
    "rb_r2_personaldata_phonenumber":  ("personal_info", "opt",  "📱 Phone number"),
    "rb_r2_personaldata_address":      ("personal_info", "opt",  "🏠 Address"),
    "rb_r2_personaldata_stolendata":   ("personal_info", "opt",  "🔑 Stolen data or credentials"),
    "rb_r2_personaldata_other":        ("personal_info", "opt",  "❓ Other personal information"),
    "rb_r2_scamfraud_impersonation":   ("fake",          "opt",  "🎭 Impersonation"),
    "rb_r2_scamfraud_financial":       ("fake",          "opt",  "💸 Deceptive financial claims"),
    "rb_r2_scamfraud_malware":         ("fake",          "opt",  "🎣 Malware, phishing"),
    "rb_r2_scamfraud_fraudseller":     ("fake",          "opt",  "🛍️ Fraudulent seller"),
    "rb_r2_spam_insults":              ("spam",          "opt",  "🗣️ Insults or false information"),
    "rb_r2_spam_illegalcontent":       ("spam",          "opt",  "🚫 Promoting illegal content"),
    "rb_r2_spam_othercontent":         ("spam",          "opt",  "📢 Promoting other content"),
    "rb_r1_copyright":                 ("copyright",     "opt",  "©️ Copyright"),
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


def rb_kb_level1() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👎 I don't like it",           "rb_r1_idontlikeit")],
        [_btn("👶 Child abuse",                "rb_r1_childabuse")],
        [_btn("💥 Violence",                   "rb_r1_violence")],
        [_btn("🚫 Illegal goods and services", "rb_r1_illegalgoods")],
        [_btn("🔞 Illegal adult content",      "rb_r1_illegaladult")],
        [_btn("🛡️ Personal data",              "rb_r1_personaldata")],
        [_btn("💰 Scam or fraud",              "rb_r1_scamfraud")],
        [_btn("📢 Spam",                       "rb_r1_spam")],
        [_btn("©️ Copyright",                  "rb_r1_copyright")],
        [_btn("❌ Batal",                      "rb_cancel")],
    )


def rb_kb_child_abuse() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child sexual abuse",  "rb_r2_childabuse_sexualabuse")],
        [_btn("👶 Child physical abuse","rb_r2_childabuse_physicalabuse")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_violence() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information",  "rb_r2_violence_insults")],
        [_btn("😱 Graphic or disturbing content", "rb_r2_violence_graphic")],
        [_btn("💀 Extreme violence, dismemberment","rb_r2_violence_extreme")],
        [_btn("🤬 Hate speech or symbols",        "rb_r2_violence_hatespeech")],
        [_btn("⚔️ Calling for violence",          "rb_r2_violence_callingviolence")],
        [_btn("🕵️ Organized crime",               "rb_r2_violence_organizedcrime")],
        [_btn("💣 Terrorism",                     "rb_r2_violence_terrorism")],
        [_btn("🐾 Animal abuse",                  "rb_r2_violence_animalabuse")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_illegal_goods() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Weapons",                 "rb_r2_illegalgoods_weapons")],
        [_btn("💊 Drugs",                   "rb_r2_illegalgoods_drugs")],
        [_btn("📄 Fake documents",          "rb_r2_illegalgoods_fakedocs")],
        [_btn("💵 Counterfeit money",       "rb_r2_illegalgoods_counterfeit")],
        [_btn("💻 Hacking tools/malware",   "rb_r2_illegalgoods_hacking")],
        [_btn("👜 Counterfeit merchandise", "rb_r2_illegalgoods_countermerch")],
        [_btn("📦 Other goods/services",    "rb_r2_illegalgoods_other")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_weapons() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Firearms and accessories", "rb_r3_weapons_firearms")],
        [_btn("🗡️ Melee weapons",            "rb_r3_weapons_melee")],
        [_btn("⚡ Non-lethal weapons",        "rb_r3_weapons_nonlethal")],
        [_btn("❓ Other weapons",            "rb_r3_weapons_other")],
        [_btn("◀️ Kembali", "rb_back_illegalgoods"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_drugs() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🚬 Nicotine products", "rb_r3_drugs_nicotine")],
        [_btn("💊 Illegal drugs",     "rb_r3_drugs_illegaldrugs")],
        [_btn("❓ Other drugs",       "rb_r3_drugs_other")],
        [_btn("◀️ Kembali", "rb_back_illegalgoods"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_illegal_adult() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child abuse",                    "rb_r2_illegaladult_childabuse")],
        [_btn("🚫 Illegal sexual services",        "rb_r2_illegaladult_sexualservices")],
        [_btn("🐾 Animal abuse",                   "rb_r2_illegaladult_animalabuse")],
        [_btn("📸 Non-consensual sexual imagery",  "rb_r2_illegaladult_nonconsensual")],
        [_btn("🔞 Pornography",                    "rb_r2_illegaladult_pornography")],
        [_btn("❓ Other illegal sexual content",   "rb_r2_illegaladult_other")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_personal_data() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🖼️ Private images",           "rb_r2_personaldata_privateimages")],
        [_btn("📱 Phone number",             "rb_r2_personaldata_phonenumber")],
        [_btn("🏠 Address",                  "rb_r2_personaldata_address")],
        [_btn("🔑 Stolen data/credentials", "rb_r2_personaldata_stolendata")],
        [_btn("❓ Other personal info",      "rb_r2_personaldata_other")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_scam_fraud() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🎭 Impersonation",             "rb_r2_scamfraud_impersonation")],
        [_btn("💸 Deceptive financial claims","rb_r2_scamfraud_financial")],
        [_btn("🎣 Malware, phishing",         "rb_r2_scamfraud_malware")],
        [_btn("🛍️ Fraudulent seller",        "rb_r2_scamfraud_fraudseller")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_spam() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information", "rb_r2_spam_insults")],
        [_btn("🚫 Promoting illegal content",    "rb_r2_spam_illegalcontent")],
        [_btn("📢 Promoting other content",      "rb_r2_spam_othercontent")],
        [_btn("◀️ Kembali", "rb_back_l1"), _btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_comment_optional(cb_data: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Kirim Tanpa Komentar", f"rb_submit_nocomment_{cb_data}")],
        [_btn("❌ Batal", "rb_cancel")],
    )


def rb_kb_comment_required() -> InlineKeyboardMarkup:
    return _kb([_btn("❌ Batal", "rb_cancel")])



RB_L1_TO_KB = {
    "rb_r1_childabuse":   rb_kb_child_abuse,
    "rb_r1_violence":     rb_kb_violence,
    "rb_r1_illegalgoods": rb_kb_illegal_goods,
    "rb_r1_illegaladult": rb_kb_illegal_adult,
    "rb_r1_personaldata": rb_kb_personal_data,
    "rb_r1_scamfraud":    rb_kb_scam_fraud,
    "rb_r1_spam":         rb_kb_spam,
}

RB_L2_TO_KB = {
    "rb_r2_illegalgoods_weapons": rb_kb_weapons,
    "rb_r2_illegalgoods_drugs":   rb_kb_drugs,
}

RB_L1_LABELS = {
    "rb_r1_childabuse":   "👶 Child abuse",
    "rb_r1_violence":     "💥 Violence",
    "rb_r1_illegalgoods": "🚫 Illegal goods and services",
    "rb_r1_illegaladult": "🔞 Illegal adult content",
    "rb_r1_personaldata": "🛡️ Personal data",
    "rb_r1_scamfraud":    "💰 Scam or fraud",
    "rb_r1_spam":         "📢 Spam",
}


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class ReportBotStates(StatesGroup):
    waiting_comment = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID



async def _do_execute_rb(
    peer_str: str,
    option_key: str,
    comment: str,
    admin_user_id: int,
    label: str,
    target_msg,
) -> None:
    repeat = await db.get_report_count(admin_user_id)

    await target_msg.edit_text(
        f"⏳ Mengirim laporan ke user/bot...\n"
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
# COMMAND: /reportbot <username_or_link>
# ─────────────────────────────────────────────────────────────

@router.message(Command("reportbot"))
async def cmd_reportbot(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    # Clear FSM state sebelumnya biar tidak konflik
    await state.clear()

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/reportbot @username</code> atau <code>/reportbot t.me/username</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()

    # Tangani format @username langsung
    if raw.startswith("@"):
        peer_str = raw[1:]
    else:
        peer_str = parse_channel_link(raw) or raw.strip("/").split("/")[-1]

    if not peer_str:
        await message.answer(
            "❌ Username/link tidak valid.\n"
            "Contoh: <code>@somebot</code> atau <code>t.me/somebot</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(peer_str=peer_str)

    await message.answer(
        f"🤖 <b>Target Report (User/Bot)</b>\n"
        f"📌 Target: <code>{peer_str}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=rb_kb_level1(),
    )


# ─────────────────────────────────────────────────────────────
# CALLBACK: Batal
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rb_cancel")
async def cb_rb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Proses report bot dibatalkan.")
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kembali
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rb_back_l1")
async def cb_rb_back_l1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.set_state(None)
    data = await state.get_data()
    peer_str = data.get("peer_str", "")
    await callback.message.edit_text(
        f"🤖 <b>Target Report (User/Bot)</b>\n"
        f"📌 Target: <code>{peer_str}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=rb_kb_level1(),
    )
    await callback.answer()


@router.callback_query(F.data == "rb_back_illegalgoods")
async def cb_rb_back_illegalgoods(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 <b>Illegal goods and services</b>\n\nPilih sub-kategori:",
        parse_mode="HTML",
        reply_markup=rb_kb_illegal_goods(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 1
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rb_r1_"))
async def cb_rb_level1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    if cb_data in OPTION_MAP and OPTION_MAP[cb_data][1] is False:
        option_key, _, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportbot", show_alert=True)
            await state.clear()
            return
        await callback.answer()
        await _do_execute_rb(
            peer_str=peer_str,
            option_key=option_key,
            comment="",
            admin_user_id=callback.from_user.id,
            label=label,
            target_msg=callback.message,
        )
        await state.clear()
        return

    if cb_data == "rb_r1_copyright":
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
            reply_markup=rb_kb_comment_optional(cb_data),
        )
        await state.set_state(ReportBotStates.waiting_comment)
        await callback.answer()
        return

    if cb_data in RB_L1_TO_KB:
        label = RB_L1_LABELS.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=RB_L1_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 2
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rb_r2_"))
async def cb_rb_level2(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    if cb_data in RB_L2_TO_KB:
        label_map = {
            "rb_r2_illegalgoods_weapons": "🔫 Weapons",
            "rb_r2_illegalgoods_drugs":   "💊 Drugs",
        }
        label = label_map.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=RB_L2_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportbot", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )

        if needs_comment == "req":
            await state.set_state(ReportBotStates.waiting_comment)
            await callback.message.edit_text(
                "💣 <b>Terrorism</b>\n\n✏️ <b>Tambah Komentar (Wajib)</b>\n\n"
                "Kirimkan teks komentar Anda:",
                parse_mode="HTML",
                reply_markup=rb_kb_comment_required(),
            )
        else:
            await state.set_state(ReportBotStates.waiting_comment)
            await callback.message.edit_text(
                f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):\n\n"
                "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
                parse_mode="HTML",
                reply_markup=rb_kb_comment_optional(cb_data),
            )

        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Level 3
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rb_r3_"))
async def cb_rb_level3(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    cb_data = callback.data

    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis, mulai ulang /reportbot", show_alert=True)
            await state.clear()
            return

        await state.update_data(
            selected_option=option_key,
            option_label=label,
            needs_comment=needs_comment,
        )
        await state.set_state(ReportBotStates.waiting_comment)
        await callback.message.edit_text(
            f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):\n\n"
            "<i>Kirim teks komentar atau tekan Kirim Tanpa Komentar</i>",
            parse_mode="HTML",
            reply_markup=rb_kb_comment_optional(cb_data),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Kirim Tanpa Komentar → langsung eksekusi
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rb_submit_nocomment_"))
async def cb_rb_submit_nocomment(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    data = await state.get_data()
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)

    if not peer_str or not option_key:
        await callback.answer("⚠️ Sesi habis, mulai ulang /reportbot", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    await _do_execute_rb(
        peer_str=peer_str,
        option_key=option_key,
        comment="",
        admin_user_id=callback.from_user.id,
        label=label,
        target_msg=callback.message,
    )
    await state.clear()


# ─────────────────────────────────────────────────────────────
# MESSAGE: Terima Komentar → langsung eksekusi
# ─────────────────────────────────────────────────────────────

@router.message(ReportBotStates.waiting_comment)
async def handle_rb_comment(message: Message, state: FSMContext) -> None:
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
        await message.answer("❌ Proses report bot dibatalkan.")
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
        await message.answer("⚠️ Sesi habis. Mulai ulang dengan /reportbot.")
        return

    status_msg = await message.answer("⏳ Memproses...", parse_mode="HTML")
    await state.clear()
    await _do_execute_rb(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=message.from_user.id,
        label=label,
        target_msg=status_msg,
    )
