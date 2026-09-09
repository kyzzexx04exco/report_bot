"""
bot/handlers/reactchan.py
Kirim reaction emoji ke pesan channel/group menggunakan semua sender aktif.

Flow:
  /reactchan <link_channel> <message_id>
    → pilih emoji
    → konfirmasi (tampil jumlah sender aktif)
    → eksekusi paralel semua sender
    → hasil summary

Atau bisa juga:
  /reactchan <link_channel> <message_id> <emoji>
    → langsung ke konfirmasi (skip pilih emoji)
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
from utils.parsers import parse_channel_link, parse_message_link
from utils.reaction_executor import run_reaction

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# ─────────────────────────────────────────────────────────────
# DAFTAR EMOJI YANG DIDUKUNG
# ─────────────────────────────────────────────────────────────

EMOJI_LIST = [
    ("👍", "rc_e_thumbsup"),
    ("👎", "rc_e_thumbsdown"),
    ("❤️", "rc_e_heart"),
    ("🔥", "rc_e_fire"),
    ("🥰", "rc_e_love"),
    ("👏", "rc_e_clap"),
    ("😁", "rc_e_grin"),
    ("🤔", "rc_e_think"),
    ("🤯", "rc_e_mindblown"),
    ("😱", "rc_e_scream"),
    ("🤬", "rc_e_angry"),
    ("😢", "rc_e_cry"),
    ("🎉", "rc_e_party"),
    ("🏆", "rc_e_trophy"),
    ("😍", "rc_e_heart_eyes"),
    ("🐳", "rc_e_whale"),
    ("💯", "rc_e_100"),
    ("🤣", "rc_e_rofl"),
    ("💩", "rc_e_poop"),
    ("🙏", "rc_e_pray"),
]

# Map callback_data → emoji
EMOJI_MAP = {data: emoji for emoji, data in EMOJI_LIST}


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


def rc_kb_emoji() -> InlineKeyboardMarkup:
    """Keyboard pilih emoji — 4 per baris."""
    b = InlineKeyboardBuilder()
    row = []
    for emoji, cb_data in EMOJI_LIST:
        row.append(InlineKeyboardButton(text=emoji, callback_data=cb_data))
        if len(row) == 4:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(InlineKeyboardButton(text="❌ Batal", callback_data="rc_cancel"))
    return b.as_markup()


def rc_kb_confirm(emoji: str, count: int) -> InlineKeyboardMarkup:
    """Keyboard konfirmasi sebelum eksekusi."""
    return _kb(
        [_btn(f"✅ Kirim {emoji} ke {count} sender", "rc_confirm")],
        [_btn("❌ Batal", "rc_cancel")],
    )


def rc_kb_repeat() -> InlineKeyboardMarkup:
    """Keyboard pilih berapa kali per sender."""
    return _kb(
        [_btn("1x", "rc_rep_1"),  _btn("2x", "rc_rep_2"),  _btn("3x", "rc_rep_3")],
        [_btn("5x", "rc_rep_5"),  _btn("10x", "rc_rep_10"), _btn("20x", "rc_rep_20")],
        [_btn("❌ Batal", "rc_cancel")],
    )


# ─────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────

class ReactChanStates(StatesGroup):
    waiting_emoji   = State()
    waiting_repeat  = State()
    waiting_confirm = State()


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def _show_repeat(target_msg, state: FSMContext, peer_str: str, message_id: int, emoji: str) -> None:
    """Tampilkan pilih berapa kali per sender."""
    await state.update_data(peer_str=peer_str, message_id=message_id, emoji=emoji)
    await target_msg.edit_text(
        f"😀 Emoji: <b>{emoji}</b>\n"
        f"🔗 Target: <code>{peer_str}</code>\n"
        f"📩 Message ID: <code>{message_id}</code>\n\n"
        "🔁 Berapa kali tiap sender react?",
        parse_mode="HTML",
        reply_markup=rc_kb_repeat(),
    )
    await state.set_state(ReactChanStates.waiting_repeat)


async def _show_confirm(target_msg, state: FSMContext) -> None:
    """Tampilkan konfirmasi dengan jumlah sender aktif."""
    data = await state.get_data()
    peer_str   = data.get("peer_str")
    message_id = data.get("message_id")
    emoji      = data.get("emoji")
    repeat     = data.get("repeat", 1)

    active_count = await db.count_active_senders()
    total_react  = active_count * repeat

    await target_msg.edit_text(
        f"😀 Emoji     : <b>{emoji}</b>\n"
        f"🔗 Target    : <code>{peer_str}</code>\n"
        f"📩 Message ID: <code>{message_id}</code>\n"
        f"🔁 Per sender: <b>{repeat}x</b>\n"
        f"👥 Sender    : <b>{active_count}</b>\n"
        f"📊 Total est.: <b>~{total_react} react</b>\n\n"
        "Lanjutkan?",
        parse_mode="HTML",
        reply_markup=rc_kb_confirm(emoji, active_count),
    )
    await state.set_state(ReactChanStates.waiting_confirm)


# ─────────────────────────────────────────────────────────────
# COMMAND: /reactchan <link> <msg_id> [emoji]
# ─────────────────────────────────────────────────────────────

@router.message(Command("reactchan"))
async def cmd_reactchan(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 2:
        await message.answer(
            "❌ Format salah.\n\n"
            "Gunakan:\n"
            "<code>/reactchan t.me/channel/123</code>\n"
            "<code>/reactchan t.me/channel/123 👍</code>\n"
            "<code>/reactchan t.me/channel 123</code>\n"
            "<code>/reactchan t.me/channel 123 👍</code>",
            parse_mode="HTML",
        )
        return

    raw_link = parts[1].strip()
    peer_str = None
    message_id = None
    emoji = None

    # ── MODE 1: Link pesan lengkap → t.me/channel/123 ──
    parsed = parse_message_link(raw_link)
    if parsed:
        peer_str, message_id = parsed
        # Emoji opsional di argumen ke-2
        if len(parts) >= 3:
            emoji = parts[2].strip()

    # ── MODE 2: Link channel + msg_id terpisah → t.me/channel 123 ──
    elif len(parts) >= 3:
        raw_msg_id = parts[2].strip()

        peer_str = parse_channel_link(raw_link)
        if not peer_str:
            if raw_link.startswith("@"):
                peer_str = raw_link[1:]
            else:
                await message.answer(
                    "❌ Link channel tidak valid.\n"
                    "Contoh: <code>t.me/somechannel/123</code>",
                    parse_mode="HTML",
                )
                return

        try:
            message_id = int(raw_msg_id)
            if message_id <= 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "❌ Message ID harus berupa angka positif.\n"
                "Contoh: <code>/reactchan t.me/channel 123</code>",
                parse_mode="HTML",
            )
            return

        # Emoji opsional di argumen ke-3
        if len(parts) == 4:
            emoji = parts[3].strip()

    else:
        # Hanya 1 argumen tapi bukan link pesan lengkap
        await message.answer(
            "❌ Format salah. Sertakan message ID.\n\n"
            "Contoh:\n"
            "<code>/reactchan t.me/channel/123</code>\n"
            "<code>/reactchan t.me/channel 123</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(peer_str=peer_str, message_id=message_id)

    # Kalau emoji langsung disertakan → langsung ke pilih repeat
    if emoji:
        msg = await message.answer(
            f"😀 Emoji: <b>{emoji}</b>\n"
            f"🔗 Target: <code>{peer_str}</code>\n"
            f"📩 Message ID: <code>{message_id}</code>\n\n"
            "🔁 Berapa kali tiap sender react?",
            parse_mode="HTML",
            reply_markup=rc_kb_repeat(),
        )
        await state.update_data(emoji=emoji, status_msg_id=msg.message_id)
        await state.set_state(ReactChanStates.waiting_repeat)
        return

    # Tampilkan pilih emoji
    msg = await message.answer(
        f"🎯 <b>React Channel/Group</b>\n"
        f"🔗 Target: <code>{peer_str}</code>\n"
        f"📩 Message ID: <code>{message_id}</code>\n\n"
        "😀 Pilih emoji:",
        parse_mode="HTML",
        reply_markup=rc_kb_emoji(),
    )
    await state.update_data(status_msg_id=msg.message_id)
    await state.set_state(ReactChanStates.waiting_emoji)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Batal
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rc_cancel")
async def cb_rc_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Proses react dibatalkan.")
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# CALLBACK: Pilih Emoji
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rc_e_"))
async def cb_rc_emoji(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    emoji = EMOJI_MAP.get(callback.data)
    if not emoji:
        await callback.answer("⚠️ Emoji tidak dikenal.", show_alert=True)
        return

    data = await state.get_data()
    peer_str   = data.get("peer_str")
    message_id = data.get("message_id")

    await callback.answer(f"Dipilih: {emoji}")
    await _show_repeat(callback.message, state, peer_str, message_id, emoji)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Pilih Repeat
# ─────────────────────────────────────────────────────────────

REPEAT_MAP = {
    "rc_rep_1":  1,
    "rc_rep_2":  2,
    "rc_rep_3":  3,
    "rc_rep_5":  5,
    "rc_rep_10": 10,
    "rc_rep_20": 20,
}

@router.callback_query(F.data.startswith("rc_rep_"))
async def cb_rc_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    repeat = REPEAT_MAP.get(callback.data)
    if not repeat:
        await callback.answer("⚠️ Opsi tidak dikenal.", show_alert=True)
        return

    await state.update_data(repeat=repeat)
    await callback.answer(f"Per sender: {repeat}x")
    await _show_confirm(callback.message, state)


# ─────────────────────────────────────────────────────────────
# CALLBACK: Konfirmasi → Eksekusi
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rc_confirm")
async def cb_rc_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("⛔ Tidak berhak.", show_alert=True)
        return

    data = await state.get_data()
    peer_str   = data.get("peer_str")
    message_id = data.get("message_id")
    emoji      = data.get("emoji")
    repeat     = data.get("repeat", 1)

    if not peer_str or not message_id or not emoji:
        await callback.answer("⚠️ Sesi habis, mulai ulang /reactchan", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    await callback.message.edit_text(
        f"⏳ Mengirim reaction <b>{emoji}</b>...\n"
        f"🔗 Target: <code>{peer_str}</code>\n"
        f"📩 Message ID: <code>{message_id}</code>\n"
        f"🔁 Per sender: <b>{repeat}x</b>\n\n"
        "Mohon tunggu...",
        parse_mode="HTML",
    )

    result = await run_reaction(
        peer_str=peer_str,
        message_id=message_id,
        emoji=emoji,
        admin_user_id=callback.from_user.id,
        repeat=repeat,
        max_concurrent=10,
    )

    await callback.message.edit_text(result["message"], parse_mode="HTML")
    await state.clear()
