import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_fernet = None

def init_crypto():
    global _fernet
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning("[ WARN ] Переменная: \"ENCRYPTION_KEY\" отсутствует.")
        return
    try:
        _fernet = Fernet(key.encode())
        logger.info("[ OK ] Шифрование Fernet активно.")
    except Exception as e:
        logger.error(f"[ FAIL ] Некорректная переменная: \"ENCRYPTION_KEY\": {e}.")

def get_fernet():
    return _fernet

def encrypt_config(data: str) -> str:
    if _fernet and data:
        return _fernet.encrypt(data.encode()).decode()
    return data

def decrypt_config(token: str) -> str:
    if not token:
        return ""
    if _fernet:
        try:
            return _fernet.decrypt(token.encode()).decode()
        except Exception:
            return token
    return token
