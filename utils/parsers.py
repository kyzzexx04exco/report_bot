"""
utils/parsers.py
Fungsi-fungsi untuk mem-parsing URL/link Telegram menjadi komponen
yang dibutuhkan (peer username/ID dan message_id).
"""

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────
# POLA REGEX
# ─────────────────────────────────────────────────────────────

# Contoh link:
#   https://t.me/somechannel
#   https://t.me/somechannel/123
#   t.me/somechannel/123
#   https://t.me/c/1234567890/123   ← private/supergroup via numeric ID
#   https://telegram.me/somechannel/123

_PUBLIC_WITH_MSG = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/([A-Za-z0-9_]{3,})/(\d+)",
    re.IGNORECASE,
)

_PUBLIC_NO_MSG = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/([A-Za-z0-9_]{3,})$",
    re.IGNORECASE,
)

_PRIVATE_WITH_MSG = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/c/(\d+)/(\d+)",
    re.IGNORECASE,
)

_JOINCHAT = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:joinchat|\+)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────
# FUNGSI PUBLIK
# ─────────────────────────────────────────────────────────────

def parse_channel_link(text: str) -> Optional[str]:
    """
    Ekstrak peer identifier dari link channel/grup Telegram.
    Mengembalikan:
      - username (str)      → untuk public channel, misal "somechannel"
      - "-100XXXXXXXXXX"    → untuk private/supergroup (format string)
      - None                → jika link tidak valid
    """
    text = text.strip()

    # Coba cocokkan link dengan message_id dulu (public)
    m = _PUBLIC_WITH_MSG.search(text)
    if m:
        return m.group(1)  # username

    # Coba link tanpa message_id (public)
    m = _PUBLIC_NO_MSG.search(text)
    if m:
        return m.group(1)

    # Coba private supergroup: t.me/c/CHANNEL_ID/MSG_ID
    m = _PRIVATE_WITH_MSG.search(text)
    if m:
        raw_id = m.group(1)
        return f"-100{raw_id}"

    return None


def parse_message_link(text: str) -> Optional[tuple[str, int]]:
    """
    Ekstrak (peer, message_id) dari link pesan Telegram.
    Mengembalikan:
      - (peer_str, message_id) jika berhasil
      - None jika link tidak mengandung message_id

    peer_str bisa berupa username atau "-100XXXXXXXXXX".
    """
    text = text.strip()

    # Public: t.me/username/123
    m = _PUBLIC_WITH_MSG.search(text)
    if m:
        username = m.group(1)
        msg_id = int(m.group(2))
        return username, msg_id

    # Private supergroup: t.me/c/CHANNEL_ID/MSG_ID
    m = _PRIVATE_WITH_MSG.search(text)
    if m:
        raw_id = m.group(1)
        msg_id = int(m.group(2))
        return f"-100{raw_id}", msg_id

    return None


def is_valid_telegram_link(text: str) -> bool:
    """
    Cek apakah teks adalah link Telegram yang valid
    (bisa berupa link channel, grup, atau pesan).
    """
    text = text.strip()
    return bool(
        _PUBLIC_WITH_MSG.search(text)
        or _PUBLIC_NO_MSG.search(text)
        or _PRIVATE_WITH_MSG.search(text)
        or _JOINCHAT.search(text)
    )


def normalize_phone(phone: str) -> str:
    """
    Normalisasi nomor HP:
    - Hapus spasi, tanda hubung, kurung
    - Tambah '+' di depan jika belum ada
    """
    phone = re.sub(r"[\s\-\(\)]", "", phone)
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone
