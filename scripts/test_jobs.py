import asyncio
import sys
import os
import logging
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)5s] %(name)-15s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("test_jobs")


async def main():
    logger.info("[ TEST ] Запуск тестовой последовательности...")

    try:
        from infrastructure.database.db import init_db
        await init_db()
        logger.info("[ OK ] Инициализирована база данных.")
    except Exception as exc:
        logger.error(f"[ FAIL ] Инициализация базы данных: {exc}.")
        traceback.print_exc()
        return

    try:
        from config import BOT_TOKEN, BACKUP_CHAT_ID
    except Exception as exc:
        logger.error(f"[ FAIL ] Импорт конфига: {exc}.")
        traceback.print_exc()
        return

    if not BOT_TOKEN:
        logger.error("[ FAIL ] Переменная: \"BOT_TOKEN\" отсутствует.")
        return

    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        bot = Bot(
            token=BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logger.info("[ OK ] Экземпляр бота создан.")
    except Exception as exc:
        logger.error(f"[ FAIL ] Инициализация бота: {exc}.")
        traceback.print_exc()
        return

    try:
        from services.scheduler import (
            check_expiring_subscriptions,
            job_backup,
            job_expire_pending_payments,
            job_cleanup_fsm,
        )
        logger.info("[ OK ] Импортированы модули задач.")
    except Exception as exc:
        logger.error(f"[ FAIL ] Импорт планировщика: {exc}.")
        traceback.print_exc()
        return

    logger.info("[ TEST ] check_expiring_subscriptions...")
    try:
        await check_expiring_subscriptions(bot)
        logger.info("[ OK ] Завершено.")
    except Exception as exc:
        logger.error(f"[ FAIL ] {exc}.")
        traceback.print_exc()

    logger.info("[ TEST ] job_expire_pending_payments...")
    try:
        await job_expire_pending_payments()
        logger.info("[ OK ] Завершено.")
    except Exception as exc:
        logger.error(f"[ FAIL ] {exc}.")
        traceback.print_exc()

    logger.info("[ TEST ] job_cleanup_fsm...")
    try:
        count = await job_cleanup_fsm()
        logger.info(f"[ OK ] Завершено. Очищено: {count or 0}.")
    except Exception as exc:
        logger.error(f"[ FAIL ] {exc}.")
        traceback.print_exc()

    logger.info("[ TEST ] job_backup...")
    if BACKUP_CHAT_ID:
        try:
            await job_backup(bot)
            logger.info("[ OK ] Завершено.")
        except Exception as exc:
            logger.error(f"[ FAIL ] {exc}")
            traceback.print_exc()
    else:
        logger.info("[ SKIP ] Переменная: \"BACKUP_CHAT_ID\" отсутствует.")

    logger.info("[ TEST ] Тестовая последовательность завершена.")

    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        logging.critical(f"[ FATAL ] {exc}.")
        traceback.print_exc()
        sys.exit(1)
