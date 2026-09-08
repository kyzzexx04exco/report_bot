"""
main.py
Entry point utama untuk Telegram Report Bot.

Urutan inisialisasi:
  1. Load .env
  2. Setup logging
  3. Init database (buat tabel jika belum ada)
  4. Konfigurasi Pyrogram (API_ID, API_HASH)
  5. Load semua sender dari DB ke Pyrogram client pool
  6. Daftarkan semua router ke Dispatcher
  7. Start polling Aiogram bot
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Load environment variables dari .env
load_dotenv()

# ─────────────────────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# VALIDASI ENV
# ─────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        logger.critical(f"❌ Environment variable '{key}' tidak ditemukan di .env!")
        sys.exit(1)
    return value


BOT_TOKEN = _require_env("BOT_TOKEN")
API_ID    = int(_require_env("API_ID"))
API_HASH  = _require_env("API_HASH")
OWNER_ID  = int(_require_env("OWNER_ID"))


# ─────────────────────────────────────────────────────────────
# IMPORT MODUL INTERNAL (setelah env diload)
# ─────────────────────────────────────────────────────────────

from database import db
from utils import pyro_client
from bot.handlers import start, sender, report, reportv2, settings


# ─────────────────────────────────────────────────────────────
# STARTUP / SHUTDOWN HOOKS
# ─────────────────────────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    """Dijalankan saat bot pertama kali start."""
    logger.info("=" * 60)
    logger.info("🚀 Report Bot sedang starting...")
    logger.info("=" * 60)

    # Inisialisasi database
    logger.info("[STARTUP] Inisialisasi database...")
    await db.init_db()
    logger.info("[STARTUP] ✅ Database siap.")

    # Konfigurasi Pyrogram
    logger.info("[STARTUP] Konfigurasi Pyrogram client pool...")
    pyro_client.configure(API_ID, API_HASH)

    # Load semua sender dari database ke pool
    all_senders = await db.get_all_senders()
    logger.info(f"[STARTUP] Ditemukan {len(all_senders)} sender di database.")
    await pyro_client.load_all_sessions(all_senders)

    active_count = await db.count_active_senders()
    logger.info(f"[STARTUP] ✅ {active_count} sender aktif siap digunakan.")

    # Kirim notifikasi ke owner
    try:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=(
                "✅ <b>Report Bot berhasil dijalankan!</b>\n\n"
                f"📱 Sender aktif: <b>{active_count}</b>\n\n"
                "Ketik /help untuk melihat daftar command."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"[STARTUP] Tidak bisa kirim notifikasi ke owner: {e}")

    logger.info("[STARTUP] ✅ Bot siap menerima perintah!")
    logger.info("=" * 60)


async def on_shutdown(bot: Bot) -> None:
    """Dijalankan saat bot shutdown."""
    logger.info("[SHUTDOWN] Menghentikan semua Pyrogram client...")
    await pyro_client.stop_all_clients()
    logger.info("[SHUTDOWN] Menutup koneksi MongoDB...")
    await db.close_pool()
    logger.info("[SHUTDOWN] ✅ Semua client dihentikan. Bot offline.")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

async def main() -> None:
    # Inisialisasi Aiogram Bot & Dispatcher
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # MemoryStorage untuk FSM (cukup untuk single-instance bot)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Daftarkan lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # ── Daftarkan semua router ──
    # Urutan registrasi penting: handler yang lebih spesifik didaftarkan lebih dulu
    dp.include_router(start.router)      # /start, /help
    dp.include_router(sender.router)     # /addsender, /cancel + FSM OTP
    dp.include_router(report.router)     # /report + FSM + callback
    dp.include_router(reportv2.router)   # /reportv2 + FSM + callback (report peer)
    dp.include_router(settings.router)   # /setcdrep, /listsender, /delsender

    logger.info("[MAIN] Semua router berhasil didaftarkan.")
    logger.info("[MAIN] Memulai polling...")

    try:
        # Hapus webhook jika ada, lalu mulai polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n[MAIN] Bot dihentikan oleh pengguna (Ctrl+C).")
