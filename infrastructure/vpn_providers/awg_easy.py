import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any, List

from infrastructure.vpn_providers.base import IVPNProvider

logger = logging.getLogger(__name__)


class AwgEasyAPI:

    def __init__(self, url: str, password: str, name: str = "unknown"):
        self.base_url = url.rstrip('/')
        self.password = password
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info(f"[ OK ] Закрыта сессия: {self.name}.")

    async def _request(self, method: str, endpoint: str, data: dict = None, retry: int = 3):
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": self.password,
            "Content-Type": "application/json"
        }
        for attempt in range(retry):
            try:
                session = await self._get_session()
                async with session.request(
                    method, url, json=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status in (200, 204):
                        ct = response.headers.get('Content-Type', '')
                        if 'application/json' in ct:
                            return await response.json()
                        return await response.text()
                    if response.status == 401 and attempt < retry - 1:
                        logger.warning(f"[ WARN ] {self.name}: повторная попытка аутентификации {attempt + 1}.")
                        await asyncio.sleep(1)
                        continue
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status} на {endpoint} | {error_text}")
            except aiohttp.ClientError as e:
                if attempt < retry - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        f"[ WARN ] {self.name}: повторное подключение {attempt + 1}/{retry} через {wait} секунд."
                    )
                    await asyncio.sleep(wait)
                    continue
                raise Exception(f"Подключение провалено после: {retry} попыток: {e}.")

    async def healthcheck(self) -> bool:
        try:
            result = await self._request("GET", "/api/wireguard/client")
            return isinstance(result, list)
        except Exception as e:
            logger.error(f"[ FAIL ] {self.name} healthcheck: {e}.")
            return False

    async def get_all_clients(self) -> List[Dict[str, Any]]:
        result = await self._request("GET", "/api/wireguard/client")
        return result if isinstance(result, list) else []

    async def create_client(self, name: str) -> Optional[str]:
        clients = await self.get_all_clients()
        for client in clients:
            if client.get("name") == name:
                return str(client.get("id"))
        try:
            created = await self._request("POST", "/api/wireguard/client", {"name": name})
            if isinstance(created, dict) and "id" in created:
                return str(created["id"])
        except Exception as e:
            logger.warning(f"[ WARN ] {self.name}: создание POST провалено: {e}.")
        await asyncio.sleep(0.5)
        clients = await self.get_all_clients()
        for client in clients:
            if client.get("name") == name:
                return str(client.get("id"))
        raise Exception(f"Клиент: {name} — не найден после попытки создания.")

    async def delete_client(self, client_id: str) -> bool:
        try:
            await self._request("DELETE", f"/api/wireguard/client/{client_id}")
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] {self.name}: удаление {client_id}: {e}.")
            return False

    async def disable_client(self, client_id: str) -> bool:
        try:
            await self._request("POST", f"/api/wireguard/client/{client_id}/disable")
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] {self.name}: отключение {client_id}: {e}.")
            return False

    async def enable_client(self, client_id: str) -> bool:
        try:
            await self._request("POST", f"/api/wireguard/client/{client_id}/enable")
            logger.info(f"[ OK ] {self.name}: клиент {client_id} активирован.")
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] {self.name}: активация {client_id}: {e}.")
            return False

    async def get_config(self, client_id: str) -> str:
        return await self._request("GET", f"/api/wireguard/client/{client_id}/configuration")


class AwgEasyProvider(IVPNProvider):

    def __init__(self, api: AwgEasyAPI):
        self._api = api

    @property
    def name(self) -> str:
        return f"awg:{self._api.name}"

    async def create_access(self, client_name: str) -> tuple[str, str]:
        awg_id = await self._api.create_client(client_name)
        config = await self._api.get_config(awg_id)
        return awg_id, config

    async def disable_access(self, provider_id: str) -> bool:
        return await self._api.disable_client(provider_id)

    async def enable_access(self, provider_id: str) -> bool:
        return await self._api.enable_client(provider_id)

    async def revoke_access(self, provider_id: str) -> bool:
        return await self._api.delete_client(provider_id)

    async def healthcheck(self) -> bool:
        return await self._api.healthcheck()
