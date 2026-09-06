"""
utils/pyro_client.py
Manajemen Pyrogram client — membuat, menyimpan, dan mendapatkan
client aktif berdasarkan phone_number.

Pool global `_clients` diisi saat bot pertama kali start dengan
mem-load semua session_string dari database.
"""

import logging
import os
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
)

logger = logging.getLogger(__name__)

# Pool global: { phone_number: pyrogram.Client }
_clients: dict[str, Client] = {}

# Akan diisi dari .env saat init
_API_ID: int = 0
_API_HASH: str = ""


def configure(api_id: int, api_hash: str) -> None:
    """Set API credentials dari .env. Dipanggil sekali saat startup."""
    global _API_ID, _API_HASH
    _API_ID = api_id
    _API_HASH = api_hash


def create_client_from_session(
    phone_number: str,
    session_string: str,
) -> Client:
    """
    Buat Pyrogram Client dari session_string yang sudah tersimpan.
    Client belum di-start di sini — panggil .start() atau gunakan
    sebagai async context manager.
    """
    if not _API_ID or not _API_HASH:
        raise RuntimeError(
            "API_ID / API_HASH belum dikonfigurasi. "
            "Panggil pyro_client.configure() dulu."
        )

    client = Client(
        name=f"sender_{phone_number.replace('+', '').replace(' ', '')}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        session_string=session_string,
        in_memory=True,         # Tidak menyimpan file .session ke disk
        no_updates=True,        # Userbot ini hanya butuh invoke, bukan terima update
    )
    return client


def create_temp_client(phone_number: str) -> Client:
    """
    Buat Pyrogram Client sementara untuk proses login (belum ada session).
    Dipakai di flow /addsender.
    """
    if not _API_ID or not _API_HASH:
        raise RuntimeError(
            "API_ID / API_HASH belum dikonfigurasi. "
            "Panggil pyro_client.configure() dulu."
        )

    safe_phone = phone_number.replace("+", "").replace(" ", "")
    client = Client(
        name=f"temp_{safe_phone}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        in_memory=True,
        no_updates=True,
    )
    return client


async def load_all_sessions(senders: list[dict]) -> None:
    """
    Load dan start semua sender aktif dari database ke dalam _clients pool.
    Dipanggil saat bot pertama kali start.
    """
    for sender in senders:
        phone = sender["phone_number"]
        session = sender["session_string"]
        is_active = bool(sender["is_active"])

        if not is_active:
            logger.info(f"[POOL] Sender {phone} tidak aktif, dilewati.")
            continue

        try:
            client = create_client_from_session(phone, session)
            await client.start()
            _clients[phone] = client
            me = await client.get_me()
            logger.info(
                f"[POOL] ✅ Sender {phone} loaded → @{me.username or me.first_name}"
            )
        except (AuthKeyUnregistered, SessionRevoked, UserDeactivated) as e:
            logger.error(
                f"[POOL] ❌ Sender {phone} session tidak valid ({e}). "
                "Tandai inactive di DB."
            )
            # Tandai inactive (dipanggil dari luar agar tidak circular import)
            _clients.pop(phone, None)
        except Exception as e:
            logger.error(f"[POOL] ❌ Gagal load sender {phone}: {e}")


async def add_client_to_pool(phone_number: str, client: Client) -> None:
    """Tambahkan client yang sudah aktif ke pool."""
    _clients[phone_number] = client
    logger.info(f"[POOL] Sender {phone_number} ditambahkan ke pool.")


async def remove_client_from_pool(phone_number: str) -> None:
    """Stop dan hapus client dari pool."""
    client = _clients.pop(phone_number, None)
    if client:
        try:
            await client.stop()
        except Exception:
            pass
        logger.info(f"[POOL] Sender {phone_number} dihapus dari pool.")


def get_client(phone_number: str) -> Optional[Client]:
    """Ambil client dari pool berdasarkan nomor HP."""
    return _clients.get(phone_number)


def get_all_clients() -> dict[str, Client]:
    """Ambil semua client yang ada di pool."""
    return dict(_clients)


async def stop_all_clients() -> None:
    """Stop semua client (dipanggil saat shutdown)."""
    for phone, client in list(_clients.items()):
        try:
            await client.stop()
            logger.info(f"[POOL] Client {phone} dihentikan.")
        except Exception as e:
            logger.warning(f"[POOL] Gagal hentikan client {phone}: {e}")
    _clients.clear()
