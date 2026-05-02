import aiosqlite
import asyncio
import os
import time
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional, Any

from core.crypto import get_fernet

logger = logging.getLogger(__name__)

DB_DIR = os.getenv("DB_DIR", "data")
DB_PATH = os.path.join(DB_DIR, "database.sqlite")
os.makedirs(DB_DIR, exist_ok=True)

_SCHEMA_VERSION = 5
_db_semaphore = asyncio.Semaphore(20)


@asynccontextmanager
async def _get_db():
    async with _db_semaphore:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db


async def get_db_connection():
    return _get_db()


async def close_db() -> None:
    logger.info("[ OK ] Пул соединений базы данных освобождён.")


async def _execute(query: str, params: tuple = ()) -> aiosqlite.Cursor:
    async with _get_db() as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur


async def _fetchall(query: str, params: tuple = ()) -> list[dict]:
    async with _get_db() as db:
        async with db.execute(query, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def _fetchone(query: str, params: tuple = ()) -> Optional[dict]:
    async with _get_db() as db:
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def _apply_migrations() -> None:
    current_ver = 0
    try:
        row = await _fetchone("SELECT value FROM settings WHERE key = ?", ("schema_version",))
        if row and row.get("value"):
            current_ver = int(row["value"])
    except (aiosqlite.OperationalError, sqlite3.OperationalError):
        current_ver = 0

    logger.info(f"[ OK ] Версия схемы: {current_ver}.")

    if current_ver < 1:
        await _migrate_v1()
    if current_ver < 2:
        await _migrate_v2()
    if current_ver < 3:
        await _migrate_v3()
    if current_ver < 4:
        await _migrate_v4()
    if current_ver < 5:
        await _migrate_v5()
    if current_ver < _SCHEMA_VERSION:
        await set_setting("schema_version", str(_SCHEMA_VERSION))
        logger.info(f"[ OK ] Схема обновлена до: {_SCHEMA_VERSION}.")


async def _migrate_v1() -> None:
    async with _get_db() as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                sub_end_date INTEGER DEFAULT 0,
                status TEXT DEFAULT 'inactive',
                server_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                server_name TEXT,
                awg_id TEXT,
                device_type TEXT,
                config_text TEXT,
                client_name TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(tg_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                status TEXT DEFAULT 'pending',
                payment_type TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS fsm_states (
                key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_sub_end ON users(sub_end_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_fsm_updated ON fsm_states(updated_at)")
        await db.commit()
    logger.info("[ OK ] Завершена миграция: v1.")


async def _migrate_v2() -> None:
    fernet = get_fernet()
    if not fernet:
        logger.warning("[ WARN ] Ключ для миграции: v2 — не найден, все остается в открытом виде.")
        return
    rows = await _fetchall(
        "SELECT id, config_text FROM devices WHERE config_text IS NOT NULL AND config_text != ''"
    )
    async with _get_db() as db:
        updated = 0
        for row in rows:
            raw = row["config_text"]
            if raw.startswith("gAAAAAB"):
                continue
            try:
                enc = fernet.encrypt(raw.encode()).decode()
                await db.execute("UPDATE devices SET config_text = ? WHERE id = ?", (enc, row["id"]))
                updated += 1
            except Exception as exc:
                logger.error(f"[ FAIL ] Шифрование строки {row['id']}: {exc}.")
        await db.commit()
    logger.info(f"[ OK ] Завершена миграция: v2. Защифровано: {updated}.")


async def _migrate_v3() -> None:
    async with _get_db() as db:
        cursor = await db.execute("PRAGMA table_info(devices)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'client_name' not in columns:
            await db.execute("ALTER TABLE devices ADD COLUMN client_name TEXT DEFAULT ''")
            await db.commit()
            logger.info("[ OK ] Завершена миграция: v3. Добавлена колонка: \"client_name\".")


async def _migrate_v4() -> None:
    async with _get_db() as db:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_devices_server ON devices(server_name)")
        await db.commit()
    logger.info("[ OK ] Завершена миграция: v4. Индексы: \"device_type\" и \"server_name\" — готовы.")


async def _migrate_v5() -> None:
    async with _get_db() as db:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status_partial ON payments(status) WHERE status IN ('pending','partial')")
        await db.commit()
    logger.info("[ OK ] Завершена миграция: v5. Активна поддержка: \"provisioning support\".")


async def init_db() -> None:
    await _apply_migrations()


# Пользователи
async def get_user(tg_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def add_user(tg_id: int, username: str) -> None:
    await _execute("INSERT OR IGNORE INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username))


async def update_user_subscription(tg_id: int, days_added: int, server_name: Optional[str] = None) -> int:
    user = await get_user(tg_id)
    current = int(time.time())
    if user and user.get('sub_end_date', 0) > current:
        new_end = user['sub_end_date'] + (days_added * 86400)
    else:
        new_end = current + (days_added * 86400)
    srv = server_name or (user.get('server_name') if user else None)
    await _execute(
        "UPDATE users SET sub_end_date = ?, status = 'active', server_name = ? WHERE tg_id = ?",
        (new_end, srv, tg_id),
    )
    return new_end


async def adjust_user_subscription(tg_id: int, delta_days: int) -> int:
    user = await get_user(tg_id)
    if not user:
        raise ValueError("Агент не был найден.")
    current = int(time.time())
    current_end = user.get('sub_end_date', 0)
    base = current_end if current_end > current else current
    new_end = max(current, base + (delta_days * 86400))
    status = 'active' if new_end > current else 'expired'
    await _execute(
        "UPDATE users SET sub_end_date = ?, status = ? WHERE tg_id = ?",
        (new_end, status, tg_id),
    )
    return new_end


async def update_user_status(tg_id: int, status: str) -> None:
    await _execute("UPDATE users SET status = ? WHERE tg_id = ?", (status, tg_id))


async def get_expired_users() -> list[Any]:
    current = int(time.time())
    return await _fetchall(
        "SELECT tg_id, sub_end_date FROM users WHERE sub_end_date < ? AND status = 'active'", (current,)
    )


async def get_expiring_soon_users(days: int = 1) -> list[Any]:
    current = int(time.time())
    future = current + (days * 86400)
    return await _fetchall(
        "SELECT tg_id FROM users WHERE sub_end_date > ? AND sub_end_date <= ? AND status = 'active'",
        (current, future),
    )


async def get_active_users() -> list[Any]:
    return await _fetchall("SELECT tg_id FROM users WHERE status = 'active'")


async def get_all_users() -> list[dict]:
    return await _fetchall("SELECT * FROM users")


async def delete_user(tg_id: int) -> None:
    await _execute("DELETE FROM devices WHERE user_id = ?", (tg_id,))
    await _execute("DELETE FROM payments WHERE tg_id = ?", (tg_id,))
    await _execute("DELETE FROM users WHERE tg_id = ?", (tg_id,))


async def delete_all_users() -> None:
    await _execute("DELETE FROM devices")
    await _execute("DELETE FROM payments")
    await _execute("DELETE FROM users")


# Устройства
async def add_device(
    user_id: int,
    awg_id: str,
    config_text: str,
    server_name: str,
    device_type: str,
    client_name: str = "",
) -> None:
    await _execute(
        "INSERT INTO devices (user_id, awg_id, config_text, server_name, device_type, client_name) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, awg_id, config_text, server_name, device_type, client_name),
    )


async def get_user_devices(user_id: int) -> list[dict]:
    return await _fetchall("SELECT * FROM devices WHERE user_id = ?", (user_id,))


async def get_user_devices_by_type(user_id: int, device_type: str) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM devices WHERE user_id = ? AND device_type = ?", (user_id, device_type)
    )


async def get_device(device_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))


async def get_all_devices() -> list[dict]:
    return await _fetchall("SELECT * FROM devices")


async def delete_user_devices(user_id: int) -> None:
    await _execute("DELETE FROM devices WHERE user_id = ?", (user_id,))


async def get_expired_devices(cutoff_days: int = 30) -> list[dict]:
    cutoff = int(time.time()) - (cutoff_days * 86400)
    return await _fetchall(
        "SELECT d.* FROM devices d JOIN users u ON d.user_id = u.tg_id "
        "WHERE u.status = 'expired' AND u.sub_end_date < ?",
        (cutoff,),
    )


async def delete_device_record(device_id: int) -> None:
    await _execute("DELETE FROM devices WHERE id = ?", (device_id,))


# Платежи
async def create_payment(tg_id: int, payment_type: str = "new") -> int:
    cur = await _execute(
        "INSERT INTO payments (tg_id, payment_type, status) VALUES (?, ?, 'pending')",
        (tg_id, payment_type),
    )
    return cur.lastrowid


async def get_payment(payment_id: int) -> Optional[dict]:
    return await _fetchone("SELECT * FROM payments WHERE id = ?", (payment_id,))


async def get_pending_payments(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with _get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT p.*, u.username FROM payments p LEFT JOIN users u ON p.tg_id = u.tg_id "
            "WHERE p.status = 'pending' ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return rows, total


async def has_pending_payment(tg_id: int) -> bool:
    row = await _fetchone(
        "SELECT 1 FROM payments WHERE tg_id = ? AND status = 'pending' LIMIT 1", (tg_id,)
    )
    return row is not None


async def update_payment_status(payment_id: int, status: str) -> Optional[dict]:
    async with _get_db() as db:
        async with db.execute(
            "UPDATE payments SET status = ? WHERE id = ? AND status = 'pending' RETURNING *",
            (status, payment_id),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()
        return dict(row) if row else None


async def set_payment_status(payment_id: int, status: str) -> None:
    await _execute("UPDATE payments SET status = ? WHERE id = ?", (status, payment_id))


async def get_partial_payments() -> list[dict]:
    return await _fetchall("SELECT * FROM payments WHERE status = 'partial'")


async def expire_old_pending_payments(hours: int = 24) -> int:
    cutoff_ts = int(time.time()) - hours * 3600
    cur = await _execute(
        "UPDATE payments SET status = 'expired' "
        "WHERE status = 'pending' AND CAST(strftime('%s', created_at) AS INTEGER) < ?",
        (cutoff_ts,),
    )
    return cur.rowcount or 0


# Настройки
async def get_setting(key: str) -> Optional[str]:
    row = await _fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    await _execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )


# Очистка FSM хранилища
async def cleanup_fsm_states(hours: int = 48) -> int:
    cur = await _execute(
        "DELETE FROM fsm_states WHERE updated_at < datetime('now', ?)",
        (f"-{hours} hours",),
    )
    return cur.rowcount or 0


# Статистика
async def get_stats() -> dict:
    async with _get_db() as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute("SELECT COUNT(*) FROM users WHERE status='active'")).fetchone())[0]
        expired = (await (await db.execute("SELECT COUNT(*) FROM users WHERE status='expired'")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")).fetchone())[0]
        return {"total": total, "active": active, "expired": expired, "pending": pending}
