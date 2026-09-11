"""
bot/handlers/warmup.py
Command /warmup — warmup semua sender aktif biar trust score naik.
"""

import logging
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from utils.warmup import run_warmup

logger = logging.getLogger(__name__)
router = Router()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


@router.message(Command("warmup"))
async def cmd_warmup(message: Message) -> None:
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Anda tidak berhak menggunakan bot ini.")
        return

    status = await message.answer(
        "⏳ <b>Warmup sender dimulai...</b>\n\n"
        "<i>Proses ini butuh beberapa menit tergantung jumlah sender. "
        "Sender akan join channel populer dan baca history untuk meningkatkan trust score.</i>",
        parse_mode="HTML",
    )

    result = await run_warmup(admin_user_id=message.from_user.id)
    await status.edit_text(result["message"], parse_mode="HTML")
