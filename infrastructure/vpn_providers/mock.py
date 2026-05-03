import asyncio
import logging
import time
from typing import Tuple

from infrastructure.vpn_providers.base import IVPNProvider

logger = logging.getLogger(__name__)

class MockProvider(IVPNProvider):

    def __init__(self, name: str, node_type: str):
        self._name = name
        self._node_type = node_type.upper()
        self._counter = int(time.time())

    @property
    def name(self) -> str:
        return f"mock:{self._node_type.lower()}:{self._name}"

    async def create_access(self, client_name: str) -> Tuple[str, str]:
        self._counter += 1
        provider_id = str(self._counter)
        
        await asyncio.sleep(0.5) 
        
        if self._node_type == "MTPROTO":
            fake_config = f"tg://proxy?server=127.0.0.1&port=443&secret=eeMOCK{self._name}{provider_id}"
            logger.info(f"[ MOCK ] Создан MTPROTO секрет: {provider_id} для {client_name}")
        else:
            fake_config = (
                f"[Interface]\n"
                f"PrivateKey = MOCK_PRIVATE_KEY_{self._name}_{provider_id}\n"
                f"Address = 10.8.0.2/24\n"
                f"DNS = 1.1.1.1\n\n"
                f"[Peer]\n"
                f"PublicKey = MOCK_PUBLIC_KEY_{self._name}\n"
                f"Endpoint = 127.0.0.1:51820\n"
                f"AllowedIPs = 0.0.0.0/0\n"
            )
            logger.info(f"[ MOCK ] Создан AWG конфиг: {provider_id} для {client_name}")
            
        return provider_id, fake_config

    async def disable_access(self, provider_id: str) -> bool:
        await asyncio.sleep(0.2)
        logger.info(f"[ MOCK ] Доступ приостановлен для ID: {provider_id} на ноде {self._name}.")
        return True

    async def enable_access(self, provider_id: str) -> bool:
        await asyncio.sleep(0.2)
        logger.info(f"[ MOCK ] Доступ восстановлен для ID: {provider_id} на ноде {self._name}.")
        return True

    async def revoke_access(self, provider_id: str) -> bool:
        await asyncio.sleep(0.3)
        logger.info(f"[ MOCK ] Клиент полностью удален по ID: {provider_id} с ноды {self._name}.")
        return True

    async def healthcheck(self) -> bool:
        return True