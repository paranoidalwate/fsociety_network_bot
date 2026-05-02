from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Dict, List


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: List[int] = []
    BACKUP_CHAT_ID: str = ""
    ENCRYPTION_KEY: str = ""

    # Ноды AmneziaWG
    WG_API_URL_1: str = ""
    WG_PASSWORD_1: str = ""
    WG_API_NAME_1: str = "FL"
    WG_API_URL_2: str = ""
    WG_PASSWORD_2: str = ""
    WG_API_NAME_2: str = "NL"

    # Sidecar MTProto-Manager
    MTPROTO_MGR_URL_1: str = ""
    MTPROTO_HOST_1: str = ""
    MTPROTO_PORT_1: int = 443
    MTPROTO_MGR_URL_2: str = ""
    MTPROTO_HOST_2: str = ""
    MTPROTO_PORT_2: int = 443

    # Подписка и TLL
    SUBSCRIPTION_DAYS: int = 30
    PAYMENT_EXPIRE_HOURS: int = 24

    # Раздельное туннелирование
    SPLIT_TUNNEL_URL: str = "https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/ipsum.lst"
    SPLIT_TUNNEL_UPDATE_HOURS: int = 24
    COMPACT_MOBILE_CIDRS: str = ""
    MOBILE_MAX_ALLOWEDIPS_LEN: int = 3000
    FALLBACK_MOBILE_CIDRS: str = ""

    # Мониторинг и алерты
    NODE_ALERT_THRESHOLD: int = 3
    DB_BACKUP_HOUR: int = 4

    # Логи
    LOG_LEVEL: str = "INFO"

    # Рассылка
    BROADCAST_BATCH_SIZE: int = 20
    BROADCAST_DELAY: float = 1.5

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_ids(cls, v):
        if not v:
            return []
        if isinstance(v, list):
            return [
                int(x) for x in v
                if isinstance(x, (int, str)) and str(x).strip().lstrip("-").isdigit()
            ]
        return [
            int(x.strip())
            for x in str(v).replace(";", ",").split(",")
            if x.strip().lstrip("-").isdigit()
        ]

    @property
    def VPN_SERVERS(self) -> Dict[str, Dict]:
        out = {}
        for i, (url, pw, name) in enumerate([
            (self.WG_API_URL_1, self.WG_PASSWORD_1, self.WG_API_NAME_1),
            (self.WG_API_URL_2, self.WG_PASSWORD_2, self.WG_API_NAME_2),
        ], 1):
            if url and pw:
                out[f"server{i}"] = {"name": name, "url": url, "password": pw}
        return out

    @property
    def MTPROTO_SERVERS(self) -> Dict[str, Dict]:
        out = {}
        for i in (1, 2):
            url = getattr(self, f"MTPROTO_MGR_URL_{i}", "")
            host = getattr(self, f"MTPROTO_HOST_{i}", "")
            port = getattr(self, f"MTPROTO_PORT_{i}", 443)
            if url and host:
                out[f"server{i}"] = {"url": url, "host": host, "port": port}
        return out

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
