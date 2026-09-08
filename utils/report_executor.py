"""
utils/report_executor.py

Detect InputReportReason yang tersedia di runtime (bukan hardcode),
sehingga tidak crash apapun versi pyrogram yang dipakai.

Flow report:
  - Opsi tanpa komentar  → langsung invoke messages.Report
  - Opsi dengan komentar → FSM minta komentar dulu, lalu invoke

Repeat N kali dengan rotasi sender (round-robin by last_used).
"""

import logging
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import FloodWait, PeerIdInvalid, UsernameNotOccupied
from pyrogram.raw import functions, types as raw_types

from database import db
from utils import pyro_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# BUILD REPORT_OPTIONS — detect di runtime, jangan hardcode
#
# Format tiap entry: (label, key, reason_class, needs_comment)
#
# Urutan & label = sama persis seperti Telegram app.
# Kalau suatu reason class tidak ada di versi pyrogram yang dipakai,
# entry itu di-skip otomatis — bot tidak crash.
# ─────────────────────────────────────────────────────────────

def _get(name: str):
    """Return class dari raw_types jika ada, else None."""
    return getattr(raw_types, name, None)


_CANDIDATES = [
    # (label,             key,               type_name,                          needs_comment)
    ("Spam",             "spam",            "InputReportReasonSpam",             False),
    ("Violence",         "violence",        "InputReportReasonViolence",         False),
    ("Pornography",      "porn",            "InputReportReasonPornography",      False),
    ("Child Abuse",      "childabuse",      "InputReportReasonChildAbuse",       False),
    ("Illegal Drugs",    "illegaldrugs",    "InputReportReasonIllegalDrugs",     False),
    ("Personal Details", "personaldetails", "InputReportReasonPersonalDetails",  True),
    ("Copyright",        "copyright",       "InputReportReasonCopyright",        True),
    ("Fake Account",     "fake",            "InputReportReasonFake",             True),
    ("Other",            "other",           "InputReportReasonOther",            True),
]

REPORT_OPTIONS: list[tuple] = []
for _label, _key, _type_name, _needs_comment in _CANDIDATES:
    _cls = _get(_type_name)
    if _cls is not None:
        REPORT_OPTIONS.append((_label, _key, _cls, _needs_comment))
        logger.debug(f"[REPORT_OPTIONS] ✅ {_type_name}")
    else:
        logger.warning(f"[REPORT_OPTIONS] ⚠️  {_type_name} tidak ada di versi pyrogram ini, skip.")

_OPTION_MAP: dict[str, tuple] = {
    key: (cls, nc) for _, key, cls, nc in REPORT_OPTIONS
}


def get_option_meta(key: str) -> tuple:
    """Return (reason_class, needs_comment). Fallback ke Other jika key tidak dikenal."""
    return _OPTION_MAP.get(key, (_get("InputReportReasonOther") or raw_types.InputReportReasonOther, True))


# ─────────────────────────────────────────────────────────────
# SINGLE REPORT — satu sender, satu invoke
# ─────────────────────────────────────────────────────────────

async def do_report(
    client: Client,
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
) -> None:
    """
    Invoke messages.Report satu kali.
    Raise FloodWait jika kena rate limit.
    Raise ValueError jika peer tidak ditemukan.
    Raise Exception untuk error lain.
    """
    reason_cls, _ = get_option_meta(option_key)

    try:
        peer = await client.resolve_peer(peer_str)
    except (PeerIdInvalid, UsernameNotOccupied) as e:
        raise ValueError(f"Peer '{peer_str}' tidak ditemukan.") from e

    await client.invoke(
        functions.messages.Report(
            peer=peer,
            id=message_ids,
            reason=reason_cls(),
            message=comment,
        )
    )


# ─────────────────────────────────────────────────────────────
# ORCHESTRATOR — repeat N kali, rotasi sender
# ─────────────────────────────────────────────────────────────

async def run_report_with_rotation(
    peer_str: str,
    message_ids: list[int],
    option_key: str,
    comment: str = "",
    admin_user_id: int = 0,
    repeat: int = 1,
) -> dict:
    """
    Kirim laporan sebanyak `repeat` kali dengan rotasi sender.

    Per iterasi:
      - get_next_sender(exclude) → sender paling lama idle, bukan yang sudah dicoba
      - Cooldown belum habis → skip (bukan inactive)
      - FloodWait → inactive + hapus dari pool → coba sender lain
      - Peer tidak ada → stop semua, return error
    """
    cooldown_sec = await db.get_cooldown(admin_user_id)
    repeat = max(1, repeat)

    sent_count = 0
    last_phone = ""

    for i in range(repeat):
        tried_in_round: set[str] = set()
        got_one = False

        while True:
            sender = await db.get_next_sender(exclude=tried_in_round)

            if sender is None:
                if sent_count > 0:
                    return _partial(sent_count, repeat, peer_str)
                return _no_sender()

            phone = sender["phone_number"]
            tried_in_round.add(phone)

            # Cek cooldown — skip saja, jangan inactive
            last_used = sender.get("last_used")
            if last_used:
                try:
                    lu_dt = datetime.fromisoformat(str(last_used))
                    elapsed = (datetime.utcnow() - lu_dt).total_seconds()
                    if elapsed < cooldown_sec:
                        logger.info(f"[REPORT] {phone} cooldown {int(cooldown_sec-elapsed)}s, skip.")
                        continue
                except (ValueError, TypeError):
                    pass

            client = pyro_client.get_client(phone)
            if client is None:
                logger.warning(f"[REPORT] {phone} tidak di pool, tandai inactive.")
                await db.set_sender_inactive(phone)
                continue

            try:
                await do_report(
                    client=client,
                    peer_str=peer_str,
                    message_ids=message_ids,
                    option_key=option_key,
                    comment=comment,
                )
                await db.update_sender_last_used(phone)
                last_phone = phone
                sent_count += 1
                got_one = True
                logger.info(f"[REPORT] ✅ {i+1}/{repeat} via {phone}")
                break

            except FloodWait as fw:
                logger.warning(f"[REPORT] FloodWait {fw.value}s pada {phone}, inactive.")
                await db.set_sender_inactive(phone)
                await pyro_client.remove_client_from_pool(phone)
                continue

            except ValueError as e:
                # Peer tidak ditemukan — stop semua iterasi
                return {
                    "success": False,
                    "message": f"❌ {e}",
                    "sent": sent_count,
                    "total": repeat,
                }

            except Exception as e:
                logger.error(f"[REPORT] Error tak terduga {phone}: {e}")
                return {
                    "success": False,
                    "message": f"❌ Error: {e}",
                    "sent": sent_count,
                    "total": repeat,
                }

        if not got_one:
            break

    if sent_count == 0:
        return _all_failed()

    if repeat == 1:
        return {
            "success": True,
            "message": (
                f"✅ <b>Laporan terkirim!</b>\n"
                f"📱 Sender: <code>{last_phone}</code>\n"
                f"🎯 Target: <code>{peer_str}</code>"
            ),
            "sent": sent_count,
            "total": repeat,
        }

    return {
        "success": True,
        "message": (
            f"✅ <b>Selesai!</b>\n"
            f"📊 Terkirim: <b>{sent_count}/{repeat}</b>\n"
            f"🎯 Target: <code>{peer_str}</code>\n"
            f"📱 Sender terakhir: <code>{last_phone}</code>"
        ),
        "sent": sent_count,
        "total": repeat,
    }


# ─────────────────────────────────────────────────────────────
# Result helpers
# ─────────────────────────────────────────────────────────────

def _no_sender() -> dict:
    return {"success": False, "message": "🚫 Tidak ada sender aktif. Tambah dulu via /addsender.", "sent": 0, "total": 0}

def _all_failed() -> dict:
    return {"success": False, "message": "🚫 Semua sender kena FloodWait/inactive. Coba lagi nanti.", "sent": 0, "total": 0}

def _partial(sent, total, peer_str) -> dict:
    return {
        "success": True,
        "message": (
            f"⚠️ <b>Parsial</b>\n"
            f"📊 Terkirim: <b>{sent}/{total}</b>\n"
            f"🎯 Target: <code>{peer_str}</code>\n"
            f"💡 Sender habis setelah {sent} laporan."
        ),
        "sent": sent, "total": total,
    }
