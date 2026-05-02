import sys
from .settings import settings

_mod = sys.modules[__name__]

# Повторный экспорт для обеспечения обратной совместимости
for k, v in settings.model_dump().items():
    setattr(_mod, k, v)

_mod.ADMIN_IDS = settings.ADMIN_IDS
_mod.BACKUP_CHAT_ID = (
    int(settings.BACKUP_CHAT_ID)
    if settings.BACKUP_CHAT_ID.strip().lstrip("-").isdigit()
    else (settings.ADMIN_IDS[0] if settings.ADMIN_IDS else 0)
)
_mod.VPN_SERVERS = settings.VPN_SERVERS
_mod.MTPROTO_SERVERS = settings.MTPROTO_SERVERS
_mod.COMPACT_MOBILE_LIST = [c.strip() for c in settings.COMPACT_MOBILE_CIDRS.split(",") if c.strip()]
_mod.FALLBACK_MOBILE_LIST = [c.strip() for c in (settings.FALLBACK_MOBILE_CIDRS or "31.13.64.0/18,157.240.0.0/16").split(",") if c.strip()]
