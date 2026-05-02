import aiohttp
import asyncio
import logging
from typing import Optional

from infrastructure.vpn_providers.base import IVPNProvider

logger = logging.getLogger(__name__)


class MTProtoManagerAPI:

    def __init__(self, url: str, proxy_host: str, proxy_port: int, name: str = "unknown"):
        self.base_url = url.rstrip('/')
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
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

    async def _request(self, method: str, endpoint: str, json: dict = None, retry: int = 3):
        url = f"{self.base_url}{endpoint}"
        last_exc = None
        for attempt in range(retry):
            try:
                session = await self._get_session()
                async with session.request(
                    method, url, json=json,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status in (200, 201, 204):
                        if resp.status == 204:
                            return {}
                        return await resp.json()
                    text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {text}")
            except Exception as e:
                last_exc = e
                if attempt < retry - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
        raise last_exc

    async def create_secret(self) -> dict:
        return await self._request("POST", "/api/secret")

    async def disable_secret(self, secret_id: str) -> dict:
        return await self._request("POST", f"/api/secret/{secret_id}/disable")

    async def enable_secret(self, secret_id: str) -> dict:
        return await self._request("POST", f"/api/secret/{secret_id}/enable")

    async def delete_secret(self, secret_id: str) -> dict:
        return await self._request("DELETE", f"/api/secret/{secret_id}")

    async def healthcheck(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception:
            return False


class MtprotoProvider(IVPNProvider):

    def __init__(self, api: MTProtoManagerAPI):
        self._api = api

    @property
    def name(self) -> str:
        return f"mtproto:{self._api.name}"

    async def create_access(self, client_name: str) -> tuple[str, str]:
        """Returns (secret_id, tg://proxy link). client_name is ignored by MTProto sidecar."""
        result = await self._api.create_secret()
        secret_id = result.get("secret_id") or result.get("id")
        secret_key = result.get("secret_key") or result.get("secret")
        link = (
            f"tg://proxy?server={self._api.proxy_host}"
            f"&port={self._api.proxy_port}"
            f"&secret={secret_key}"
        )
        return secret_id, link

    async def disable_access(self, provider_id: str) -> bool:
        try:
            await self._api.disable_secret(provider_id)
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] MTProto отключение {provider_id}: {e}.")
            return False

    async def enable_access(self, provider_id: str) -> bool:
        try:
            await self._api.enable_secret(provider_id)
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] MTProto отключение {provider_id}: {e}.")
            return False

    async def revoke_access(self, provider_id: str) -> bool:
        try:
            await self._api.delete_secret(provider_id)
            return True
        except Exception as e:
            logger.error(f"[ FAIL ] MTProto отключение {provider_id}: {e}.")
            return False

    async def healthcheck(self) -> bool:
        return await self._api.healthcheck()
