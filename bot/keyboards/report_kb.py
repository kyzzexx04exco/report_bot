"""
bot/keyboards/report_kb.py
Inline keyboard untuk semua level opsi report Telegram.
Mengikuti struktur menu report Telegram versi terbaru 2026.

Sistem 2-3 level:
  Level 1 → Kategori utama (9 opsi)
  Level 2 → Sub-kategori
  Level 3 → Sub-sub-kategori (Weapons, Drugs)

Callback data format:
  L1: "r1_<key>"           contoh: "r1_spam"
  L2: "r2_<parent>_<key>"  contoh: "r2_violence_terrorism"
  L3: "r3_<parent>_<key>"  contoh: "r3_weapons_firearms"
  Batal: "r_cancel"
  Kirim (dengan/tanpa komentar): ditangani di handler
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ─────────────────────────────────────────────────────────────
# TOMBOL BATAL & KEMBALI
# ─────────────────────────────────────────────────────────────

BTN_CANCEL = InlineKeyboardButton(text="❌ Batal", callback_data="r_cancel")
BTN_BACK_L1 = InlineKeyboardButton(text="◀️ Kembali", callback_data="r_back_l1")


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Helper: buat InlineKeyboardMarkup dari list of rows."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.row(*row)
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# LEVEL 1 — Kategori Utama
# ─────────────────────────────────────────────────────────────

def kb_level1() -> InlineKeyboardMarkup:
    """
    9 tombol kategori utama report Telegram.
    Opsi 1 (I don't like it) langsung eksekusi.
    Opsi lain masuk ke level 2.
    """
    return _kb(
        [InlineKeyboardButton(
            text="👎 I don't like it",
            callback_data="r1_idontlikeit"
        )],
        [InlineKeyboardButton(
            text="👶 Child abuse",
            callback_data="r1_childabuse"
        )],
        [InlineKeyboardButton(
            text="💥 Violence",
            callback_data="r1_violence"
        )],
        [InlineKeyboardButton(
            text="🚫 Illegal goods and services",
            callback_data="r1_illegalgoods"
        )],
        [InlineKeyboardButton(
            text="🔞 Illegal adult content",
            callback_data="r1_illegaladult"
        )],
        [InlineKeyboardButton(
            text="🛡️ Personal data",
            callback_data="r1_personaldata"
        )],
        [InlineKeyboardButton(
            text="💰 Scam or fraud",
            callback_data="r1_scamfraud"
        )],
        [InlineKeyboardButton(
            text="📢 Spam",
            callback_data="r1_spam"
        )],
        [InlineKeyboardButton(
            text="©️ Copyright",
            callback_data="r1_copyright"
        )],
        [BTN_CANCEL],
    )


# ─────────────────────────────────────────────────────────────
# LEVEL 2 — Sub-kategori per Kategori Utama
# ─────────────────────────────────────────────────────────────

def kb_child_abuse() -> InlineKeyboardMarkup:
    """Sub-opsi Child abuse."""
    return _kb(
        [InlineKeyboardButton(
            text="🔴 Child sexual abuse",
            callback_data="r2_childabuse_sexualabuse"
        )],
        [InlineKeyboardButton(
            text="🔴 Child physical abuse",
            callback_data="r2_childabuse_physicalabuse"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_violence() -> InlineKeyboardMarkup:
    """Sub-opsi Violence."""
    return _kb(
        [InlineKeyboardButton(
            text="🗣️ Insults or false information",
            callback_data="r2_violence_insults"
        )],
        [InlineKeyboardButton(
            text="😱 Graphic or disturbing content",
            callback_data="r2_violence_graphic"
        )],
        [InlineKeyboardButton(
            text="💀 Extreme violence, dismemberment",
            callback_data="r2_violence_extreme"
        )],
        [InlineKeyboardButton(
            text="🤬 Hate speech or symbols",
            callback_data="r2_violence_hatespeech"
        )],
        [InlineKeyboardButton(
            text="⚔️ Calling for violence",
            callback_data="r2_violence_callingviolence"
        )],
        [InlineKeyboardButton(
            text="🕵️ Organized crime",
            callback_data="r2_violence_organizedcrime"
        )],
        [InlineKeyboardButton(
            text="💣 Terrorism",
            callback_data="r2_violence_terrorism"
        )],
        [InlineKeyboardButton(
            text="🐾 Animal abuse",
            callback_data="r2_violence_animalabuse"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_illegal_goods() -> InlineKeyboardMarkup:
    """Sub-opsi Illegal goods and services."""
    return _kb(
        [InlineKeyboardButton(
            text="🔫 Weapons",
            callback_data="r2_illegalgoods_weapons"
        )],
        [InlineKeyboardButton(
            text="💊 Drugs",
            callback_data="r2_illegalgoods_drugs"
        )],
        [InlineKeyboardButton(
            text="📄 Fake documents",
            callback_data="r2_illegalgoods_fakedocs"
        )],
        [InlineKeyboardButton(
            text="💵 Counterfeit money",
            callback_data="r2_illegalgoods_counterfeit"
        )],
        [InlineKeyboardButton(
            text="💻 Hacking tools and malware",
            callback_data="r2_illegalgoods_hacking"
        )],
        [InlineKeyboardButton(
            text="👜 Counterfeit merchandise",
            callback_data="r2_illegalgoods_countermerch"
        )],
        [InlineKeyboardButton(
            text="📦 Other goods and services",
            callback_data="r2_illegalgoods_other"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_illegal_adult() -> InlineKeyboardMarkup:
    """Sub-opsi Illegal adult content."""
    return _kb(
        [InlineKeyboardButton(
            text="👶 Child abuse",
            callback_data="r2_illegaladult_childabuse"
        )],
        [InlineKeyboardButton(
            text="🚫 Illegal sexual services",
            callback_data="r2_illegaladult_sexualservices"
        )],
        [InlineKeyboardButton(
            text="🐾 Animal abuse",
            callback_data="r2_illegaladult_animalabuse"
        )],
        [InlineKeyboardButton(
            text="📸 Non-consensual sexual imagery",
            callback_data="r2_illegaladult_nonconsensual"
        )],
        [InlineKeyboardButton(
            text="🔞 Pornography",
            callback_data="r2_illegaladult_pornography"
        )],
        [InlineKeyboardButton(
            text="❓ Other illegal sexual content",
            callback_data="r2_illegaladult_other"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_personal_data() -> InlineKeyboardMarkup:
    """Sub-opsi Personal data."""
    return _kb(
        [InlineKeyboardButton(
            text="🖼️ Private images",
            callback_data="r2_personaldata_privateimages"
        )],
        [InlineKeyboardButton(
            text="📱 Phone number",
            callback_data="r2_personaldata_phonenumber"
        )],
        [InlineKeyboardButton(
            text="🏠 Address",
            callback_data="r2_personaldata_address"
        )],
        [InlineKeyboardButton(
            text="🔑 Stolen data or credentials",
            callback_data="r2_personaldata_stolendata"
        )],
        [InlineKeyboardButton(
            text="❓ Other personal information",
            callback_data="r2_personaldata_other"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_scam_fraud() -> InlineKeyboardMarkup:
    """Sub-opsi Scam or fraud."""
    return _kb(
        [InlineKeyboardButton(
            text="🎭 Impersonation",
            callback_data="r2_scamfraud_impersonation"
        )],
        [InlineKeyboardButton(
            text="💸 Deceptive or unrealistic financial claims",
            callback_data="r2_scamfraud_financial"
        )],
        [InlineKeyboardButton(
            text="🎣 Malware, phishing",
            callback_data="r2_scamfraud_malware"
        )],
        [InlineKeyboardButton(
            text="🛍️ Fraudulent seller, product or service",
            callback_data="r2_scamfraud_fraudseller"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


def kb_spam() -> InlineKeyboardMarkup:
    """Sub-opsi Spam."""
    return _kb(
        [InlineKeyboardButton(
            text="🗣️ Insults or false information",
            callback_data="r2_spam_insults"
        )],
        [InlineKeyboardButton(
            text="🚫 Promoting illegal content",
            callback_data="r2_spam_illegalcontent"
        )],
        [InlineKeyboardButton(
            text="📢 Promoting other content",
            callback_data="r2_spam_othercontent"
        )],
        [BTN_BACK_L1, BTN_CANCEL],
    )


# ─────────────────────────────────────────────────────────────
# LEVEL 3 — Sub-sub-kategori (Weapons & Drugs)
# ─────────────────────────────────────────────────────────────

def kb_weapons() -> InlineKeyboardMarkup:
    """Sub-opsi Weapons (level 3)."""
    BTN_BACK_L2 = InlineKeyboardButton(
        text="◀️ Kembali", callback_data="r_back_illegalgoods"
    )
    return _kb(
        [InlineKeyboardButton(
            text="🔫 Firearms and accessories",
            callback_data="r3_weapons_firearms"
        )],
        [InlineKeyboardButton(
            text="🗡️ Melee weapons",
            callback_data="r3_weapons_melee"
        )],
        [InlineKeyboardButton(
            text="⚡ Non-lethal weapons",
            callback_data="r3_weapons_nonlethal"
        )],
        [InlineKeyboardButton(
            text="❓ Other weapons",
            callback_data="r3_weapons_other"
        )],
        [BTN_BACK_L2, BTN_CANCEL],
    )


def kb_drugs() -> InlineKeyboardMarkup:
    """Sub-opsi Drugs (level 3)."""
    BTN_BACK_L2 = InlineKeyboardButton(
        text="◀️ Kembali", callback_data="r_back_illegalgoods"
    )
    return _kb(
        [InlineKeyboardButton(
            text="🚬 Nicotine products",
            callback_data="r3_drugs_nicotine"
        )],
        [InlineKeyboardButton(
            text="💊 Illegal drugs",
            callback_data="r3_drugs_illegaldrugs"
        )],
        [InlineKeyboardButton(
            text="❓ Other drugs",
            callback_data="r3_drugs_other"
        )],
        [BTN_BACK_L2, BTN_CANCEL],
    )


# ─────────────────────────────────────────────────────────────
# KEYBOARD KOMENTAR (setelah pilih sub-opsi)
# ─────────────────────────────────────────────────────────────

def kb_comment_optional(callback_data_submit: str) -> InlineKeyboardMarkup:
    """
    Keyboard untuk opsi yang komentar OPSIONAL.
    Ada 2 tombol: Kirim Langsung (tanpa komentar) dan Batal.
    User bisa kirim teks komentar atau klik Kirim Langsung.
    """
    return _kb(
        [InlineKeyboardButton(
            text="📤 Kirim Tanpa Komentar",
            callback_data=f"r_submit_nocomment_{callback_data_submit}"
        )],
        [BTN_CANCEL],
    )


def kb_comment_required() -> InlineKeyboardMarkup:
    """
    Keyboard untuk opsi yang komentar WAJIB (Terrorism).
    Hanya ada tombol Batal — user harus ketik komentar dulu.
    """
    return _kb(
        [BTN_CANCEL],
    )


def kb_cancel_only() -> InlineKeyboardMarkup:
    """Keyboard hanya tombol Batal."""
    return _kb([BTN_CANCEL])
