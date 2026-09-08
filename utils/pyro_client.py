"""
utils/pyro_client.py
Manajemen Pyrogram client pool.

Bug "Client is already terminated" terjadi karena:
- create_temp_client tanpa workdir → pyrogram coba buat file .session di cwd
- Kalau cwd read-only atau ada file bentrok, pyrogram crash saat connect/disconnect
- Fix: pakai workdir="/tmp" + in_memory=True di semua client

Proxy:
- Dikonfigurasi via .env: PROXY_SCHEME, PROXY_HOST, PROXY_PORT
- PROXY_SCHEME: mtproto | socks5 | socks4 | http
- Kalau PROXY_HOST tidak di-set, proxy dinonaktifkan
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

_clients: dict[str, Client] = {}
_API_ID: int = 0
_API_HASH: str = ""


def _build_proxy() -> Optional[dict]:
    """Baca konfigurasi proxy dari env. Return None kalau tidak di-set."""
    host = os.getenv("PROXY_HOST", "").strip()
    if not host:
        return None

    scheme = os.getenv("PROXY_SCHEME", "mtproto").strip().lower()
    port   = int(os.getenv("PROXY_PORT", "443"))

    proxy: dict = {"scheme": scheme, "hostname": host, "port": port}

    # Opsional — hanya untuk socks5/http yang butuh auth
    username = os.getenv("PROXY_USERNAME", "").strip()
    password = os.getenv("PROXY_PASSWORD", "").strip()
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password

    logger.info(f"[PROXY] Menggunakan proxy {scheme}://{host}:{port}")
    return proxy


def configure(api_id: int, api_hash: str) -> None:
    global _API_ID, _API_HASH
    _API_ID = api_id
    _API_HASH = api_hash


def create_client_from_session(phone_number: str, session_string: str) -> Client:
    """Buat client dari session_string yang sudah ada di DB."""
    if not _API_ID or not _API_HASH:
        raise RuntimeError("Panggil pyro_client.configure() dulu.")

    safe = phone_number.replace("+", "").replace(" ", "")
    return Client(
        name=f"sender_{safe}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=True,
        workdir="/tmp",
        proxy=_build_proxy(),
    )


def create_temp_client(phone_number: str) -> Client:
    """
    Buat client sementara untuk flow /addsender (belum ada session).

    workdir="/tmp" wajib — tanpa ini pyrogram coba tulis file .session
    di working directory bot, yang sering read-only atau bentrok dan
    menyebabkan "Client is already terminated".
    """
    if not _API_ID or not _API_HASH:
        raise RuntimeError("Panggil pyro_client.configure() dulu.")

    safe = phone_number.replace("+", "").replace(" ", "")
    return Client(
        name=f"temp_{safe}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        in_memory=True,
        no_updates=True,
        workdir="/tmp",
        proxy=_build_proxy(),
    )


async def load_all_sessions(senders: list[dict]) -> None:
    """Load semua sender aktif dari DB ke pool saat startup."""
    from database import db  # import di sini hindari circular
    for sender in senders:
        phone = sender["phone_number"]
        session = sender.get("session_string", "")
        if not bool(sender.get("is_active")):
            logger.info(f"[POOL] {phone} tidak aktif, skip.")
            continue
        if not session:
            logger.warning(f"[POOL] {phone} tidak punya session_string, skip.")
            continue
        try:
            client = create_client_from_session(phone, session)
            await client.start()
            _clients[phone] = client
            me = await client.get_me()
            logger.info(f"[POOL] ✅ {phone} → @{me.username or me.first_name}")
        except (AuthKeyUnregistered, SessionRevoked, UserDeactivated) as e:
            logger.error(f"[POOL] ❌ {phone} session invalid: {e} — tandai inactive.")
            await db.set_sender_inactive(phone)
        except Exception as e:
            # Jangan coba stop client yang gagal start —
            # Pyrogram 2.0.106 internal state-nya incomplete, stop() bakal crash
            logger.error(f"[POOL] ❌ Gagal load {phone}: {e}")


async def add_client_to_pool(phone_number: str, client: Client) -> None:
    _clients[phone_number] = client
    logger.info(f"[POOL] {phone_number} ditambahkan ke pool.")


async def remove_client_from_pool(phone_number: str) -> None:
    client = _clients.pop(phone_number, None)
    if client:
        try:
            await client.stop()
        except Exception:
            pass
    logger.info(f"[POOL] {phone_number} dihapus dari pool.")


def get_client(phone_number: str) -> Optional[Client]:
    return _clients.get(phone_number)


def get_all_clients() -> dict[str, Client]:
    return dict(_clients)


async def stop_all_clients() -> None:
    for phone, client in list(_clients.items()):
        try:
            await client.stop()
        except Exception as e:
            logger.warning(f"[POOL] Gagal stop {phone}: {e}")
    _clients.clear()
