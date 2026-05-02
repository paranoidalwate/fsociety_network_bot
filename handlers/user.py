import time
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError

from infrastructure.database.db import (
    add_user, get_user, create_payment, get_setting, get_user_devices,
    has_pending_payment, get_device,
)
from core.crypto import decrypt_config
from core.ui import (
    answer_menu, _safe_edit_media, _safe_cb_answer,
    kb_main, kb_back, kb_cancel_payment, kb_sub_active,
    kb_configs, kb_payment_admin,
)
from config import ADMIN_IDS

logger = logging.getLogger(__name__)
router = Router()


class PaymentState(StatesGroup):
    waiting_for_receipt = State()

# Точка входа и навигация
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username or "unknown")
    text = (
        "<blockquote>Sometimes I dream of saving the world. Saving everyone from the invisible hand, "
        "one that brands us with an employee badge.</blockquote>\n\n"
        "<b>root@fsociety:~#</b> <i>welcome to fsociety...</i>"
    )
    await answer_menu(message, text, reply_markup=kb_main(), asset_key="menu")

@router.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <i>cd /home</i>\n\nSelect operation:",
        reply_markup=kb_main(),
        asset_key="menu",
    )
    await _safe_cb_answer(cb)

# Профиль и конфигурации
@router.callback_query(F.data == "my_sub")
async def show_subscription(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    if not user:
        await cb.message.answer("Session not found. Run /start.")
        await _safe_cb_answer(cb)
        return

    sub_end = user.get("sub_end_date", 0)
    now = int(time.time())
    active = sub_end > now
    days_left = (sub_end - now) // 86400 if active else 0
    date_text = time.strftime("%d.%m.%Y %H:%M", time.localtime(sub_end)) if active else "—"
    status_text = "ACTIVE" if active else "INACTIVE"

    text = (
        "<b>root@fsociety:~#</b> <i>fsociety_stats...</i>\n\n"
        "<b>┌───────[ ACCESS LEVEL ]───────</b>\n"
        f"<b>│</b> User:   <code>@{user['username']}</code>\n"
        f"<b>│</b> Status: <code>{status_text}</code>\n"
        f"<b>│</b> TTL:    <code>{days_left}d</code>\n"
        f"<b>│</b> Until:  <code>{date_text}</code>\n"
        "<b>└───────────────────────────</b>"
    )

    await _safe_edit_media(
        cb, text,
        reply_markup=kb_sub_active(),
        asset_key="profile",
    )
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "show_configs")
async def cb_show_configs(cb: CallbackQuery):
    devices = await get_user_devices(cb.from_user.id)
    if not devices:
        await _safe_cb_answer(cb, "Configs not found.", show_alert=True)
        return

    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <i>ls /opt/fsociety/payloads/</i>\n\nAvailable configurations:",
        reply_markup=kb_configs(devices),
        asset_key="configs",
    )
    await _safe_cb_answer(cb)

@router.callback_query(F.data.startswith("dl_cfg_"))
async def cb_download_config(cb: CallbackQuery, bot: Bot):
    dev_id = int(cb.data.split("_", 2)[2])
    dev = await get_device(dev_id)
    if not dev or dev["user_id"] != cb.from_user.id:
        await _safe_cb_answer(cb, "Access denied.", show_alert=True)
        return

    cfg = decrypt_config(dev["config_text"])
    if not cfg:
        await _safe_cb_answer(cb, "Decryption error.", show_alert=True)
        return

    client_name = dev.get("client_name") or f"fsociety_{dev['server_name']}_{dev['device_type']}"

    if dev["device_type"] == "mtproto":
        await bot.send_message(
            cb.from_user.id,
            f"<b>root@fsociety:~#</b> <code>extract_payload --srv={dev['server_name']} --type=MTPROTO</code>\n\n"
            f"Tap to connect:\n👉 <a href='{cfg}'>MTProto Proxy</a>\n\n"
            f"<code>{cfg}</code>",
            disable_web_page_preview=True,
        )
        await _safe_cb_answer(cb, "Payload delivered.")
        return

    filename = f"{client_name}.conf"
    file = BufferedInputFile(cfg.encode("utf-8"), filename=filename)

    await bot.send_document(
        cb.from_user.id,
        document=file,
        caption=f"<b>root@fsociety:~#</b> <code>extract_payload --srv={dev['server_name']} --type={dev['device_type'].upper()}</code>",
    )
    await _safe_cb_answer(cb, "Payload delivered.")

# Платежи
@router.callback_query(F.data == "buy_sub")
async def buy_subscription(cb: CallbackQuery, state: FSMContext):
    if await has_pending_payment(cb.from_user.id):
        await _safe_cb_answer(cb, "Duplicate request in queue.", show_alert=True)
        return

    devices = await get_user_devices(cb.from_user.id)
    is_renewal = bool(devices)

    details = await get_setting("payment_details") or "2200 7021 2828 2824"
    amount = await get_setting("payment_amount") or "250 RUB"

    payment_type = "RENEWAL" if is_renewal else "NEW"
    text = (
        f"<b>root@fsociety:~#</b> <code>initiate_payment --type={payment_type}</code>\n\n"
        f"<blockquote><b>TARGET:</b>\n<code>{details}</code>\n\n"
        f"<b>AMOUNT:</b> <code>{amount}</code></blockquote>\n\n"
        "Upload <b>receipt screenshot</b> as single photo.\n"
        "<i>Request auto-destructs in 24h.</i>"
    )
    await _safe_edit_media(cb, text, reply_markup=kb_cancel_payment(), asset_key="payment")
    await state.set_state(PaymentState.waiting_for_receipt)
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "instructions")
async def show_instructions(cb: CallbackQuery):
    text = (
        "<b>root@fsociety:~#</b> <code>cat /docs/getting_started.txt</code>\n\n"
        "<b>1.</b> Download the client:\n"
        '<b>• Android</b> — <a href="https://play.google.com/store/apps/details?id=org.amnezia.awg&pcampaignid=web_share">AmneziaWG</a>\n'
        '<b>• iOS</b> — <a href="https://apps.apple.com/us/app/amneziawg/id6478942365">AmneziaWG</a>\n'
        '<b>• Windows</b> — <a href="https://wiresock.net/wiresock-vpn-client/download">WireSock Secure Connect</a>\n'
        '<b>• Linux</b> — <a href="https://github.com/paranoidalwate/awg_daemon">awg-daemon</a>\n\n'
        "<b>2.</b> Receive the <code>.conf</code> files (for PC/PH) and MTProto links after payment and confirmation by the root.\n\n"
        "<b>3.</b> Import the downloaded files into the clients listed above."
    )
    await _safe_edit_media(cb, text, reply_markup=kb_back(), asset_key="settings")
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "cancel_payment")
async def cancel_payment(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <code>kill -9 payment_session</code>\n\nOperation aborted.",
        reply_markup=kb_main(),
        asset_key="menu",
    )
    await _safe_cb_answer(cb)

@router.message(PaymentState.waiting_for_receipt, F.photo)
async def process_receipt(message: Message, state: FSMContext, bot: Bot):
    if await has_pending_payment(message.from_user.id):
        await message.answer(
            "<b>root@fsociety:~#</b> <code>warn: duplicate_request</code>\n\n"
            "You already have an active request in queue.",
            reply_markup=kb_main(),
        )
        await state.clear()
        return

    devices = await get_user_devices(message.from_user.id)
    payment_type = "renewal" if devices else "new"
    payment_id = await create_payment(message.from_user.id, payment_type)

    await message.answer(
        "<b>root@fsociety:~#</b> <code>upload receipt.png</code>\n\n"
        "Receipt transmitted.\nAwaiting C&C verdict.",
        reply_markup=kb_main(),
    )
    await state.clear()

    caption = (
        f"<b>root@fsociety:~#</b> <code>log /var/inbox/{payment_id}.msg</code>\n\n"
        f"<blockquote>TYPE:  <code>{payment_type}</code>\n"
        f"FROM:  @{message.from_user.username or 'unknown'}\n"
        f"UID:   <tg-spoiler>{message.from_user.id}</tg-spoiler></blockquote>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=kb_payment_admin(payment_id),
            )
        except TelegramForbiddenError:
            logger.warning(f"[ BLOCKED ] Администратор: {admin_id} — заблокировал бота.")
        except Exception as e:
            logger.error(f"[ FAIL ] Уведомление администратора {admin_id}: {e}.")

@router.message(PaymentState.waiting_for_receipt)
async def process_receipt_invalid(message: Message):
    await message.answer(
        "<b>root@fsociety:~#</b> <code>err: invalid_format</code>\n\n"
        "Send <b>screenshot</b> as single photo. Or abort.",
        reply_markup=kb_cancel_payment(),
    )
