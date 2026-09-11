"""
bot/handlers/reportpriv.py
Report channel/group PRIVATE (t.me/+ atau t.me/joinchat/).

Fix v2:
- Setelah sender join via invite link, resolve chat ID dulu
  sebelum dipakai buat report (invite link tidak bisa di-resolve_peer)
- FSM conflict fix: command apapun saat waiting_comment → clear state dulu
"""

import logging
import os
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from utils import pyro_client
from utils.report_executor import run_report_peer

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

_PRIVATE_INVITE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:joinchat|\+)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def parse_private_link(text: str):
    text = text.strip()
    m = _PRIVATE_INVITE.search(text)
    if m:
        raw_hash = m.group(1)
        full_link = f"https://t.me/+{raw_hash}"
        return raw_hash, full_link
    return None


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ─────────────────────────────────────────────────────────────
# RESOLVE PEER SETELAH JOIN — fix utama reportpriv
# ─────────────────────────────────────────────────────────────

async def resolve_peer_after_join(invite_link: str) -> str | None:
    """
    Invite link (t.me/+xxx) tidak bisa langsung di-resolve_peer.
    Solusi: join dulu via 1 sender, lalu get_chat() untuk dapat username/ID,
    kemudian pakai itu sebagai peer_str untuk semua operasi selanjutnya.
    Return peer_str yang valid (username atau -100ID), atau None jika gagal.
    """
    all_senders = await db.get_all_senders()
    for s in all_senders:
        if not s.get("is_active"):
            continue
        client = pyro_client.get_client(s["phone_number"])
        if client is None:
            continue
        try:
            # Join dulu
            chat = await client.join_chat(invite_link)
            if chat:
                # Kalau ada username, pakai itu — lebih reliable
                if hasattr(chat, "username") and chat.username:
                    logger.info(f"[RESOLVE] Resolved ke @{chat.username}")
                    return chat.username
                # Kalau tidak ada username, pakai numeric ID
                if hasattr(chat, "id") and chat.id:
                    peer_id = str(chat.id)
                    if not peer_id.startswith("-100"):
                        peer_id = f"-100{abs(chat.id)}"
                    logger.info(f"[RESOLVE] Resolved ke {peer_id}")
                    return peer_id
        except Exception as e:
            logger.warning(f"[RESOLVE] Gagal join/resolve via {s['phone_number']}: {e}")
            continue
    return None


# ─────────────────────────────────────────────────────────────
# OPTION MAP
# ─────────────────────────────────────────────────────────────

OPTION_MAP = {
    "rp_r1_idontlikeit":               ("other",         False,  "👎 I don't like it"),
    "rp_r2_childabuse_sexualabuse":    ("child_abuse",   "opt",  "👶 Child sexual abuse"),
    "rp_r2_childabuse_physicalabuse":  ("child_abuse",   "opt",  "👶 Child physical abuse"),
    "rp_r2_violence_insults":          ("violence",      "opt",  "🗣️ Insults or false information"),
    "rp_r2_violence_graphic":          ("violence",      "opt",  "😱 Graphic or disturbing content"),
    "rp_r2_violence_extreme":          ("violence",      "opt",  "💀 Extreme violence, dismemberment"),
    "rp_r2_violence_hatespeech":       ("violence",      "opt",  "🤬 Hate speech or symbols"),
    "rp_r2_violence_callingviolence":  ("violence",      "opt",  "⚔️ Calling for violence"),
    "rp_r2_violence_organizedcrime":   ("violence",      "opt",  "🕵️ Organized crime"),
    "rp_r2_violence_terrorism":        ("violence",      "req",  "💣 Terrorism"),
    "rp_r2_violence_animalabuse":      ("violence",      "opt",  "🐾 Animal abuse"),
    "rp_r2_illegalgoods_fakedocs":     ("other",         "opt",  "📄 Fake documents"),
    "rp_r2_illegalgoods_counterfeit":  ("other",         "opt",  "💵 Counterfeit money"),
    "rp_r2_illegalgoods_hacking":      ("other",         "opt",  "💻 Hacking tools and malware"),
    "rp_r2_illegalgoods_countermerch": ("other",         "opt",  "👜 Counterfeit merchandise"),
    "rp_r2_illegalgoods_other":        ("other",         "opt",  "📦 Other goods and services"),
    "rp_r3_weapons_firearms":          ("other",         "opt",  "🔫 Firearms and accessories"),
    "rp_r3_weapons_melee":             ("other",         "opt",  "🗡️ Melee weapons"),
    "rp_r3_weapons_nonlethal":         ("other",         "opt",  "⚡ Non-lethal weapons"),
    "rp_r3_weapons_other":             ("other",         "opt",  "❓ Other weapons"),
    "rp_r3_drugs_nicotine":            ("other",         "opt",  "🚬 Nicotine products"),
    "rp_r3_drugs_illegaldrugs":        ("other",         "opt",  "💊 Illegal drugs"),
    "rp_r3_drugs_other":               ("other",         "opt",  "❓ Other drugs"),
    "rp_r2_illegaladult_childabuse":   ("child_abuse",   "opt",  "👶 Child abuse"),
    "rp_r2_illegaladult_sexualservices":("porn",         "opt",  "🚫 Illegal sexual services"),
    "rp_r2_illegaladult_animalabuse":  ("violence",      "opt",  "🐾 Animal abuse"),
    "rp_r2_illegaladult_nonconsensual":("porn",          "opt",  "📸 Non-consensual sexual imagery"),
    "rp_r2_illegaladult_pornography":  ("porn",          "opt",  "🔞 Pornography"),
    "rp_r2_illegaladult_other":        ("porn",          "opt",  "❓ Other illegal sexual content"),
    "rp_r2_personaldata_privateimages":("personal_info", "opt",  "🖼️ Private images"),
    "rp_r2_personaldata_phonenumber":  ("personal_info", "opt",  "📱 Phone number"),
    "rp_r2_personaldata_address":      ("personal_info", "opt",  "🏠 Address"),
    "rp_r2_personaldata_stolendata":   ("personal_info", "opt",  "🔑 Stolen data or credentials"),
    "rp_r2_personaldata_other":        ("personal_info", "opt",  "❓ Other personal information"),
    "rp_r2_scamfraud_impersonation":   ("fake",          "opt",  "🎭 Impersonation"),
    "rp_r2_scamfraud_financial":       ("fake",          "opt",  "💸 Deceptive financial claims"),
    "rp_r2_scamfraud_malware":         ("fake",          "opt",  "🎣 Malware, phishing"),
    "rp_r2_scamfraud_fraudseller":     ("fake",          "opt",  "🛍️ Fraudulent seller"),
    "rp_r2_spam_insults":              ("spam",          "opt",  "🗣️ Insults or false information"),
    "rp_r2_spam_illegalcontent":       ("spam",          "opt",  "🚫 Promoting illegal content"),
    "rp_r2_spam_othercontent":         ("spam",          "opt",  "📢 Promoting other content"),
    "rp_r1_copyright":                 ("copyright",     "opt",  "©️ Copyright"),
}


# ─────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────

def _kb(*rows) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*row)
    return b.as_markup()

def _btn(text, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)

def rp_kb_level1() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👎 I don't like it",           "rp_r1_idontlikeit")],
        [_btn("👶 Child abuse",                "rp_r1_childabuse")],
        [_btn("💥 Violence",                   "rp_r1_violence")],
        [_btn("🚫 Illegal goods and services", "rp_r1_illegalgoods")],
        [_btn("🔞 Illegal adult content",      "rp_r1_illegaladult")],
        [_btn("🛡️ Personal data",              "rp_r1_personaldata")],
        [_btn("💰 Scam or fraud",              "rp_r1_scamfraud")],
        [_btn("📢 Spam",                       "rp_r1_spam")],
        [_btn("©️ Copyright",                  "rp_r1_copyright")],
        [_btn("❌ Batal",                      "rp_cancel")],
    )

def rp_kb_child_abuse() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child sexual abuse",   "rp_r2_childabuse_sexualabuse")],
        [_btn("👶 Child physical abuse", "rp_r2_childabuse_physicalabuse")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_violence() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information",   "rp_r2_violence_insults")],
        [_btn("😱 Graphic or disturbing content",  "rp_r2_violence_graphic")],
        [_btn("💀 Extreme violence, dismemberment","rp_r2_violence_extreme")],
        [_btn("🤬 Hate speech or symbols",         "rp_r2_violence_hatespeech")],
        [_btn("⚔️ Calling for violence",           "rp_r2_violence_callingviolence")],
        [_btn("🕵️ Organized crime",                "rp_r2_violence_organizedcrime")],
        [_btn("💣 Terrorism",                      "rp_r2_violence_terrorism")],
        [_btn("🐾 Animal abuse",                   "rp_r2_violence_animalabuse")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_illegal_goods() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Weapons",                 "rp_r2_illegalgoods_weapons")],
        [_btn("💊 Drugs",                   "rp_r2_illegalgoods_drugs")],
        [_btn("📄 Fake documents",          "rp_r2_illegalgoods_fakedocs")],
        [_btn("💵 Counterfeit money",       "rp_r2_illegalgoods_counterfeit")],
        [_btn("💻 Hacking tools/malware",   "rp_r2_illegalgoods_hacking")],
        [_btn("👜 Counterfeit merchandise", "rp_r2_illegalgoods_countermerch")],
        [_btn("📦 Other goods/services",    "rp_r2_illegalgoods_other")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_weapons() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🔫 Firearms and accessories", "rp_r3_weapons_firearms")],
        [_btn("🗡️ Melee weapons",            "rp_r3_weapons_melee")],
        [_btn("⚡ Non-lethal weapons",        "rp_r3_weapons_nonlethal")],
        [_btn("❓ Other weapons",            "rp_r3_weapons_other")],
        [_btn("◀️ Kembali", "rp_back_illegalgoods"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_drugs() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🚬 Nicotine products", "rp_r3_drugs_nicotine")],
        [_btn("💊 Illegal drugs",     "rp_r3_drugs_illegaldrugs")],
        [_btn("❓ Other drugs",       "rp_r3_drugs_other")],
        [_btn("◀️ Kembali", "rp_back_illegalgoods"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_illegal_adult() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("👶 Child abuse",                   "rp_r2_illegaladult_childabuse")],
        [_btn("🚫 Illegal sexual services",       "rp_r2_illegaladult_sexualservices")],
        [_btn("🐾 Animal abuse",                  "rp_r2_illegaladult_animalabuse")],
        [_btn("📸 Non-consensual sexual imagery", "rp_r2_illegaladult_nonconsensual")],
        [_btn("🔞 Pornography",                   "rp_r2_illegaladult_pornography")],
        [_btn("❓ Other illegal sexual content",  "rp_r2_illegaladult_other")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_personal_data() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🖼️ Private images",          "rp_r2_personaldata_privateimages")],
        [_btn("📱 Phone number",            "rp_r2_personaldata_phonenumber")],
        [_btn("🏠 Address",                 "rp_r2_personaldata_address")],
        [_btn("🔑 Stolen data/credentials","rp_r2_personaldata_stolendata")],
        [_btn("❓ Other personal info",     "rp_r2_personaldata_other")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_scam_fraud() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🎭 Impersonation",              "rp_r2_scamfraud_impersonation")],
        [_btn("💸 Deceptive financial claims", "rp_r2_scamfraud_financial")],
        [_btn("🎣 Malware, phishing",          "rp_r2_scamfraud_malware")],
        [_btn("🛍️ Fraudulent seller",         "rp_r2_scamfraud_fraudseller")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_spam() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🗣️ Insults or false information", "rp_r2_spam_insults")],
        [_btn("🚫 Promoting illegal content",    "rp_r2_spam_illegalcontent")],
        [_btn("📢 Promoting other content",      "rp_r2_spam_othercontent")],
        [_btn("◀️ Kembali", "rp_back_l1"), _btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_comment_optional(cb_data: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Kirim Tanpa Komentar", f"rp_submit_nocomment_{cb_data}")],
        [_btn("❌ Batal", "rp_cancel")],
    )

def rp_kb_comment_required() -> InlineKeyboardMarkup:
    return _kb([_btn("❌ Batal", "rp_cancel")])

def rp_kb_select_messages() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("📨 All Messages", "rp_select_all_messages")],
        [_btn("❌ Batal", "rp_cancel")],
    )

RP_L1_TO_KB = {
    "rp_r1_childabuse":   rp_kb_child_abuse,
    "rp_r1_violence":     rp_kb_violence,
    "rp_r1_illegalgoods": rp_kb_illegal_goods,
    "rp_r1_illegaladult": rp_kb_illegal_adult,
    "rp_r1_personaldata": rp_kb_personal_data,
    "rp_r1_scamfraud":    rp_kb_scam_fraud,
    "rp_r1_spam":         rp_kb_spam,
}

RP_L2_TO_KB = {
    "rp_r2_illegalgoods_weapons": rp_kb_weapons,
    "rp_r2_illegalgoods_drugs":   rp_kb_drugs,
}

RP_L1_LABELS = {
    "rp_r1_childabuse":   "👶 Child abuse",
    "rp_r1_violence":     "💥 Violence",
    "rp_r1_illegalgoods": "🚫 Illegal goods and services",
    "rp_r1_illegaladult": "🔞 Illegal adult content",
    "rp_r1_personaldata": "🛡️ Personal data",
    "rp_r1_scamfraud":    "💰 Scam or fraud",
    "rp_r1_spam":         "📢 Spam",
}


# ─────────────────────────────────────────────────────────────
# FSM
# ─────────────────────────────────────────────────────────────

class ReportPrivStates(StatesGroup):
    waiting_comment         = State()
    waiting_select_messages = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

async def _show_select_messages(target_msg, state: FSMContext, label: str, comment: str = "") -> None:
    comment_line = f"\n💬 Komentar: <i>{comment}</i>" if comment else ""
    await target_msg.edit_text(
        f"📋 Alasan: <b>{label}</b>{comment_line}\n\n"
        "📨 Pilih pesan yang ingin dilaporkan:",
        parse_mode="HTML",
        reply_markup=rp_kb_select_messages(),
    )
    await state.set_state(ReportPrivStates.waiting_select_messages)


async def _do_execute_rp(
    peer_str: str,
    option_key: str,
    comment: str,
    admin_user_id: int,
    label: str,
    target_msg,
) -> None:
    repeat = await db.get_report_count(admin_user_id)
    await target_msg.edit_text(
        f"⏳ Mengirim laporan ke channel/group private...\n"
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
# COMMAND /reportpriv — clear FSM dulu biar tidak konflik
# ─────────────────────────────────────────────────────────────

@router.message(Command("reportpriv"))
async def cmd_reportpriv(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    # Clear FSM state apapun yang aktif sebelumnya
    await state.clear()

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n"
            "Gunakan: <code>/reportpriv t.me/+XXXXXXXX</code>",
            parse_mode="HTML",
        )
        return

    raw_link = parts[1].strip()
    parsed = parse_private_link(raw_link)
    if not parsed:
        await message.answer(
            "❌ Link invite private tidak valid.\n"
            "Contoh: <code>t.me/+AbCdEfGhIjK</code> atau <code>t.me/joinchat/AbCdEfGhIjK</code>",
            parse_mode="HTML",
        )
        return

    invite_hash, full_link = parsed

    # Resolve peer sekarang — join dulu via 1 sender, dapat chat ID/username
    resolving_msg = await message.answer(
        f"🔒 <b>Target Report (Private Channel/Group)</b>\n"
        f"📌 Link: <code>{full_link}</code>\n\n"
        "⏳ Resolving peer...",
        parse_mode="HTML",
    )

    resolved_peer = await resolve_peer_after_join(full_link)

    if not resolved_peer:
        await resolving_msg.edit_text(
            "❌ Gagal resolve peer. Kemungkinan:\n"
            "- Link invite sudah expired\n"
            "- Tidak ada sender aktif\n"
            "- Channel/group sudah tidak ada",
            parse_mode="HTML",
        )
        return

    # Simpan peer_str yang sudah resolved (username atau -100ID)
    await state.update_data(peer_str=resolved_peer, invite_link=full_link)

    await resolving_msg.edit_text(
        f"🔒 <b>Target Report (Private Channel/Group)</b>\n"
        f"📌 Link: <code>{full_link}</code>\n"
        f"✅ Peer: <code>{resolved_peer}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=rp_kb_level1(),
    )


# ─────────────────────────────────────────────────────────────
# CALLBACKS — sama persis, tidak berubah
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rp_cancel")
async def cb_rp_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Proses report private dibatalkan.")
    await callback.answer()


@router.callback_query(F.data == "rp_back_l1")
async def cb_rp_back_l1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await state.set_state(None)
    data = await state.get_data()
    peer_str = data.get("peer_str", "")
    invite_link = data.get("invite_link", peer_str)
    await callback.message.edit_text(
        f"🔒 <b>Target Report (Private Channel/Group)</b>\n"
        f"📌 Link: <code>{invite_link}</code>\n"
        f"✅ Peer: <code>{peer_str}</code>\n\n"
        "Pilih <b>kategori laporan</b>:",
        parse_mode="HTML",
        reply_markup=rp_kb_level1(),
    )
    await callback.answer()


@router.callback_query(F.data == "rp_back_illegalgoods")
async def cb_rp_back_illegalgoods(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 <b>Illegal goods and services</b>\n\nPilih sub-kategori:",
        parse_mode="HTML",
        reply_markup=rp_kb_illegal_goods(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rp_r1_"))
async def cb_rp_level1(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    cb_data = callback.data

    if cb_data in OPTION_MAP and OPTION_MAP[cb_data][1] is False:
        option_key, _, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        peer_str = data.get("peer_str")
        if not peer_str:
            await callback.answer("⚠️ Sesi habis.", show_alert=True)
            await state.clear()
            return
        await state.update_data(selected_option=option_key, option_label=label, pending_comment="")
        await callback.answer()
        await _show_select_messages(callback.message, state, label)
        return

    if cb_data == "rp_r1_copyright":
        _, _, label = OPTION_MAP[cb_data]
        await state.update_data(selected_option="copyright", option_label=label, needs_comment="opt")
        await state.set_state(ReportPrivStates.waiting_comment)
        await callback.message.edit_text(
            "©️ <b>Copyright</b>\n\nTambah komentar (opsional):",
            parse_mode="HTML",
            reply_markup=rp_kb_comment_optional(cb_data),
        )
        await callback.answer()
        return

    if cb_data in RP_L1_TO_KB:
        label = RP_L1_LABELS.get(cb_data, cb_data)
        await callback.message.edit_text(
            f"{label}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=RP_L1_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


@router.callback_query(F.data.startswith("rp_r2_"))
async def cb_rp_level2(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    cb_data = callback.data

    if cb_data in RP_L2_TO_KB:
        labels = {"rp_r2_illegalgoods_weapons": "🔫 Weapons", "rp_r2_illegalgoods_drugs": "💊 Drugs"}
        await callback.message.edit_text(
            f"{labels.get(cb_data, cb_data)}\n\nPilih sub-kategori:",
            parse_mode="HTML",
            reply_markup=RP_L2_TO_KB[cb_data](),
        )
        await callback.answer()
        return

    if cb_data in OPTION_MAP:
        option_key, needs_comment, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        if not data.get("peer_str"):
            await callback.answer("⚠️ Sesi habis.", show_alert=True)
            await state.clear()
            return
        await state.update_data(selected_option=option_key, option_label=label, needs_comment=needs_comment)
        await state.set_state(ReportPrivStates.waiting_comment)
        if needs_comment == "req":
            await callback.message.edit_text(
                f"<b>{label}</b>\n\n✏️ Komentar <b>wajib</b>:",
                parse_mode="HTML",
                reply_markup=rp_kb_comment_required(),
            )
        else:
            await callback.message.edit_text(
                f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):",
                parse_mode="HTML",
                reply_markup=rp_kb_comment_optional(cb_data),
            )
        await callback.answer()
        return

    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


@router.callback_query(F.data.startswith("rp_r3_"))
async def cb_rp_level3(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    cb_data = callback.data
    if cb_data in OPTION_MAP:
        option_key, _, label = OPTION_MAP[cb_data]
        data = await state.get_data()
        if not data.get("peer_str"):
            await callback.answer("⚠️ Sesi habis.", show_alert=True)
            await state.clear()
            return
        await state.update_data(selected_option=option_key, option_label=label, needs_comment="opt")
        await state.set_state(ReportPrivStates.waiting_comment)
        await callback.message.edit_text(
            f"<b>{label}</b>\n\n✏️ Tambah komentar (opsional):",
            parse_mode="HTML",
            reply_markup=rp_kb_comment_optional(cb_data),
        )
        await callback.answer()
        return
    await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)


@router.callback_query(F.data.startswith("rp_submit_nocomment_"))
async def cb_rp_submit_nocomment(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    data = await state.get_data()
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)
    if not peer_str or not option_key:
        await callback.answer("⚠️ Sesi habis.", show_alert=True)
        await state.clear()
        return
    await state.update_data(pending_comment="")
    await callback.answer()
    await _show_select_messages(callback.message, state, label, comment="")


@router.message(ReportPrivStates.waiting_comment)
async def handle_rp_comment(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return
    if message.text and message.text.strip().startswith("/"):
        # Command masuk saat waiting_comment → clear state, proses command normal
        await state.clear()
        await message.answer(
            "⚠️ Sesi report dibatalkan karena ada command baru.\n"
            "Silakan ulangi command yang kamu kirim.",
        )
        return
    comment = message.text.strip() if message.text else ""
    data = await state.get_data()
    if data.get("needs_comment") == "req" and not comment:
        await message.answer("❌ Komentar tidak boleh kosong.")
        return
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)
    if not peer_str or not option_key:
        await state.clear()
        await message.answer("⚠️ Sesi habis. Mulai ulang dengan /reportpriv.")
        return
    await state.update_data(pending_comment=comment)
    status_msg = await message.answer(
        f"📋 Alasan: <b>{label}</b>\n"
        f"💬 Komentar: <i>{comment}</i>\n\n"
        "📨 Pilih pesan yang ingin dilaporkan:",
        parse_mode="HTML",
        reply_markup=rp_kb_select_messages(),
    )
    await state.update_data(status_msg_id=status_msg.message_id)
    await state.set_state(ReportPrivStates.waiting_select_messages)


@router.callback_query(F.data == "rp_select_all_messages")
async def cb_rp_select_all_messages(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    data = await state.get_data()
    peer_str = data.get("peer_str")
    option_key = data.get("selected_option")
    label = data.get("option_label", option_key)
    comment = data.get("pending_comment", "")
    if not peer_str or not option_key:
        await callback.answer("⚠️ Sesi habis.", show_alert=True)
        await state.clear()
        return
    await callback.answer()
    await _do_execute_rp(
        peer_str=peer_str,
        option_key=option_key,
        comment=comment,
        admin_user_id=callback.from_user.id,
        label=label,
        target_msg=callback.message,
    )
    await state.clear()
