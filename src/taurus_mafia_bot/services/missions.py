from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from taurus_mafia_bot.db import Database
from taurus_mafia_bot.services.economy import EconomyService

DEFAULT_MISSIONS = {
    "1": {"name": "задание 1", "description": "описание", "reward_taurons": 10, "reward_taurcoins": 0},
}


class MissionService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.path = Path(db.path).parent / "missions.json"

    def load(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return DEFAULT_MISSIONS.copy()

    def save(self, missions: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(missions, ensure_ascii=False, indent=2), encoding="utf-8")

    async def ensure_for_user(self, user_id: int) -> None:
        for mission_id in self.load():
            await self.db.execute(
                "INSERT OR IGNORE INTO user_missions (user_id, mission_id, status) VALUES (?, ?, 'pending')",
                (user_id, int(mission_id)),
            )

    async def user_missions(self, user_id: int, status: str = "pending"):
        return await self.db.fetch_all("SELECT * FROM user_missions WHERE user_id = ? AND status = ? ORDER BY mission_id", (user_id, status))

    async def active_missions(self, user_id: int):
        return await self.db.fetch_all(
            """
            SELECT *
            FROM user_missions
            WHERE user_id = ? AND status IN ('pending', 'reported')
            ORDER BY mission_id
            """,
            (user_id,),
        )

    async def report(self, user_id: int, mission_id: int, report_data: str) -> None:
        await self.db.execute(
            """
            INSERT INTO user_missions (user_id, mission_id, status, report_data, timestamp)
            VALUES (?, ?, 'reported', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, mission_id) DO UPDATE SET status = 'reported', report_data = excluded.report_data, timestamp = CURRENT_TIMESTAMP
            """,
            (user_id, mission_id, report_data),
        )

    async def complete(self, user_id: int, mission_id: int, economy: EconomyService) -> bool:
        mission = self.load().get(str(mission_id))
        if not mission:
            return False
        status = await self.db.fetch_val(
            "SELECT status FROM user_missions WHERE user_id = ? AND mission_id = ?",
            (user_id, mission_id),
        )
        if status != "reported":
            return False
        await economy.add_taurons(user_id, int(mission.get("reward_taurons", 0)), f"mission:{mission_id}")
        await economy.add_taurcoins(user_id, int(mission.get("reward_taurcoins", 0)), f"mission:{mission_id}")
        await self.db.execute("UPDATE user_missions SET status = 'completed', timestamp = CURRENT_TIMESTAMP WHERE user_id = ? AND mission_id = ?", (user_id, mission_id))
        return True

    async def reject(self, user_id: int, mission_id: int) -> None:
        await self.db.execute("UPDATE user_missions SET status = 'pending', timestamp = CURRENT_TIMESTAMP WHERE user_id = ? AND mission_id = ?", (user_id, mission_id))

    async def reset_completed(self) -> int:
        count = int(await self.db.fetch_val("SELECT COUNT(*) FROM user_missions WHERE status = 'completed'") or 0)
        await self.db.execute("UPDATE user_missions SET status = 'pending' WHERE status = 'completed'")
        return count
