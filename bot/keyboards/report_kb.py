"""
bot/keyboards/report_kb.py
Keyboard opsi report — dibangun dari REPORT_OPTIONS di report_executor.
Label persis sama seperti Telegram, urutan sama.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.report_executor import REPORT_OPTIONS


def get_report_keyboard() -> InlineKeyboardMarkup:
    """Keyboard opsi report — satu tombol per baris, sama seperti Telegram."""
    builder = InlineKeyboardBuilder()
    for label, key, _, _ in REPORT_OPTIONS:
        builder.row(
            InlineKeyboardButton(text=label, callback_data=f"ropt_{key}")
        )
    builder.row(
        InlineKeyboardButton(text="Cancel", callback_data="ropt_cancel")
    )
    return builder.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Cancel", callback_data="ropt_cancel")
    )
    return builder.as_markup()
