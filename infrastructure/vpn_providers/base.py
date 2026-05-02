from abc import ABC, abstractmethod


class IVPNProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def create_access(self, client_name: str) -> tuple[str, str]:

    @abstractmethod
    async def disable_access(self, provider_id: str) -> bool:
        ...

    @abstractmethod
    async def enable_access(self, provider_id: str) -> bool:
        ...

    @abstractmethod
    async def revoke_access(self, provider_id: str) -> bool:
        ...

    @abstractmethod
    async def healthcheck(self) -> bool:
        ...
