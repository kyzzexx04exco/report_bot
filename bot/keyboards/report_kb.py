"""
bot/keyboards/report_kb.py
Inline keyboard dengan opsi-opsi report persis seperti di aplikasi Telegram.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_report_keyboard() -> InlineKeyboardMarkup:
    """
    Buat inline keyboard berisi semua opsi laporan Telegram.
    Setiap tombol membawa callback_data yang akan ditangkap di handler.
    """
    builder = InlineKeyboardBuilder()

    buttons = [
        ("🔞 Spam",                              "report_spam"),
        ("🔞 Pornografi",                         "report_porn"),
        ("💥 Kekerasan",                          "report_violence"),
        ("👶 Eksploitasi Anak",                   "report_child_abuse"),
        ("🤬 Pelecehan / Ujaran Kebencian",       "report_harassment"),
        ("👤 Akun Palsu / Impersonasi",           "report_fake"),
        ("📄 Hak Cipta / Pelanggaran HKI",        "report_copyright"),
        ("💰 Penipuan / Barang & Jasa Palsu",     "report_fraud"),
        ("🛡️ Informasi Pribadi",                  "report_personal_info"),
        ("❓ Lainnya",                             "report_other"),
    ]

    for label, callback_data in buttons:
        builder.row(
            InlineKeyboardButton(text=label, callback_data=callback_data)
        )

    # Tombol Batal di bagian paling bawah
    builder.row(
        InlineKeyboardButton(text="❌ Batal", callback_data="report_cancel")
    )

    return builder.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Keyboard hanya berisi tombol Batal (dipakai saat menunggu input komentar)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Batal", callback_data="report_cancel")
    )
    return builder.as_markup()
