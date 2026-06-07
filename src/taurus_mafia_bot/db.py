from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import aiosqlite


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys = ON")
            await self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    async def migrate(self) -> None:
        conn = await self.connect()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                taurons INTEGER NOT NULL DEFAULT 0,
                taurcoins INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_prizes (
                user_id INTEGER NOT NULL,
                prize_code TEXT NOT NULL,
                prize_name TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, prize_code),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS balance_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                currency TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS parametrs (
                name TEXT PRIMARY KEY,
                value REAL NOT NULL
            );

            INSERT OR IGNORE INTO parametrs (name, value) VALUES ('convert_rate', 10);

            CREATE TABLE IF NOT EXISTS user_missions (
                user_id INTEGER NOT NULL,
                mission_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                report_data TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, mission_id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS shop_bonus_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS roulette_spins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                spin_number INTEGER NOT NULL DEFAULT 0,
                prize_code TEXT NOT NULL DEFAULT '',
                prize_name TEXT NOT NULL DEFAULT '',
                guaranteed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_user_prizes_user ON user_prizes(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_missions_status ON user_missions(status);
            CREATE INDEX IF NOT EXISTS idx_roulette_spins_user ON roulette_spins(user_id);
            """
        )
        try:
            await conn.execute("ALTER TABLE roulette_spins ADD COLUMN spin_number INTEGER NOT NULL DEFAULT 0")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        await conn.execute("UPDATE roulette_spins SET spin_number = id WHERE spin_number = 0")
        await conn.commit()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        conn = await self.connect()
        await conn.execute(sql, tuple(params))
        await conn.commit()

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        conn = await self.connect()
        cursor = await conn.execute(sql, tuple(params))
        return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        conn = await self.connect()
        cursor = await conn.execute(sql, tuple(params))
        return await cursor.fetchall()

    async def fetch_val(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetch_one(sql, params)
        if row is None:
            return None
        return row[0]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
