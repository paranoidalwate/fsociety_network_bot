import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from infrastructure.vpn_providers.base import IVPNProvider
from infrastructure.database.db import add_device, get_user_devices
from core.base62 import encode_base62
from core.utils import inject_split_tunneling
from core.crypto import encrypt_config

logger = logging.getLogger(__name__)


@dataclass
class ProvisionResult:
    success: Dict[str, bool] = field(default_factory=dict)


def _task_key(server_key: str, device_type: str) -> str:
    return f"{server_key}:{device_type}"


def generate_client_name(payment_id: int, server_key: str, device_type: str) -> str:
    server_codes = {"server1": "FL", "server2": "NL"}
    srv = server_codes.get(server_key, server_key[:2].upper())
    b62 = encode_base62(payment_id)
    dt_map = {"pc": "PC", "mobile": "PH", "mtproto": "MT"}
    dev = dt_map.get(device_type, device_type[:2].upper())
    return f"{srv}{b62}{dev}"


class BundleProvisioner:

    def __init__(
        self,
        awg_providers: Dict[str, IVPNProvider],
        mtproto_providers: Dict[str, IVPNProvider],
    ):
        self.awg = awg_providers
        self.mtproto = mtproto_providers

    def get_provider(self, server_key: str, device_type: str) -> Optional[IVPNProvider]:
        if device_type in ("pc", "mobile"):
            return self.awg.get(server_key)
        if device_type == "mtproto":
            return self.mtproto.get(server_key)
        return None

    def _build_tasks(self, payment_id: int) -> List[Tuple[str, str, IVPNProvider, str, str]]:
        tasks = []
        for sk in sorted(self.awg.keys()):
            tasks.append((
                sk, "pc", self.awg[sk],
                generate_client_name(payment_id, sk, "pc"), "PC"
            ))
            tasks.append((
                sk, "mobile", self.awg[sk],
                generate_client_name(payment_id, sk, "mobile"), "Phone"
            ))
        for sk in sorted(self.mtproto.keys()):
            tasks.append((
                sk, "mtproto", self.mtproto[sk],
                generate_client_name(payment_id, sk, "mtproto"), "MTProto"
            ))
        return tasks

    async def _execute_single(
        self,
        tg_id: int,
        server_key: str,
        device_type: str,
        provider: IVPNProvider,
        client_name: str,
    ) -> bool:
        key = _task_key(server_key, device_type)
        provider_id = None
        try:
            provider_id, raw_config = await provider.create_access(client_name)

            if device_type == "mtproto":
                config_text = raw_config
            else:
                config_text = await inject_split_tunneling(raw_config, device_type=device_type)

            enc_config = encrypt_config(config_text)

            await add_device(
                user_id=tg_id,
                awg_id=provider_id,
                config_text=enc_config,
                server_name=server_key,
                device_type=device_type,
                client_name=client_name,
            )
            logger.info(f"[ OK ] Выдан: {key}, для UID = {tg_id}, (PID = {provider_id}).")
            return True
        except Exception as e:
            if provider_id is not None:
                try:
                    await provider.revoke_access(provider_id)
                except Exception as rev:
                    logger.error(f"[ FAIL ] Откат: {key}, {provider_id}: {rev}.")
            logger.error(f"[ FAIL ] Проксирование: {key}, для UID = {tg_id}: {e}.")
            return False

    async def provision(self, tg_id: int, payment_id: int, max_retries: int = 3) -> ProvisionResult:
        tasks = self._build_tasks(payment_id)
        result = ProvisionResult()

        async def _run_with_retry(t):
            sk, dt, prov, cname, _ = t
            key = _task_key(sk, dt)
            for attempt in range(max_retries):
                ok = await self._execute_single(tg_id, sk, dt, prov, cname)
                if ok:
                    return key, True
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"[ RETRY ] {key} попытка {attempt+2}/{max_retries} через {wait} секунд.")
                    await asyncio.sleep(wait)
            return key, False

        outcomes = await asyncio.gather(*[_run_with_retry(t) for t in tasks], return_exceptions=True)

        for i, outcome in enumerate(outcomes):
            sk, dt, _, _, _ = tasks[i]
            key = _task_key(sk, dt)
            if isinstance(outcome, Exception):
                logger.exception(f"[ FAIL ] {key} вылетел: {outcome}.")
                result.success[key] = False
            else:
                _, ok = outcome
                result.success[key] = ok

        return result

    def is_fully_successful(self, result: ProvisionResult) -> bool:
        return all(result.success.values())

    async def retry_partial(self, tg_id: int, payment_id: int) -> ProvisionResult:
        existing = {
            (d["server_name"], d["device_type"])
            for d in await get_user_devices(tg_id)
        }
        tasks = self._build_tasks(payment_id)
        missing = [t for t in tasks if (t[0], t[1]) not in existing]

        result = ProvisionResult()
        for t in tasks:
            k = _task_key(t[0], t[1])
            result.success[k] = (t[0], t[1]) in existing

        if not missing:
            return result

        outcomes = await asyncio.gather(*[
            self._execute_single(tg_id, t[0], t[1], t[2], t[3])
            for t in missing
        ], return_exceptions=True)

        for i, outcome in enumerate(outcomes):
            sk, dt, _, _, _ = missing[i]
            key = _task_key(sk, dt)
            if isinstance(outcome, Exception):
                result.success[key] = False
            else:
                result.success[key] = bool(outcome)
        return result


# Синглтон аксессоры (инициализируются в run.py)
_provisioner = None


def init_provisioner(awg_providers, mtproto_providers):
    global _provisioner
    _provisioner = BundleProvisioner(awg_providers, mtproto_providers)
    logger.info(
        f"[ OK ] Активирован BundleProvisioner: AWG = {list(awg_providers)}, MTProto = {list(mtproto_providers)}."
    )


def get_provisioner() -> BundleProvisioner:
    if _provisioner is None:
        raise RuntimeError("[ FATAL ] BundleProvisioner не инициализирован.")
    return _provisioner
