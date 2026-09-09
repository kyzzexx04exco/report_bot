"""
main.py
Entry point utama untuk Telegram Report Bot.
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


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

from database import db
from utils import pyro_client
from bot.handlers import start, sender, report, reportv2, reportbot, reportpriv, settings, reactchan


async def on_startup(bot: Bot) -> None:
    logger.info("=" * 60)
    logger.info("🚀 Report Bot sedang starting...")
    logger.info("=" * 60)

    logger.info("[STARTUP] Inisialisasi database...")
    await db.init_db()
    logger.info("[STARTUP] ✅ Database siap.")

    logger.info("[STARTUP] Konfigurasi Pyrogram client pool...")
    pyro_client.configure(API_ID, API_HASH)

    all_senders = await db.get_all_senders()
    logger.info(f"[STARTUP] Ditemukan {len(all_senders)} sender di database.")
    await pyro_client.load_all_sessions(all_senders)

    active_count = await db.count_active_senders()
    logger.info(f"[STARTUP] ✅ {active_count} sender aktif siap digunakan.")

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
    logger.info("[SHUTDOWN] Menghentikan semua Pyrogram client...")
    await pyro_client.stop_all_clients()
    logger.info("[SHUTDOWN] Menutup koneksi MongoDB...")
    await db.close_pool()
    logger.info("[SHUTDOWN] ✅ Semua client dihentikan. Bot offline.")


async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # ── Daftarkan semua router ──
    dp.include_router(start.router)       # /start, /help
    dp.include_router(sender.router)      # /addsender + FSM OTP
    dp.include_router(report.router)      # /report + FSM + callback
    dp.include_router(reportv2.router)    # /reportv2 — report channel/group public
    dp.include_router(reportbot.router)   # /reportbot — report user/bot
    dp.include_router(reportpriv.router)  # /reportpriv — report channel/group private
    dp.include_router(settings.router)    # /setcdrep, /listsender, /delsender
    dp.include_router(reactchan.router)   # /reactchan — react emoji ke channel/group

    logger.info("[MAIN] Semua router berhasil didaftarkan.")
    logger.info("[MAIN] Memulai polling...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n[MAIN] Bot dihentikan oleh pengguna (Ctrl+C).")
