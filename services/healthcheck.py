import asyncio
import logging
from typing import Dict

from aiogram import Bot

from infrastructure.vpn_providers.base import IVPNProvider
from domain.provisioning import get_provisioner
from config import BACKUP_CHAT_ID, NODE_ALERT_THRESHOLD

logger = logging.getLogger(__name__)


class NodeHealthMonitor:

    def __init__(self):
        self._fail_streaks: Dict[str, int] = {}

    async def check_all(self, bot: Bot) -> Dict[str, bool]:
        provisioner = get_provisioner()
        results = {}

        all_providers = {
            **{f"awg:{k}": v for k, v in provisioner.awg.items()},
            **{f"mtproto:{k}": v for k, v in provisioner.mtproto.items()},
        }

        for name, provider in all_providers.items():
            try:
                ok = await provider.healthcheck()
                results[name] = ok
                if ok:
                    if self._fail_streaks.get(name, 0) > 0:
                        logger.info(f"[ RECOVERY ] {name}: вернулся в сеть.")
                    self._fail_streaks[name] = 0
                else:
                    self._fail_streaks[name] = self._fail_streaks.get(name, 0) + 1
            except Exception as e:
                logger.error(f"[ FAIL ] Healthcheck {name}: {e}.")
                self._fail_streaks[name] = self._fail_streaks.get(name, 0) + 1
                results[name] = False

        for name, streak in self._fail_streaks.items():
            if streak == NODE_ALERT_THRESHOLD:
                await self._send_alert(bot, name, streak)
            elif streak > 0 and streak % NODE_ALERT_THRESHOLD == 0:
                await self._send_alert(bot, name, streak)

        return results

    async def _send_alert(self, bot: Bot, node_name: str, streak: int):
        if not BACKUP_CHAT_ID:
            return
        try:
            await bot.send_message(
                BACKUP_CHAT_ID,
                f"<b>[ NODE_ALERT ]</b>\n\n"
                f"Node: <code>{node_name}</code>\n"
                f"Consecutive failures: <code>{streak}</code>\n\n"
                f"<i>Control is an illusion.</i>",
            )
            logger.warning(f"[ OK ] Алерт доставлен → {BACKUP_CHAT_ID}.")
        except Exception as e:
            logger.error(f"[ FAIL ] Провалена доставка алерта: {e}.")


_monitor = None


def get_health_monitor() -> NodeHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = NodeHealthMonitor()
    return _monitor
