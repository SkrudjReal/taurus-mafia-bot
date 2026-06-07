from __future__ import annotations

from aiogram.types import User

from taurus_mafia_bot.db import Database


class EconomyError(Exception):
    pass


class EconomyService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_user(self, user: User, *, is_admin: bool = False) -> None:
        await self.db.execute(
            """
            INSERT INTO users (telegram_id, full_name, username, is_admin)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username,
                is_admin = MAX(users.is_admin, excluded.is_admin),
                updated_at = CURRENT_TIMESTAMP
            """,
            (user.id, user.full_name, user.username, int(is_admin)),
        )

    async def profile(self, user_id: int):
        return await self.db.fetch_one("SELECT * FROM users WHERE telegram_id = ?", (user_id,))

    async def find_user(self, identifier: str):
        ident = identifier.strip()
        if ident.startswith("@"):
            return await self.db.fetch_one("SELECT * FROM users WHERE lower(username) = lower(?)", (ident[1:],))
        if ident.isdigit() or (ident.startswith("-") and ident[1:].isdigit()):
            return await self.profile(int(ident))
        return None

    async def all_users(self, *, page: int | None = None, per_page: int = 100):
        if page is None:
            return await self.db.fetch_all("SELECT * FROM users ORDER BY created_at DESC")
        offset = max(page - 1, 0) * per_page
        return await self.db.fetch_all("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset))

    async def user_count(self) -> int:
        return int(await self.db.fetch_val("SELECT COUNT(*) FROM users") or 0)

    async def top_taurons(self, limit: int = 10):
        return await self.db.fetch_all(
            """
            SELECT telegram_id, full_name, username, taurons
            FROM users
            WHERE taurons > 0
            ORDER BY taurons DESC, full_name COLLATE NOCASE ASC, telegram_id ASC
            LIMIT ?
            """,
            (limit,),
        )

    async def total_taurons(self) -> int:
        return int(await self.db.fetch_val("SELECT COALESCE(SUM(taurons), 0) FROM users") or 0)

    async def add_taurons(self, user_id: int, amount: int, reason: str) -> None:
        await self.change_balance(user_id, "T", amount, reason)

    async def add_taurcoins(self, user_id: int, amount: int, reason: str) -> None:
        await self.change_balance(user_id, "TC", amount, reason)

    async def change_balance(self, user_id: int, currency: str, amount: int, reason: str) -> None:
        column = {"T": "taurons", "TC": "taurcoins"}.get(currency)
        if not column:
            raise EconomyError("Неизвестная валюта")
        row = await self.profile(user_id)
        if row is None:
            raise EconomyError("Пользователь не найден в базе. Он должен нажать /start.")
        await self.db.execute(f"UPDATE users SET {column} = {column} + ?, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ?", (amount, user_id))
        await self.db.execute(
            "INSERT INTO balance_transactions (user_id, currency, amount, reason) VALUES (?, ?, ?, ?)",
            (user_id, currency, amount, reason),
        )

    async def transfer(self, sender_id: int, receiver_id: int, currency: str, amount: int) -> None:
        if amount <= 0:
            raise EconomyError("Количество должно быть положительным.")
        if sender_id == receiver_id:
            raise EconomyError("Самому себе передавать нельзя.")
        sender = await self.profile(sender_id)
        receiver = await self.profile(receiver_id)
        if sender is None:
            raise EconomyError("Профиль отправителя не найден. Нажмите /start.")
        if receiver is None:
            raise EconomyError("Получатель не найден в системе.")
        column = "taurons" if currency == "T" else "taurcoins"
        if int(sender[column]) < amount:
            raise EconomyError(f"Недостаточно {currency}.")
        await self.change_balance(sender_id, currency, -amount, f"transfer_to:{receiver_id}")
        await self.change_balance(receiver_id, currency, amount, f"transfer_from:{sender_id}")

    async def get_rate(self) -> int:
        value = await self.db.fetch_val("SELECT value FROM parametrs WHERE name = 'convert_rate'")
        return int(value or 10)

    async def set_rate(self, rate: int) -> None:
        if rate <= 0:
            raise EconomyError("Курс должен быть положительным.")
        await self.db.execute(
            "INSERT INTO parametrs (name, value) VALUES ('convert_rate', ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (rate,),
        )

    async def convert_one(self, user_id: int) -> tuple[int, int, int]:
        row = await self.profile(user_id)
        if row is None:
            raise EconomyError("Профиль не найден.")
        rate = await self.get_rate()
        if int(row["taurcoins"]) < rate:
            raise EconomyError(f"Недостаточно Taurcoins. Нужно минимум {rate} TC.")
        await self.change_balance(user_id, "TC", -rate, "convert_to_t")
        await self.change_balance(user_id, "T", 1, "convert_from_tc")
        updated = await self.profile(user_id)
        return rate, int(updated["taurons"]), int(updated["taurcoins"])

    async def set_admin(self, user_id: int, value: bool) -> None:
        row = await self.profile(user_id)
        if row is None:
            raise EconomyError("Пользователь не найден в базе.")
        await self.db.execute("UPDATE users SET is_admin = ?, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ?", (int(value), user_id))

    async def is_admin(self, user_id: int, settings=None) -> bool:
        if settings and user_id in settings.admin_ids:
            return True
        row = await self.profile(user_id)
        return bool(row and row["is_admin"])

    async def grant_prize(self, user_id: int, prize_code: str, prize_name: str, count: int = 1) -> None:
        await self.db.execute(
            """
            INSERT INTO user_prizes (user_id, prize_code, prize_name, count, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, prize_code) DO UPDATE SET
                count = count + excluded.count,
                prize_name = excluded.prize_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, prize_code, prize_name, count),
        )

    async def use_prize(self, user_id: int, prize_code: str) -> None:
        row = await self.db.fetch_one("SELECT count FROM user_prizes WHERE user_id = ? AND prize_code = ?", (user_id, prize_code))
        if row is None or int(row["count"]) <= 0:
            raise EconomyError("Бонус не найден в инвентаре.")
        await self.db.execute("UPDATE user_prizes SET count = count - 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND prize_code = ?", (user_id, prize_code))
