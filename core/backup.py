import logging
import aiosqlite
from pathlib import Path
from datetime import datetime

from aiogram import Bot
from aiogram.types import FSInputFile

from config import BACKUP_CHAT_ID
from infrastructure.database.db import DB_PATH
from core.crypto import get_fernet

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("logs/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_MAX_BACKUPS = 14

async def create_backup() -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"fsociety_network_bot-{ts}.sqlite"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("VACUUM INTO ?", (target.as_posix(),))
        await db.commit()
    logger.info(f"[ OK ] Создался снапшот: {target.name}.")

    fernet = get_fernet()
    if fernet:
        enc_target = target.with_suffix(".sqlite.enc")
        data = target.read_bytes()
        enc_target.write_bytes(fernet.encrypt(data))
        target.unlink()
        target = enc_target
        logger.info(f"[ OK ] Зашифровался снапшот: {target.name}.")
    return target

async def send_backup(bot: Bot) -> None:
    if not BACKUP_CHAT_ID:
        logger.warning("[ WARN ] Переменная: \"BACKUP_CHAT_ID\" отсутствует.")
        return
    try:
        path = await create_backup()
        size_kb = path.stat().st_size / 1024
        enc_note = " [ ENCRYPTED ]" if path.suffix == ".enc" else ""
        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        caption = (
            f"<b>root@fsociety:~#</b> <code>tar czvf backup.tar.gz /data</code>\n\n"
            f"<blockquote>TIMESTAMP: <code>{timestamp}Z</code>\n"
            f"SIZE:      <code>{size_kb:.1f} KB</code>\n"
            f"STATUS:    {enc_note}</blockquote>\n\n"
        )
        await bot.send_document(
            BACKUP_CHAT_ID,
            document=FSInputFile(path),
            caption=caption,
            parse_mode="HTML",
        )
        logger.info(f"[ OK ] Снапшот доставлен → {BACKUP_CHAT_ID}.")

        all_files = sorted(BACKUP_DIR.glob("fsociety_*.sqlite*"))
        for old in all_files[:-_MAX_BACKUPS]:
            try:
                old.unlink()
                logger.info(f"[ OK ] Удален устаревший снапшот: {old.name}.")
            except Exception as e:
                logger.error(f"[ FAIL ] Ошибка ротации {old}: {e}.")
    except Exception as e:
        logger.exception(f"[ FAIL ] Катастрофическая ошибка снапшота: {e}.")
        try:
            await bot.send_message(
                BACKUP_CHAT_ID,
                f"<b>root@fsociety:~#</b> <code>backup --status</code>\n\n"
                f"<blockquote><b>[ CRITICAL ]</b>\n<code>{e}</code></blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            pass
