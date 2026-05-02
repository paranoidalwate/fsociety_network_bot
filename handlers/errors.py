import logging

from aiogram import Router
from aiogram.types import ErrorEvent
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)
router = Router()

@router.errors()
async def global_error_handler(event: ErrorEvent):
    exc = event.exception
    update = event.update
    update_id = update.update_id if update else "?"
    logger.exception("[ FATAL ] Необработанное исключение в update %s.", update_id)
