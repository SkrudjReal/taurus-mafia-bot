from __future__ import annotations

import re
from dataclasses import dataclass

from taurus_mafia_bot.db import Database


@dataclass(frozen=True)
class BonusType:
    id: int
    code: str
    name: str
    description: str
    price: int


class ShopService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_bonus_types(self) -> list[BonusType]:
        rows = await self.db.fetch_all(
            "SELECT id, code, name, description, price FROM shop_bonus_types ORDER BY id"
        )
        return [self._row_to_bonus(row) for row in rows]

    async def get_bonus_type(self, bonus_id: int) -> BonusType | None:
        row = await self.db.fetch_one(
            "SELECT id, code, name, description, price FROM shop_bonus_types WHERE id = ?",
            (bonus_id,),
        )
        return self._row_to_bonus(row) if row else None

    async def create_bonus_type(self, name: str, description: str, price: int) -> BonusType:
        if price < 0:
            raise ValueError("Цена не может быть отрицательной")
        code_base = re.sub(r"[^a-zA-Z0-9_]+", "_", name.lower()).strip("_") or "bonus"
        code = code_base
        suffix = 1
        while await self.db.fetch_val("SELECT 1 FROM shop_bonus_types WHERE code = ?", (code,)):
            suffix += 1
            code = f"{code_base}_{suffix}"
        await self.db.execute(
            "INSERT INTO shop_bonus_types (code, name, description, price) VALUES (?, ?, ?, ?)",
            (code, name, description, price),
        )
        new_id = int(await self.db.fetch_val("SELECT last_insert_rowid()"))
        bonus = await self.get_bonus_type(new_id)
        assert bonus is not None
        return bonus

    async def delete_bonus_type(self, bonus_id: int) -> bool:
        exists = await self.get_bonus_type(bonus_id)
        if exists is None:
            return False
        await self.db.execute("DELETE FROM shop_bonus_types WHERE id = ?", (bonus_id,))
        return True

    @staticmethod
    def _row_to_bonus(row) -> BonusType:
        return BonusType(
            id=int(row["id"]),
            code=str(row["code"]),
            name=str(row["name"]),
            description=str(row["description"]),
            price=int(row["price"]),
        )
