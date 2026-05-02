import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from infrastructure.database.db import (
    update_payment_status, update_user_subscription,
    get_user_devices, get_all_users, get_stats,
    get_pending_payments, get_setting, set_setting, set_payment_status,
)
from core.crypto import decrypt_config
from core.ui import (
    _safe_edit_media, _safe_cb_answer,
    kb_admin_main, kb_admin_settings, kb_back, kb_broadcast_confirm,
    kb_pending_nav, kb_payment_admin,
)
from domain.provisioning import get_provisioner, generate_client_name
from config import ADMIN_IDS, SUBSCRIPTION_DAYS, BROADCAST_BATCH_SIZE, BROADCAST_DELAY

logger = logging.getLogger(__name__)
router = Router()

router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_broadcast_confirm = State()
    waiting_payment_details = State()
    waiting_payment_amount = State()

PENDING_PAGE_SIZE = 5


# Команды
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    stats = await get_stats()
    text = (
        "<b>root@fsociety:~#</b> <code>nmap -sP botnet</code>\n\n"
        "<b>┌───────[ BOTNET STATUS ]───────</b>\n"
        f"<b>│</b> Agents: <code>{stats['total']}</code>\n"
        f"<b>│</b> Active: <code>{stats['active']}</code>\n"
        f"<b>│</b> Expired:   <code>{stats['expired']}</code>\n"
        f"<b>│</b> Queue:  <code>{stats['pending']}</code>\n"
        "<b>└───────────────────────────</b>"
    )
    await message.answer(text, reply_markup=kb_admin_main())

@router.callback_query(F.data == "back_to_admin")
async def cb_back_admin(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    stats = await get_stats()
    text = (
        "<b>root@fsociety:~#</b> <code>nmap -sP botnet</code>\n\n"
        "<b>┌───────[ BOTNET STATUS ]───────</b>\n"
        f"<b>│</b> Agents: <code>{stats['total']}</code>\n"
        f"<b>│</b> Active: <code>{stats['active']}</code>\n"
        f"<b>│</b> Expired:   <code>{stats['expired']}</code>\n"
        f"<b>│</b> Queue:  <code>{stats['pending']}</code>\n"
        "<b>└───────────────────────────</b>"
    )
    await _safe_edit_media(cb, text, reply_markup=kb_admin_main(), asset_key="menu")
    await _safe_cb_answer(cb)

# Заявки на оплату
@router.callback_query(F.data == "admin_pending")
async def cb_pending(cb: CallbackQuery):
    await _pending_page(cb, 0)

@router.callback_query(F.data.startswith("admin_pending:"))
async def cb_pending_page(cb: CallbackQuery):
    page = int(cb.data.split(":", 1)[1])
    await _pending_page(cb, page)

async def _pending_page(cb: CallbackQuery, page: int):
    payments, total = await get_pending_payments(
        limit=PENDING_PAGE_SIZE, offset=page * PENDING_PAGE_SIZE
    )
    if not payments:
        await _safe_edit_media(
            cb,
            '<b>root@fsociety:~#</b> <code>grep -i "pending" /var/queue/</code>\n\n<i>Queue is empty.</i>',
            reply_markup=kb_back(),
            asset_key="menu",
        )
        await _safe_cb_answer(cb)
        return

    pages = (total - 1) // PENDING_PAGE_SIZE + 1
    lines = [f"<b>root@fsociety:~#</b> <code>tail -n 5 /var/deaddrops.log</code>\n"]
    lines.append(f"Page {page + 1}/{pages}\n")
    for p in payments:
        uname = p.get("username") or "unknown"
        lines.append(
            f"<code>#{p['id']}</code> | @{uname} | <tg-spoiler>{p['tg_id']}</tg-spoiler>\n"
            f"   └── <i>{p['payment_type']}</i> @ {p['created_at']}"
        )

    hasPrev = page > 0
    hasNext = (page + 1) * PENDING_PAGE_SIZE < total

    await _safe_edit_media(
        cb,
        "\n".join(lines),
        reply_markup=kb_pending_nav(page, hasPrev, hasNext),
        asset_key="menu",
    )
    await _safe_cb_answer(cb)

# Статистика и настройки
@router.callback_query(F.data == "admin_stats")
async def cb_stats(cb: CallbackQuery):
    stats = await get_stats()
    text = (
        "<b>root@fsociety:~#</b> <code>nmap -sP botnet</code>\n\n"
        "<b>┌───────[ BOTNET STATUS ]───────</b>\n"
        f"<b>│</b> Agents: <code>{stats['total']}</code>\n"
        f"<b>│</b> Active: <code>{stats['active']}</code>\n"
        f"<b>│</b> Expired:   <code>{stats['expired']}</code>\n"
        f"<b>│</b> Queue:  <code>{stats['pending']}</code>\n"
        "<b>└───────────────────────────</b>"
    )
    await _safe_edit_media(cb, text, reply_markup=kb_back(), asset_key="menu")
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "admin_settings")
async def cb_settings(cb: CallbackQuery):
    details = await get_setting("payment_details") or "Not set"
    amount = await get_setting("payment_amount") or "250 RUB"
    text = (
        "<b>root@fsociety:~#</b> <code>cat /etc/payment.cfg</code>\n\n"
        f"<blockquote><b>TARGET:</b>\n<code>{details}</code>\n\n"
        f"<b>SUM:</b> <code>{amount}</code></blockquote>"
    )
    await _safe_edit_media(cb, text, reply_markup=kb_admin_settings(), asset_key="settings")
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "admin_set_details")
async def cb_set_details(cb: CallbackQuery, state: FSMContext):
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <code>nano /etc/payment.cfg</code>\n\nEnter new bank details:",
        kb_back(),
        asset_key="settings",
    )
    await state.set_state(AdminState.waiting_payment_details)
    await _safe_cb_answer(cb)

@router.callback_query(F.data == "admin_set_amount")
async def cb_set_amount(cb: CallbackQuery, state: FSMContext):
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <code>nano /etc/payment.cfg</code>\n\nEnter new price:",
        kb_back(),
        asset_key="settings",
    )
    await state.set_state(AdminState.waiting_payment_amount)
    await _safe_cb_answer(cb)

@router.message(AdminState.waiting_payment_details)
async def do_set_details(message: Message, state: FSMContext):
    await set_setting("payment_details", message.text)
    await message.answer(
        "<b>root@fsociety:~#</b> <code>[ OK ] target updated</code>\n\nBank details overwritten.",
        reply_markup=kb_admin_main(),
    )
    await state.clear()

@router.message(AdminState.waiting_payment_amount)
async def do_set_amount(message: Message, state: FSMContext):
    await set_setting("payment_amount", message.text)
    await message.answer(
        "<b>root@fsociety:~#</b> <code>[ OK ] sum updated</code>\n\nPrice updated.",
        reply_markup=kb_admin_main(),
    )
    await state.clear()

# Рассылка
@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(cb: CallbackQuery, state: FSMContext):
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <code>deploy --mass --target=all</code>\n\nEnter payload. HTML allowed.",
        reply_markup=kb_back(),
        asset_key="settings",
    )
    await state.set_state(AdminState.waiting_broadcast)
    await _safe_cb_answer(cb)

@router.message(AdminState.waiting_broadcast)
async def do_broadcast_preview(message: Message, state: FSMContext):
    await state.update_data(broadcast_text=message.text)
    await message.answer(
        f"<b>root@fsociety:~#</b> <code>deploy --dry-run</code>\n\n"
        f"{message.text}\n\n"
        "<i>Confirm deployment?</i>",
        reply_markup=kb_broadcast_confirm(),
    )
    await state.set_state(AdminState.waiting_broadcast_confirm)

@router.callback_query(F.data == "broadcast_confirm", AdminState.waiting_broadcast_confirm)
async def cb_broadcast_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()
    await _safe_cb_answer(cb, "deploying...")

    users = await get_all_users()
    ok, blocked, errors = 0, 0, 0
    total = len(users)

    for i in range(0, total, BROADCAST_BATCH_SIZE):
        batch = users[i:i + BROADCAST_BATCH_SIZE]
        for u in batch:
            try:
                await bot.send_message(u["tg_id"], f"<b>fsociety</b>\n\n{text}")
                ok += 1
            except TelegramForbiddenError:
                blocked += 1
            except Exception:
                errors += 1
        if i + BROADCAST_BATCH_SIZE < total:
            await asyncio.sleep(BROADCAST_DELAY)

    result_text = (
        "<b>root@fsociety:~#</b> <code>deploy --status</code>\n\n"
        "<b>┌───────[ BROADCAST LOG ]───────</b>\n"
        f"<b>│</b> Sent:    <code>{ok}</code>\n"
        f"<b>│</b> Blocked: <code>{blocked}</code>\n"
        f"<b>│</b> Failed:  <code>{errors}</code>\n"
        "<b>└───────────────────────────</b>"
    )
    await _safe_edit_media(cb, result_text, reply_markup=kb_admin_main(), asset_key="menu")

@router.callback_query(F.data == "broadcast_cancel", AdminState.waiting_broadcast_confirm)
async def cb_broadcast_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_media(
        cb,
        "<b>root@fsociety:~#</b> <code>kill -9 broadcast</code>\n\nDeployment aborted.",
        reply_markup=kb_admin_main(),
        asset_key="menu",
    )
    await _safe_cb_answer(cb)

# Выдача набора
async def _deliver_bundle(bot: Bot, tg_id: int):
    devices = await get_user_devices(tg_id)
    for dev in devices:
        cfg = decrypt_config(dev["config_text"])
        if not cfg:
            continue
        client_name = dev.get("client_name") or f"fsociety_{dev['server_name']}_{dev['device_type']}"

        if dev["device_type"] == "mtproto":
            try:
                await bot.send_message(
                    tg_id,
                    f"<b>root@fsociety:~#</b> <code>extract_payload --srv={dev['server_name']} --type=MTPROTO</code>\n\n"
                    f"Tap to connect:\n👉 <a href='{cfg}'>MTProto Proxy</a>\n\n"
                    f"<code>{cfg}</code>",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"[ FAIL ] Доставка MTProto для {tg_id}: {e}.")
        else:
            filename = f"{client_name}.conf"
            file = BufferedInputFile(cfg.encode("utf-8"), filename=filename)
            try:
                await bot.send_document(
                    tg_id,
                    document=file,
                    caption=f"<b>root@fsociety:~#</b> <code>extract_payload --srv={dev['server_name']} --type={dev['device_type'].upper()}</code>",
                )
            except Exception as e:
                logger.error(f"[ FAIL ] Доставка {filename} для {tg_id}: {e}.")

# Подтверждение и отклонение
@router.callback_query(F.data.startswith("approve_"))
async def approve_payment(cb: CallbackQuery, bot: Bot):
    await _safe_cb_answer(cb, "processing request...")
    payment_id = int(cb.data.split("_", 1)[1])

    payment = await update_payment_status(payment_id, "approved")
    if not payment:
        old = cb.message.caption or ""
        await _safe_edit_media(
            cb, old + "\n\n<b>[ INFO ]</b> Already processed.", asset_key="menu"
        )
        return

    tg_id = payment["tg_id"]
    provisioner = get_provisioner()

    devices = await get_user_devices(tg_id)

    # Продление — активация существующих устройств на всех нодах
    if devices:
        enabled = 0
        for dev in devices:
            provider = provisioner.get_provider(dev["server_name"], dev["device_type"])
            if not provider:
                continue
            try:
                await provider.enable_access(dev["awg_id"])
                enabled += 1
            except Exception as e:
                logger.error(f"[ FAIL ] Активация {dev['awg_id']}: {e}.")

        await update_user_subscription(tg_id, SUBSCRIPTION_DAYS)

        try:
            await bot.send_message(
                tg_id,
                f"<b>root@fsociety:~#</b> <code>renew --uid {tg_id}</code>\n\n"
                f"Access renewed.\nActive configs: <code>{enabled}</code>"
            )
        except TelegramForbiddenError:
            pass

        old = cb.message.caption or ""
        await _safe_edit_media(
            cb, old + f"\n\n<b>[ OK ]</b> Renewal · active: <code>{enabled}</code>", asset_key="menu"
        )
        return

    # Новая подписка — полная выдача набора
    await update_user_subscription(tg_id, SUBSCRIPTION_DAYS)

    try:
        await bot.send_message(
            tg_id,
            f"<b>root@fsociety:~#</b> <code>confirm_payment #{payment_id}</code>\n\n"
            "Payment confirmed.\nGenerating full bundle..."
        )
    except TelegramForbiddenError:
        pass

    result = await provisioner.provision(tg_id, payment_id)

    if provisioner.is_fully_successful(result):
        await _deliver_bundle(bot, tg_id)
        total = len(result.success)
        old = cb.message.caption or ""
        tail = f"\n\n<b>[ OK ]</b> Deployed · <code>{total}/{total}</code>"
        await _safe_edit_media(cb, old + tail, asset_key="menu")
    else:
        failed = [k for k, v in result.success.items() if not v]
        await set_payment_status(payment_id, "partial")
        try:
            await bot.send_message(
                tg_id,
                f"<b>root@fsociety:~#</b> <code>bundle --status</code>\n\n"
                f"Partial deployment. Some nodes are degraded.\n"
                f"We will auto-retry every 10 minutes.\n\n"
                f"Failed: <code>{', '.join(failed)}</code>"
            )
        except Exception:
            pass

        old = cb.message.caption or ""
        await _safe_edit_media(
            cb,
            old + f"\n\n<b>[ PARTIAL ]</b> Failed nodes: <code>{', '.join(failed)}</code>",
            asset_key="menu",
        )


@router.callback_query(F.data.startswith("reject_"))
async def reject_payment(cb: CallbackQuery, bot: Bot):
    await _safe_cb_answer(cb)
    payment_id = int(cb.data.split("_", 1)[1])

    payment = await update_payment_status(payment_id, "rejected")
    if not payment:
        old = cb.message.caption or ""
        await _safe_edit_media(cb, old + "\n\n<b>[ INFO ]</b> Already processed.", asset_key="menu")
        return

    try:
        await bot.send_message(
            payment["tg_id"],
            f"<b>root@fsociety:~#</b> <code>reject_payment #{payment_id}</code>\n\n"
            "Payment declined.\nContact C&C if you think this is an error."
        )
    except TelegramForbiddenError:
        pass

    old = cb.message.caption or ""
    await _safe_edit_media(cb, old + "\n\n<b>[ REJECTED ]</b>", asset_key="menu")
