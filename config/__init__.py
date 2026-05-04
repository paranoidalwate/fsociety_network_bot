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
