import logging
from pathlib import Path
from typing import Optional

from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InputMediaPhoto, FSInputFile,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

ASSETS_DIR = Path("assets")

ASSETS = {
    "menu":     None,
    "profile":  None,
    "payment":  None,
    "settings": None,
    "configs":  None,
}

def _resolve_media(asset_key: str) -> Optional[str | FSInputFile]:
    file_id = ASSETS.get(asset_key)
    if file_id:
        return file_id
    if not asset_key:
        return None
    for ext in (".png", ".jpg", ".jpeg"):
        path = ASSETS_DIR / f"{asset_key}{ext}"
        if path.exists():
            return FSInputFile(path)
    return None

async def answer_menu(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    asset_key: str = "menu",
):
    photo_id = _resolve_media(asset_key)
    if photo_id:
        return await message.answer_photo(
            photo=photo_id, caption=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    return await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")

async def _safe_edit_media(
    obj: Message | CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    asset_key: str = "menu",
) -> Optional[Message]:
    message = obj if isinstance(obj, Message) else obj.message
    media = _resolve_media(asset_key)
    has_media = message.photo is not None

    if has_media and media:
        try:
            await message.edit_media(
                media=InputMediaPhoto(media=media, caption=text, parse_mode="HTML"),
                reply_markup=reply_markup,
            )
            return message
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not modified" in err:
                return message
            logger.debug(f"[ UI ] Провален edit_media: {e}.")
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer_photo(
                photo=media, caption=text, reply_markup=reply_markup, parse_mode="HTML"
            )

    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return message
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return message
        logger.debug(f"[ UI ] Провален edit_text: {e}.")
        try:
            await message.delete()
        except Exception:
            pass
        if media:
            return await message.answer_photo(
                photo=media, caption=text, reply_markup=reply_markup, parse_mode="HTML"
            )
        return await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")

async def _safe_cb_answer(cb: CallbackQuery, text: str = "", show_alert: bool = False):
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception:
        pass

def _chunk_buttons(
    labels_data: list[tuple[str, str]],
    row_widths: tuple[int, ...] = (2, 1)
) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for txt, data in labels_data:
        if data.startswith(("http://", "https://", "tg://")):
            kb.button(text=txt, url=data)
        else:
            kb.button(text=txt, callback_data=data)
    kb.adjust(*row_widths)
    return kb

# Панель пользователя
def kb_main() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Access", "buy_sub"),
        ("Identity", "my_sub"),
        ("Documentation", "instructions"),
        ("Ping C&C", "https://t.me/paranoidalwate"),
    ], (2, 2)).as_markup()

def kb_back(to: str = "back_to_menu") -> InlineKeyboardMarkup:
    return _chunk_buttons([("« Return", to)], (1,)).as_markup()

def kb_cancel_payment() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Abort", "cancel_payment"),
    ], (1,)).as_markup()

def kb_sub_active() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Configuration", "show_configs"),
        ("« Return", "back_to_menu"),
    ], (1, 1)).as_markup()

def kb_configs(devices: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for dev in devices:
        label = f"▸ {dev['server_name']} · {dev['device_type'].upper()}"
        buttons.append((label, f"dl_cfg_{dev['id']}"))
    buttons.append(("« Return", "my_sub"))
    return _chunk_buttons(buttons, (2, 1)).as_markup()

# Панель администратора
def kb_admin_main() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Payment Requests", "admin_pending"),
        ("Service Status", "admin_stats"),
        ("Broadcast", "admin_broadcast"),
        ("Settings", "admin_settings"),
    ], (2, 2)).as_markup()

def kb_admin_settings() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Bank Details", "admin_set_details"),
        ("Price", "admin_set_amount"),
        ("« Return", "back_to_admin"),
    ], (2, 1)).as_markup()

def kb_broadcast_confirm() -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Deploy", "broadcast_confirm"),
        ("Abort", "broadcast_cancel"),
    ], (2,)).as_markup()

def kb_pending_nav(page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_prev:
        buttons.append(("« Back", f"admin_pending:{page - 1}"))
    if has_next:
        buttons.append(("Next »", f"admin_pending:{page + 1}"))
    buttons.append(("« Return", "back_to_admin"))
    return _chunk_buttons(buttons, (2, 1)).as_markup()

def kb_payment_admin(payment_id: int) -> InlineKeyboardMarkup:
    return _chunk_buttons([
        ("Approve", f"approve_{payment_id}"),
        ("Decline", f"reject_{payment_id}"),
    ], (2,)).as_markup()
