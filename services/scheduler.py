import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from infrastructure.database.db import (
    get_expired_users, get_expiring_soon_users,
    update_user_status, get_user_devices,
    expire_old_pending_payments, cleanup_fsm_states,
    get_expired_devices, delete_device_record,
    get_partial_payments, set_payment_status,
)
from core.backup import send_backup
from domain.provisioning import get_provisioner
from config import DB_BACKUP_HOUR, PAYMENT_EXPIRE_HOURS

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def check_expired_subscriptions(bot: Bot):
    logger.info("[ JOB ] Проверка просроченных пользователей...")
    provisioner = get_provisioner()

    for user in await get_expired_users():
        tg_id = user["tg_id"]
        devices = await get_user_devices(tg_id)

        for dev in devices:
            provider = provisioner.get_provider(dev["server_name"], dev["device_type"])
            if not provider:
                logger.warning(f"[ WARN ] Отсутствует провайдер для: {dev['server_name']}/{dev['device_type']}.")
                continue
            try:
                await provider.disable_access(dev["awg_id"])
                logger.info(f"[ OK ] Отключен UID = {tg_id} {dev['device_type']} на {dev['server_name']}.")
            except Exception as e:
                logger.error(f"[ FAIL ] Отключение {dev['awg_id']}: {e}.")

        await update_user_status(tg_id, 'expired')

        try:
            await bot.send_message(
                tg_id,
                f"<b>root@fsociety:~#</b> <code>revoke_access --uid {tg_id}</code>\n\n"
                "Access revoked.\nTunnel sealed.\n\n"
                "<i>Restore via Access.</i>"
            )
        except TelegramForbiddenError:
            logger.warning(f"[ WARN ] Пользователь: {tg_id} — заблокировал администратора.")
        except Exception as e:
            logger.error(f"[ FAIL ] Уведомление {tg_id}: {e}.")


async def check_expiring_subscriptions(bot: Bot):
    logger.info("[ JOB ] Проверка TTL-предупреждений...")
    users = await get_expiring_soon_users(days=1)
    logger.info(f"[ JOB ] Найдены {len(users)} пользователи с TTL <24 часов.")
    for user in users:
        tg_id = user["tg_id"]
        try:
            await bot.send_message(
                tg_id,
                "<b>root@fsociety:~#</b> <code>warn: TTL < 24h</code>\n\n"
                "Subscription expires <b>tomorrow</b>.\n"
                "Renew via <b>Identity</b>."
            )
        except TelegramForbiddenError:
            pass
        except Exception as e:
            logger.error(f"[ FAIL ] Уведомление об истечении {tg_id}: {e}.")


async def job_purge_zombies():
    logger.info("[ JOB ] Очистка зомби с нод...")
    provisioner = get_provisioner()
    zombies = await get_expired_devices(cutoff_days=30)
    purged = 0
    for dev in zombies:
        provider = provisioner.get_provider(dev["server_name"], dev["device_type"])
        if not provider:
            continue
        try:
            removed = await provider.revoke_access(dev["awg_id"])
            if removed:
                await delete_device_record(dev["id"])
                purged += 1
                logger.info(f"[ OK  ] Очищен зомби: {dev['awg_id']} на {dev['server_name']}.")
        except Exception as e:
            logger.error(f"[ FAIL ] Очистка зомби {dev['awg_id']}: {e}.")
    if purged:
        logger.info(f"[ OK  ] Очистка зомби завершена. Удалено: {purged}.")


async def job_partial_provision(bot: Bot):
    logger.info("[ JOB ] Продолжение partial-выдач...")
    provisioner = get_provisioner()
    payments = await get_partial_payments()
    if not payments:
        return

    for p in payments:
        payment_id = p["id"]
        tg_id = p["tg_id"]
        try:
            result = await provisioner.retry_partial(tg_id, payment_id)
            if provisioner.is_fully_successful(result):
                await set_payment_status(payment_id, "approved")
                devices = await get_user_devices(tg_id)
                total = len(devices)
                try:
                    await bot.send_message(
                        tg_id,
                        f"<b>root@fsociety:~#</b> <code>bundle --resume</code>\n\n"
                        f"All systems nominal.\nFull bundle deployed: <code>{total}</code> payloads active.\n\n"
                        "Retrieve configs via <b>Identity → Configuration</b>."
                    )
                except Exception:
                    pass
                logger.info(f"[ OK ] Partial-платеж: #{payment_id} — полностью завершен.")
            else:
                failed_keys = [k for k, v in result.success.items() if not v]
                logger.warning(f"[ WARN ] Partial: #{payment_id} — остался неполным: {failed_keys}.")
        except Exception as e:
            logger.error(f"[ FAIL ] Повторение partial #{payment_id}: {e}.")


async def job_expire_pending_payments():
    count = await expire_old_pending_payments(hours=PAYMENT_EXPIRE_HOURS)
    if count:
        logger.info(f"[ JOB ] Удалено: {count} — просроченных платежей.")


async def job_cleanup_fsm():
    count = await cleanup_fsm_states(hours=48)
    if count:
        logger.info(f"[ JOB ] Удалено: {count} — устаревших FSM призраков.")
    return count


async def job_backup(bot: Bot):
    await send_backup(bot)


def setup_scheduler(bot: Bot):
    scheduler.add_job(
        check_expired_subscriptions, 'cron',
        hour=9, minute=0, args=[bot],
        id='check_expired', replace_existing=True,
    )
    scheduler.add_job(
        check_expiring_subscriptions, 'cron',
        hour=8, minute=0, args=[bot],
        id='check_expiring', replace_existing=True,
    )
    scheduler.add_job(
        job_purge_zombies, 'cron',
        hour=5, minute=0,
        id='purge_zombies', replace_existing=True,
    )
    scheduler.add_job(
        job_partial_provision, 'interval',
        minutes=10, args=[bot],
        id='partial_provision', replace_existing=True,
    )
    scheduler.add_job(
        job_expire_pending_payments, 'interval',
        hours=1,
        id='expire_pending', replace_existing=True,
    )
    scheduler.add_job(
        job_cleanup_fsm, 'cron',
        hour=3, minute=0,
        id='cleanup_fsm', replace_existing=True,
    )
    scheduler.add_job(
        job_backup, 'cron',
        hour=DB_BACKUP_HOUR, minute=0, args=[bot],
        id='db_backup', replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "[ OK ] Планировщик активирован. Задачи: expired / expiring / purge_zombies / partial_provision / expire_pending / cleanup_fsm / db_backup."
    )


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[ OK ] Планировщик отключен.")
