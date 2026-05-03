import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import VPN_SERVERS, DEV_MODE
from infrastructure.vpn_providers.awg_easy import AwgEasyAPI
from infrastructure.database.db import (
    init_db, close_db, get_all_users, get_user, get_user_devices,
    get_all_devices, delete_user, delete_all_users, adjust_user_subscription,
    get_stats,
)
from core.crypto import init_crypto

UNLIMITED_DAYS = 36500


async def delete_wg_client(server_name: str, client_uuid: str):
    if DEV_MODE:
        return True, f"[ MOCK ] Purged: {client_uuid}"

    if server_name not in VPN_SERVERS:
        return False, f"Node {server_name} offline"
    config = VPN_SERVERS[server_name]
    api = AwgEasyAPI(config["url"], config["password"], name=server_name)
    try:
        await api.delete_client(client_uuid)
        return True, f"Purged: {client_uuid}"
    except Exception as e:
        return False, f"Error: {e}"
    finally:
        await api.close()


def print_menu():
    print("Fsociety Network — root@db_admin")
    print("1. Список пользователей")
    print("2. Показать статистику")
    print("3. Полная очистка пользователей")
    print("4. Удаление пользователя")
    print("5. Безлимитный доступ")
    print("6. Добавление дней подписки")
    print("7. Удаление дней подписки")
    print("0. Выход")


def safe_input(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return input().strip()
    except (EOFError, UnicodeDecodeError):
        return ""


async def main():
    await init_db()
    init_crypto()

    while True:
        print_menu()
        choice = safe_input("root@fsociety:~# ")
        if choice == "0":
            print("Goodbye, Friend.")
            break

        elif choice == "1":
            users = await get_all_users()
            if not users:
                print("\n[ INFO ] Пользователи отсутствуют.\n")
                continue
            print(f"\n{'ID':<15} {'Username':<20} {'Status':<12} {'TTL':<12}")
            print("-" * 65)
            for u in users:
                sub_str = time.strftime("%d.%m.%Y", time.localtime(u["sub_end_date"])) if u["sub_end_date"] > time.time() else "EXPIRED"
                print(f"{u['tg_id']:<15} {u['username'] or 'N/A':<20} {u['status']:<12} {sub_str:<12}")
            print()

        elif choice == "2":
            stats = await get_stats()
            devices = await get_all_devices()
            print(f"\n[ STATS ]\n  Agents:    {stats['total']}\n  Active:    {stats['active']}\n  Expired:   {stats['expired']}\n  Pending:   {stats['pending']}\n  Devices:   {len(devices)}\n")

        elif choice == "3":
            confirm = safe_input("Ты осознаешь возможные последствия? (y/n)")
            if confirm.lower() == "y":
                devices = await get_all_devices()
                purged = 0
                for dev in devices:
                    awg_id = str(dev.get("awg_id", "")).strip()
                    if awg_id and awg_id.lower() not in ("none", "null", "0", ""):
                        success, msg = await delete_wg_client(dev["server_name"], awg_id)
                        if success:
                            purged += 1
                await delete_all_users()
                print(f"[ OK ] Завершена полная очистка пользователей. Удалено: {purged}.\n")
            else:
                print("[ CANCELLED ] Полная очистка пользователей отменена.\n")

        elif choice == "4":
            tg_id_str = safe_input("Telegram ID пользователя: ")
            if not tg_id_str:
                print("[ ERROR ] Строка пустая.\n")
                continue
            try:
                tg_id = int(tg_id_str)
            except ValueError:
                print("[ ERROR ] Невалидный Telegram ID.\n")
                continue
            user = await get_user(tg_id)
            if not user:
                print("[ ERROR ] Пользователь с данным Telegram ID — не был найден.\n")
                continue
            confirm = safe_input(f"Удалить: {tg_id} ({user.get('username')})? (y/n)")
            if confirm.lower() == "y":
                devices = await get_user_devices(tg_id)
                wg_results = []
                for dev in devices:
                    awg_id = str(dev.get("awg_id", "")).strip()
                    if awg_id and awg_id.lower() not in ("none", "null", "0", ""):
                        success, msg = await delete_wg_client(dev["server_name"], awg_id)
                        wg_results.append(f"  {dev['server_name']}/{dev['device_type']}: {msg}")
                    else:
                        wg_results.append(f"  {dev['server_name']}/{dev['device_type']}: No UUID")
                await delete_user(tg_id)
                print("[ OK ] Пользователь удален.")
                for r in wg_results:
                    print(r)
            else:
                print("[ CANCELLED ] Удаление пользователя отменено.\n")
            print()

        elif choice == "5":
            tg_id_str = safe_input("Telegram ID пользователя: ")
            if not tg_id_str:
                continue
            try:
                tg_id = int(tg_id_str)
            except ValueError:
                continue
            user = await get_user(tg_id)
            if not user:
                print("[ ERROR ] Пользователь с данным Telegram ID — не был найден.\n")
                continue
            new_end = await adjust_user_subscription(tg_id, UNLIMITED_DAYS)
            print(f"[ OK ] Подписка продлена до: {time.strftime('%d.%m.%Y', time.localtime(new_end))}.\n")

        elif choice == "6":
            tg_id_str = safe_input("Telegram ID пользователя: ")
            if not tg_id_str:
                continue
            try:
                tg_id = int(tg_id_str)
            except ValueError:
                continue
            days_str = safe_input("Количество дней: ")
            try:
                days = int(days_str)
            except ValueError:
                continue
            user = await get_user(tg_id)
            if not user:
                print("[ ERROR ] Пользователь с данным Telegram ID — не был найден.\n")
                continue
            new_end = await adjust_user_subscription(tg_id, days)
            print(f"[ OK ] Добавлено {days} дней. Новый TTL: {time.strftime('%d.%m.%Y', time.localtime(new_end))}.\n")

        elif choice == "7":
            tg_id_str = safe_input("Telegram ID пользователя: ")
            if not tg_id_str:
                continue
            try:
                tg_id = int(tg_id_str)
            except ValueError:
                continue
            days_str = safe_input("Количество дней: ")
            try:
                days = int(days_str)
            except ValueError:
                continue
            user = await get_user(tg_id)
            if not user:
                print("[ ERROR ] Пользователь с данным Telegram ID — не был найден.\n")
                continue
            new_end = await adjust_user_subscription(tg_id, -days)
            print(f"[ OK ] Удалено {days} дней. Новый TTL: {time.strftime('%d.%m.%Y', time.localtime(new_end))}.\n")

        else:
            print("[ ERROR ] Невалидный выбор.\n")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
