import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from config import BOT_TOKEN, LOG_LEVEL, VPN_SERVERS, MTPROTO_SERVERS
from infrastructure.database.db import init_db, close_db
from core.crypto import init_crypto
from core.fsm_storage import SQLiteStorage
from core.middlewares import ThrottlingMiddleware
from services.scheduler import setup_scheduler, shutdown_scheduler
from infrastructure.vpn_providers.awg_easy import AwgEasyAPI, AwgEasyProvider
from infrastructure.vpn_providers.mtproto_mgr import MTProtoManagerAPI, MtprotoProvider
from domain.provisioning import init_provisioner
from handlers import user, admin, errors

logger = logging.getLogger(__name__)

def setup_logging():
    lvl = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s | [%(levelname)5s] %(name)-15s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logger.info("=" * 60)
    logger.info("[ INIT ] Загрузка Fsociety Network...")
    logger.info("[ INIT ] Hello, Friend.")
    logger.info("=" * 60)

async def on_startup(bot: Bot):
    await init_db()
    init_crypto()

    # Ноды AmneziaWG
    awg_providers = {}
    for key, cfg in VPN_SERVERS.items():
        api = AwgEasyAPI(url=cfg["url"], password=cfg["password"], name=cfg["name"])
        if await api.healthcheck():
            awg_providers[key] = AwgEasyProvider(api)
            logger.info(f"[ OK ] Нода AmneziaWG активна: {key} ({cfg['name']})")
        else:
            logger.error(f"[ FAIL ] Нода AmneziaWG недоступна: {key}")
            await api.close()

    # Ноды MTProto
    mtproto_providers = {}
    for key, cfg in MTPROTO_SERVERS.items():
        api = MTProtoManagerAPI(
            url=cfg["url"], proxy_host=cfg["host"], proxy_port=cfg["port"], name=key
        )
        if await api.healthcheck():
            mtproto_providers[key] = MtprotoProvider(api)
            logger.info(f"[ OK ] Нода MTProto активна: {key} ({cfg['host']})")
        else:
            logger.error(f"[ FAIL ] Нода MTProto недоступна: {key}")
            await api.close()

    init_provisioner(awg_providers, mtproto_providers)
    setup_scheduler(bot)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Инициализация сессии"),
            BotCommand(command="admin", description="Панель администратора"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    logger.info("[ OK ] Анти-спам файрвол активен.")
    logger.info("[ OK ] Маршрутизаторы смонтированы.")
    logger.info("[ OK ] Фоновые задачи активны.")

async def on_shutdown(bot: Bot):
    logger.info("[ SHUTDOWN ] Завершение Fsociety Network процессов...")
    shutdown_scheduler()
    from domain.provisioning import get_provisioner
    prov = get_provisioner()
    for p in list(prov.awg.values()) + list(prov.mtproto.values()):
        if hasattr(p, '_api'):
            await p._api.close()
    await close_db()
    logger.info("[ DOWN ] Goodbye, Friend.")

async def main():
    setup_logging()

    if not BOT_TOKEN:
        logger.critical("[ FATAL ] Переменная: \"BOT_TOKEN\" отсутствует. Аварийное завершение процессов.")
        sys.exit(1)

    storage = SQLiteStorage()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=storage)

    dp.message.middleware(ThrottlingMiddleware())
    dp.include_router(errors.router)
    dp.include_router(user.router)
    dp.include_router(admin.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
