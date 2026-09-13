"""
utils/pyro_client.py
Manajemen Pyrogram client pool.

FIX UTAMA:
- no_updates=False → sender keliatan online & terima update presence
- Cek koneksi pakai try/invoke GetMe() bukan is_connected (property private)
"""

import logging
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


def configure(api_id: int, api_hash: str) -> None:
    global _API_ID, _API_HASH
    _API_ID = api_id
    _API_HASH = api_hash


def create_client_from_session(phone_number: str, session_string: str) -> Client:
    if not _API_ID or not _API_HASH:
        raise RuntimeError("Panggil pyro_client.configure() dulu.")

    safe = phone_number.replace("+", "").replace(" ", "")
    return Client(
        name=f"sender_{safe}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=False,  # FIX: False agar sender keliatan online & bisa terima update
        workdir="/tmp",
    )


def create_temp_client(phone_number: str) -> Client:
    if not _API_ID or not _API_HASH:
        raise RuntimeError("Panggil pyro_client.configure() dulu.")

    safe = phone_number.replace("+", "").replace(" ", "")
    return Client(
        name=f"temp_{safe}",
        api_id=_API_ID,
        api_hash=_API_HASH,
        in_memory=True,
        no_updates=False,  # FIX: sama
        workdir="/tmp",
    )


async def _safe_start(client: Client) -> bool:
    try:
        await client.start()
        return True
    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated):
        raise
    except BaseException as e:
        logger.error(f"[POOL] start() gagal: {e}")
        return False


async def _is_alive(client: Client) -> bool:
    """
    Cek apakah client masih konek dengan cara paling ringan.
    Pyrogram tidak punya is_connected sebagai property publik yang reliable —
    cara paling aman adalah coba invoke operasi ringan (get_me).
    """
    try:
        await client.get_me()
        return True
    except Exception:
        return False


async def load_all_sessions(senders: list[dict]) -> None:
    from database import db
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
            ok = await _safe_start(client)
            if not ok:
                logger.error(f"[POOL] ❌ Gagal load {phone}: start() gagal.")
                continue
            _clients[phone] = client
            me = await client.get_me()
            logger.info(f"[POOL] ✅ {phone} → @{me.username or me.first_name}")
        except (AuthKeyUnregistered, SessionRevoked, UserDeactivated) as e:
            logger.error(f"[POOL] ❌ {phone} session invalid: {e} — tandai inactive.")
            await db.set_sender_inactive(phone)
        except BaseException as e:
            logger.error(f"[POOL] ❌ Gagal load {phone}: {e}")


async def ensure_connected(phone: str, session_string: str) -> Optional[Client]:
    """
    Cek koneksi client. Kalau mati → reconnect.
    Pakai get_me() sebagai health check, bukan is_connected (tidak reliable).
    """
    client = _clients.get(phone)

    if client is not None:
        alive = await _is_alive(client)
        if alive:
            return client
        # Mati → hapus dari pool, reconnect
        logger.warning(f"[RECONNECT] {phone} tidak responding, reconnect...")
        try:
            await client.stop()
        except Exception:
            pass
        _clients.pop(phone, None)

    # Reconnect
    try:
        new_client = create_client_from_session(phone, session_string)
        await new_client.start()
        _clients[phone] = new_client
        me = await new_client.get_me()
        logger.info(f"[RECONNECT] ✅ {phone} → @{me.username or me.first_name}")
        return new_client
    except Exception as e:
        logger.error(f"[RECONNECT] ❌ {phone} gagal reconnect: {e}")
        return None


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
