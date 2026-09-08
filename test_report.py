#!/usr/bin/env python3
"""
test_report.py — Test report BENERAN ke target nyata.

Usage:
    python3 test_report.py

Yang ditest:
  1. Semua reason class yang tersedia di pyrogram versi ini
  2. Satu invoke messages.Report beneran ke target yang kamu tentukan
  3. Bandingkan opsi bot vs opsi Telegram manual

EDIT bagian CONFIG di bawah sebelum jalankan.
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
# CONFIG — edit ini sesuai kebutuhanmu
# ─────────────────────────────────────────────────────────────

# Session string salah satu sender yang sudah ada di DB
# Ambil dari MongoDB: db.senders.findOne({is_active: true}).session_string
SESSION_STRING = "MASUKKAN_SESSION_STRING_DI_SINI"

# Target yang mau ditest reportnya
# Gunakan username channel/user spam yang jelas, atau akun test milik kamu sendiri
TARGET_PEER = "username_target"   # contoh: "somespamchannel"
TARGET_MSG_ID = 1                  # ID pesan yang mau dilaporkan

API_ID   = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

# ─────────────────────────────────────────────────────────────


async def main():
    from pyrogram import Client
    from pyrogram.raw import types, functions

    print("=" * 55)
    print("  STEP 1 — Cek reason class yang tersedia")
    print("=" * 55)

    candidates = [
        ("Spam",             "InputReportReasonSpam"),
        ("Violence",         "InputReportReasonViolence"),
        ("Pornography",      "InputReportReasonPornography"),
        ("Child Abuse",      "InputReportReasonChildAbuse"),
        ("Illegal Drugs",    "InputReportReasonIllegalDrugs"),
        ("Personal Details", "InputReportReasonPersonalDetails"),
        ("Copyright",        "InputReportReasonCopyright"),
        ("Fake Account",     "InputReportReasonFake"),
        ("Other",            "InputReportReasonOther"),
    ]

    available = []
    for label, type_name in candidates:
        cls = getattr(types, type_name, None)
        ok = cls is not None
        print(f"  {'✅' if ok else '❌'}  {label:20s}  ({type_name})")
        if ok:
            available.append((label, type_name, cls))

    print()
    print(f"  Total tersedia: {len(available)}/{len(candidates)}")
    print()

    # ── Bandingkan dengan yang Telegram tampilkan saat report manual ──
    # Telegram app (layer terbaru) menampilkan:
    TELEGRAM_MANUAL = [
        "Spam", "Violence", "Pornography", "Child Abuse",
        "Illegal Drugs", "Personal Details", "Copyright",
        "Fake Account", "Other",
    ]
    bot_labels = [l for l, _, _ in available]
    missing    = [x for x in TELEGRAM_MANUAL if x not in bot_labels]
    extra      = [x for x in bot_labels if x not in TELEGRAM_MANUAL]

    print("=" * 55)
    print("  STEP 2 — Perbandingan vs Telegram manual")
    print("=" * 55)
    if not missing and not extra:
        print("  ✅ Opsi bot IDENTIK dengan Telegram manual!")
    else:
        if missing:
            print(f"  ⚠️  Opsi yang ADA di Telegram tapi TIDAK di bot: {missing}")
            print(f"     → pyrogram kamu tidak support reason ini (layer terlalu lama)")
        if extra:
            print(f"  ⚠️  Opsi di bot tapi TIDAK di Telegram: {extra}")
    print()

    if SESSION_STRING == "MASUKKAN_SESSION_STRING_DI_SINI":
        print("=" * 55)
        print("  STEP 3 — Test invoke DILEWATI")
        print("  Edit SESSION_STRING di script ini untuk test invoke beneran.")
        print("=" * 55)
        return

    if not API_ID or not API_HASH:
        print("❌ API_ID / API_HASH belum diset di .env")
        return

    print("=" * 55)
    print("  STEP 3 — Test invoke messages.Report beneran")
    print(f"  Target : {TARGET_PEER}")
    print(f"  Msg ID : {TARGET_MSG_ID}")
    print(f"  Reason : Spam (test)")
    print("=" * 55)

    spam_cls = getattr(types, "InputReportReasonSpam", None)
    if spam_cls is None:
        print("❌ InputReportReasonSpam tidak tersedia, tidak bisa test.")
        return

    client = Client(
        name="test_report",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
        no_updates=True,
        workdir="/tmp",
    )

    async with client:
        me = await client.get_me()
        print(f"  Login sebagai: @{me.username or me.first_name} ({me.phone_number})")

        try:
            peer = await client.resolve_peer(TARGET_PEER)
            print(f"  Peer resolved: {peer}")
        except Exception as e:
            print(f"  ❌ Gagal resolve peer '{TARGET_PEER}': {e}")
            return

        try:
            result = await client.invoke(
                functions.messages.Report(
                    peer=peer,
                    id=[TARGET_MSG_ID],
                    reason=spam_cls(),
                    message="",
                )
            )
            print(f"  ✅ Invoke berhasil! Result: {result}")
            print()
            print("  → Report BENERAN work. Bot siap dipakai.")
        except Exception as e:
            print(f"  ❌ Invoke gagal: {type(e).__name__}: {e}")
            print()
            print("  Kemungkinan penyebab:")
            print("  - Peer tidak valid / pesan tidak ada")
            print("  - Akun sudah pernah report pesan ini")
            print("  - FloodWait / rate limit")

    print()
    print("=" * 55)
    print("  SELESAI")
    print("=" * 55)


asyncio.run(main())
