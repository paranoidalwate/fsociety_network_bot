import json
import logging
from typing import Optional, Any, Dict

from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.state import State as FsmState

from infrastructure.database.db import _execute, _fetchone

logger = logging.getLogger(__name__)

def _make_key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.business_connection_id or ''}"

class SQLiteStorage(BaseStorage):
    def __init__(self):
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        await _execute("""
            CREATE TABLE IF NOT EXISTS fsm_states (
                key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await _execute("CREATE INDEX IF NOT EXISTS idx_fsm_updated ON fsm_states(updated_at)")
        self._initialized = True
        logger.info("[ OK ] Инициализировано FSM-хранилище (SQLite).")

    async def set_state(self, key: StorageKey, state: Optional[str] = None) -> None:
        await self.init()
        if isinstance(state, FsmState):
            state = state.state
        skey = _make_key(key)
        if state is None:
            await self.remove_state(key)
            return
        await _execute(
            "INSERT INTO fsm_states (key, state, data) VALUES (?, ?, '{}') "
            "ON CONFLICT(key) DO UPDATE SET state=excluded.state, updated_at=CURRENT_TIMESTAMP",
            (skey, state),
        )

    async def get_state(self, key: StorageKey) -> Optional[str]:
        await self.init()
        row = await _fetchone("SELECT state FROM fsm_states WHERE key = ?", (_make_key(key),))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await self.init()
        skey = _make_key(key)
        await _execute(
            "INSERT INTO fsm_states (key, state, data) VALUES (?, '', ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP",
            (skey, json.dumps(data)),
        )

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        await self.init()
        row = await _fetchone("SELECT data FROM fsm_states WHERE key = ?", (_make_key(key),))
        if not row or not row["data"]:
            return {}
        try:
            return json.loads(row["data"])
        except json.JSONDecodeError:
            return {}

    async def update_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await self.init()
        current = await self.get_data(key)
        current.update(data)
        await self.set_data(key, current)

    async def remove_state(self, key: StorageKey) -> None:
        await self.init()
        await _execute("DELETE FROM fsm_states WHERE key = ?", (_make_key(key),))

    async def remove_data(self, key: StorageKey) -> None:
        await self.init()
        await _execute("UPDATE fsm_states SET data = '{}' WHERE key = ?", (_make_key(key),))

    async def close(self) -> None:
        pass
