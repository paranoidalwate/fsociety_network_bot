import re
import asyncio
import logging
import aiohttp
from typing import Optional

from config import (
    SPLIT_TUNNEL_URL, SPLIT_TUNNEL_UPDATE_HOURS,
    COMPACT_MOBILE_LIST, FALLBACK_MOBILE_LIST, MOBILE_MAX_ALLOWEDIPS_LEN,
)

logger = logging.getLogger(__name__)

_IPV4_CIDR = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:/(?:3[0-2]|[12]?\d))?$"
)
_IPV6_CIDR = re.compile(
    r"^(?:[0-9a-fA-F]{1,4}:){1,7}(?::|[0-9a-fA-F]{1,4})"
    r"(?:/(?:12[0-8]|1[01]\d|[1-9]?\d))?$"
)

_FALLBACK_STR = ", ".join((
    "31.13.64.0/18", "69.63.176.0/20", "157.240.0.0/16", "179.60.192.0/22",
    "104.244.32.0/20", "104.244.40.0/21",
    "142.250.0.0/15", "172.217.0.0/16", "216.239.32.0/19", "74.125.0.0/16",
    "34.96.0.0/14", "34.100.0.0/13",
    "104.16.0.0/12", "172.64.0.0/13", "162.159.0.0/16", "131.0.72.0/22",
    "140.82.112.0/20", "185.199.108.0/22",
    "20.201.28.0/22", "20.205.243.0/24",
    "13.32.0.0/15", "13.35.0.0/16", "52.84.0.0/15",
))
_FALLBACK_MOBILE_STR = ", ".join(sorted(set(FALLBACK_MOBILE_LIST)))

_cached_ips: Optional[str] = None
_cached_mobile_ips: Optional[str] = None
_last_update: float = 0.0
_fetch_lock = asyncio.Lock()


def _normalize_cidr(raw: str) -> Optional[str]:
    line = raw.split("#", 1)[0].strip()
    if not line:
        return None
    if _IPV4_CIDR.match(line):
        return line if "/" in line else f"{line}/32"
    if _IPV6_CIDR.match(line):
        return line if "/" in line else f"{line}/128"
    return None

def _parse_cidrs_sync(text: str) -> tuple[set[str], int]:
    parsed: set[str] = set()
    rejected = 0
    for raw_line in text.splitlines():
        norm = _normalize_cidr(raw_line)
        if norm:
            parsed.add(norm)
        elif raw_line.strip() and not raw_line.lstrip().startswith("#"):
            rejected += 1
    return parsed, rejected

async def fetch_blocked_ips() -> str:
    global _cached_ips, _last_update

    async with _fetch_lock:
        now = asyncio.get_running_loop().time()
        ttl = SPLIT_TUNNEL_UPDATE_HOURS * 3600

        if _cached_ips and (now - _last_update < ttl):
            return _cached_ips

        logger.info(f"[ OK ] Загрузка CIDR-feed: {SPLIT_TUNNEL_URL}.")
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(SPLIT_TUNNEL_URL) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    text = await resp.text()

            parsed, rejected = await asyncio.to_thread(_parse_cidrs_sync, text)

            if not parsed:
                raise RuntimeError("Пустой список после фильтрации.")

            _cached_ips = ", ".join(sorted(parsed))
            _last_update = now
            logger.info(f"[ OK ] CIDR-feed: принято = {len(parsed)}, отклонено = {rejected}.")
            return _cached_ips

        except Exception as e:
            logger.error(f"[ FAIL ] Провалена загрузка CIDR: {e} — fallback активирован.")
            _cached_ips = _FALLBACK_STR
            _last_update = now
            return _cached_ips

def get_compact_mobile_ips() -> str:
    global _cached_mobile_ips
    if _cached_mobile_ips is None:
        valid = []
        for cidr in COMPACT_MOBILE_LIST:
            if _normalize_cidr(cidr):
                valid.append(cidr)
            else:
                logger.warning(f"[ WARN ] Невалидный мобильный CIDR пропущен: {cidr}.")
        if not valid:
            _cached_mobile_ips = _FALLBACK_MOBILE_STR
        else:
            _cached_mobile_ips = ", ".join(sorted(set(valid)))
        count = len(_cached_mobile_ips.split(",")) if _cached_mobile_ips else 0
        logger.info(f"[ OK ] Компактный мобильный CIDR активирован: {count} сетей.")
    return _cached_mobile_ips

async def inject_split_tunneling(config_text: str, device_type: str = "pc") -> str:
    if device_type == "mobile":
        blocked = get_compact_mobile_ips()
        cidrs = [c.strip() for c in blocked.split(",") if c.strip() and ":" not in c]
        if len(cidrs) > MOBILE_MAX_ALLOWEDIPS_LEN:
            logger.warning(
                f"[ WARN ] Мобильный CIDR превышает лимит: ({len(cidrs)} > {MOBILE_MAX_ALLOWEDIPS_LEN}), возвращен fallback."
            )
            cidrs = [c.strip() for c in _FALLBACK_MOBILE_STR.split(",") if c.strip()]
        blocked = ", ".join(cidrs)
        logger.info(f"[ OK ] Инжект компактного мобильного CIDR: ({len(cidrs)} записей).")
    else:
        blocked = await fetch_blocked_ips()

    pattern = re.compile(r"^\s*AllowedIPs\s*=.*$", re.MULTILINE)
    replacement = f"AllowedIPs = {blocked}"

    new_config, n = pattern.subn(replacement, config_text)
    if n == 0:
        logger.warning("[ WARN ] Значения: \"AllowedIPs\" — не найдены. Дописываем в секцию: \"[Peer]\".")
        if "[Peer]" in config_text:
            return config_text.rstrip() + f"\n{replacement}\n"
        logger.error("[ FAIL ] Секция: \"[Peer]\" — не найдена. Конфиг не изменен.")
        return config_text
    return new_config
