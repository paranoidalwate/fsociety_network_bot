import os
import re
import json
import signal
import logging
import tempfile
import subprocess
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)5s] %(name)-15s | %(message)s",
)
logger = logging.getLogger("mtproto_mgr")

app = FastAPI(title="MTProto-Manager")

# Пути и режим работы
CONFIG_PATH = os.getenv("MTPROTO_CONFIG", "/opt/mtproto-proxy/config.toml")
STATE_PATH = os.getenv("MTPROTO_STATE", "/data/secrets_state.json")
PID_FILE = os.getenv("MTPROTO_PID_FILE", "/run/mtproto-proxy.pid")
MTPROTO_SERVICE = os.getenv("MTPROTO_SERVICE", "mtproto-proxy")
MTPROTO_DOMAIN = os.getenv("MTPROTO_DOMAIN", "").strip().lower()


class SecretEntry(BaseModel):
    id: str
    secret: str
    enabled: bool = True


_secrets: Dict[str, SecretEntry] = {}
_counter = 0


# JSON-состояние (метаданные Sidecar)
def _load_state() -> None:
    global _secrets, _counter
    if not os.path.exists(STATE_PATH):
        _secrets = {}
        _counter = 0
        return
    try:
        _ensure_dir(STATE_PATH)
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _secrets = {k: SecretEntry(**v) for k, v in data.items()}
        if _secrets:
            _counter = max(int(k) for k in _secrets.keys() if k.isdigit())
    except Exception as e:
        logger.error(f"[ FAIL ] Не удалось загрузить состояние: {e}.")
        _secrets = {}
        _counter = 0


def _save_state() -> None:
    _ensure_dir(STATE_PATH)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {k: v.model_dump() for k, v in _secrets.items()},
            f,
            indent=2,
            ensure_ascii=False,
        )


# Генерация секретов (классические и FakeTLS)
def _generate_secret() -> str:
    import secrets as secrets_mod
    base = secrets_mod.token_hex(16)
    if MTPROTO_DOMAIN:
        domain_hex = MTPROTO_DOMAIN.encode("utf-8").hex()
        return f"ee{base}{domain_hex}"
    return base


def _extract_base_secret(secret: str) -> str:
    if secret.startswith("ee") and len(secret) >= 34:
        base = secret[2:34]
        # sanity check: must be hex
        if all(c in "0123456789abcdefABCDEF" for c in base):
            return base
    return secret


# TOML синхронизация для mtproto.zig
def _ensure_dir(path: str) -> None:
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)


def _sync_toml() -> None:
    active_items = []
    for v in _secrets.values():
        if v.enabled:
            base_sec = _extract_base_secret(v.secret)
            # Правильный формат TOML для mtproto.zig
            active_items.append(f'user_{v.id} = "{base_sec}"')

    new_body = "\n".join(active_items)

    _ensure_dir(CONFIG_PATH)

    if not os.path.exists(CONFIG_PATH):
        header = "[access.users]\n"
        content = f"{header}{new_body}\n" if new_body else f"{header}\n"
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[ SYNC ] Создан {CONFIG_PATH} с {len(active_items)} активными секретами.")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        text = f.read()

    header_re = re.compile(r'^\[access\.users\]\s*$', re.MULTILINE)
    match = header_re.search(text)

    if not match:
        text = text.rstrip("\n") + f"\n\n[access.users]\n{new_body}\n"
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"[ SYNC ] Добавлена секция: [access.users] с {len(active_items)} секретами.")
        return

    section_start = match.end()
    next_section = re.compile(r'^\[', re.MULTILINE).search(text, section_start)
    section_end = next_section.start() if next_section else len(text)

    before = text[:section_start].rstrip("\n")
    after = text[section_end:]

    parts = [before, "\n"]
    if new_body:
        parts.extend([new_body, "\n"])
    if after:
        parts.append(after)

    new_text = "".join(parts)

    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(CONFIG_PATH) or ".", prefix=".mtproto_toml_"
    )
    try:
        os.write(fd, new_text.encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, CONFIG_PATH)
        logger.info(f"[ SYNC ] Перезаписана секция: [access.users]: {len(active_items)} секретов.")
    except Exception:
        os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# Горячая перезагрузка через SIGHUP
def _reload_mtproto() -> None:
    pid = None
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
    except (ValueError, OSError) as e:
        logger.warning(f"[ RELOAD ] Невозможно прочитать PID-файл {PID_FILE}: {e}.")
        pid = None

    if pid:
        try:
            os.kill(pid, signal.SIGHUP)
            logger.info(f"[ RELOAD ] Отправлен SIGHUP mtproto-proxy (pid {pid}).")
            return
        except ProcessLookupError:
            logger.warning(f"[ RELOAD ] PID {pid} — не запущен.")
        except PermissionError:
            logger.warning(f"[ RELOAD ] Отказано в доступе к сигналу (pid {pid}).")

    try:
        result = subprocess.run(
            ["systemctl", "reload", MTPROTO_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("[ RELOAD ] Выполнен systemctl reload.")
            return
        else:
            logger.warning(f"[ RELOAD ] systemctl reload: {result.stderr.strip()}.")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"[ RELOAD ] Исключение systemctl reload: {e}.")

    try:
        result = subprocess.run(
            ["systemctl", "restart", MTPROTO_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("[ RELOAD ] Выполнен systemctl restart.")
            return
        else:
            logger.warning(f"[ RELOAD ] systemctl restart: {result.stderr.strip()}.")
    except Exception as e:
        logger.warning(f"[ RELOAD ] Исключение systemctl restart: {e}.")

    logger.error("[ RELOAD ] Cигнал mtproto-proxy не отправлен.")
    logger.error("[ FATAL ] Требуется ручная перезагрузка.")


# API
@app.on_event("startup")
async def startup():
    _load_state()
    _sync_toml()
    mode = f"FakeTLS ({MTPROTO_DOMAIN})" if MTPROTO_DOMAIN else "classic MTProto"
    logger.info(
        f"Sidecar готов. Mode={mode} | State={STATE_PATH} | TOML={CONFIG_PATH} | "
        f"Загружено: {len(_secrets)} секретов ({sum(1 for v in _secrets.values() if v.enabled)} active)."
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "secrets_loaded": len(_secrets),
        "active": sum(1 for v in _secrets.values() if v.enabled),
        "mode": "faketls" if MTPROTO_DOMAIN else "classic",
    }


@app.post("/api/secret")
async def create_secret():
    global _counter
    _counter += 1
    secret_id = str(_counter)
    secret_key = _generate_secret()
    entry = SecretEntry(id=secret_id, secret=secret_key, enabled=True)
    _secrets[secret_id] = entry
    _save_state()
    _sync_toml()
    _reload_mtproto()
    logger.info(f"[ API ] Создан секрет {secret_id} (длина={len(secret_key)}).")
    return {"secret_id": secret_id, "secret_key": secret_key}


@app.post("/api/secret/{secret_id}/disable")
async def disable_secret(secret_id: str):
    if secret_id not in _secrets:
        raise HTTPException(status_code=404, detail="Secret not found")
    _secrets[secret_id].enabled = False
    _save_state()
    _sync_toml()
    _reload_mtproto()
    return {"status": "disabled", "id": secret_id}


@app.post("/api/secret/{secret_id}/enable")
async def enable_secret(secret_id: str):
    if secret_id not in _secrets:
        raise HTTPException(status_code=404, detail="Secret not found")
    _secrets[secret_id].enabled = True
    _save_state()
    _sync_toml()
    _reload_mtproto()
    return {"status": "enabled", "id": secret_id}


@app.delete("/api/secret/{secret_id}")
async def delete_secret(secret_id: str):
    if secret_id not in _secrets:
        raise HTTPException(status_code=404, detail="Secret not found")
    del _secrets[secret_id]
    _save_state()
    _sync_toml()
    _reload_mtproto()
    return {"status": "deleted", "id": secret_id}
