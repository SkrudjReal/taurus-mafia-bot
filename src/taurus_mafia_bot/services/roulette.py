from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from taurus_mafia_bot.db import Database
from taurus_mafia_bot.services.economy import EconomyError, EconomyService


class RandomSource(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class RoulettePrize:
    code: str
    name: str
    description: str
    chance_percent: float
    kind: str = "prize"
    amount: int = 1


@dataclass(frozen=True)
class RouletteResult:
    spin_number: int
    prize: RoulettePrize
    guaranteed: bool = False


COMMON_CHANCE = 9.7
ROULETTE_SPIN_COST = 5
ROULETTE_PRIZES: tuple[RoulettePrize, ...] = (
    RoulettePrize("anti_target_1", "АТ-1", "Активировать «АнтиТаргет на 1 день» для пользователя", COMMON_CHANCE),
    RoulettePrize("anti_target_2", "АТ-2", "Активировать «АнтиТаргет на 3 дня» для пользователя", COMMON_CHANCE),
    RoulettePrize("remove_warn", "Снять варн", "Удалить одно предупреждение у пользователя", COMMON_CHANCE),
    RoulettePrize("taurons_cashback", "Тауроны", "Получаете 3 таурона как кэшбэк", COMMON_CHANCE, kind="taurons", amount=3),
    RoulettePrize("pref_2", "Преф-2", "Активировать «Преф на 7 дней» для пользователя", COMMON_CHANCE),
    RoulettePrize("mode_purchase", "Покупка режима", "Предоставить возможность «Покупки режима»", COMMON_CHANCE),
    RoulettePrize("marriage", "Брак", "Дать возможность «Заключить брак»", COMMON_CHANCE),
    RoulettePrize("divorce", "Развод", "Дать возможность «Расторгнуть брак»", COMMON_CHANCE),
    RoulettePrize("iris_vip", "VIP в Ирисе", "Начислить VIP-статус в боте «Ирис»", COMMON_CHANCE),
    RoulettePrize("zazyvala_vip", "Зазывала VIP", "Начислить VIP-статус в боте «Зазывала»", COMMON_CHANCE),
    RoulettePrize("telegram_premium", "Telegram Premium", "Начислить «Telegram Premium на 3 месяца» пользователю", 1.0),
    RoulettePrize("tg_nft", "ТГ NFT", "Выдать уникальный «ТГ NFT» пользователю", 2.0),
)

PRIZES_BY_CODE = {prize.code: prize for prize in ROULETTE_PRIZES}
NFT_PRIZE = PRIZES_BY_CODE["tg_nft"]


class RouletteService:
    def __init__(self, db: Database, rng: RandomSource | None = None) -> None:
        self.db = db
        self.rng = rng or random.SystemRandom()

    async def spin(self, user_id: int, economy: EconomyService) -> RouletteResult:
        profile = await economy.profile(user_id)
        if profile is None:
            raise EconomyError("Профиль не найден. Нажмите /start.")
        if int(profile["taurons"]) < ROULETTE_SPIN_COST:
            raise EconomyError(f"Недостаточно Taurons. Нужно: {ROULETTE_SPIN_COST} T.")

        await economy.add_taurons(user_id, -ROULETTE_SPIN_COST, "roulette_spin_cost")
        spin_row_id, spin_number = await self._create_spin_row(user_id)
        guaranteed = spin_number % 50 == 0
        prize = NFT_PRIZE if guaranteed else self.pick_prize()
        prize_code = f"tg_nft_{spin_number}" if prize.code == "tg_nft" else prize.code

        if prize.kind == "taurons":
            await economy.add_taurons(user_id, prize.amount, f"roulette_spin:{spin_number}")
        else:
            await economy.grant_prize(user_id, prize_code, self.prize_display_name(prize, spin_number), 1)

        await self.db.execute(
            """
            UPDATE roulette_spins
            SET prize_code = ?, prize_name = ?, guaranteed = ?
            WHERE id = ?
            """,
            (prize_code, self.prize_display_name(prize, spin_number), int(guaranteed), spin_row_id),
        )
        return RouletteResult(spin_number=spin_number, prize=prize, guaranteed=guaranteed)

    def pick_prize(self) -> RoulettePrize:
        roll = self.rng.random() * 100
        cursor = 0.0
        for prize in ROULETTE_PRIZES:
            cursor += prize.chance_percent
            if roll < cursor:
                return prize
        return ROULETTE_PRIZES[-1]

    @staticmethod
    def prize_display_name(prize: RoulettePrize, spin_number: int) -> str:
        if prize.code == "tg_nft":
            return f"ТГ NFT #{spin_number}"
        return prize.name

    async def total_spins(self) -> int:
        return int(await self.db.fetch_val("SELECT COUNT(*) FROM roulette_spins") or 0)

    async def player_spins_count(self, user_id: int) -> int:
        return int(await self.db.fetch_val("SELECT COUNT(*) FROM roulette_spins WHERE user_id = ?", (user_id,)) or 0)

    async def reset_player_spins(self, user_id: int) -> int:
        deleted = await self.player_spins_count(user_id)
        await self.db.execute("DELETE FROM roulette_spins WHERE user_id = ?", (user_id,))
        return deleted

    async def _create_spin_row(self, user_id: int) -> tuple[int, int]:
        conn = await self.db.connect()
        spin_number = int(await self.db.fetch_val("SELECT COUNT(*) FROM roulette_spins WHERE user_id = ?", (user_id,)) or 0) + 1
        await conn.execute("INSERT INTO roulette_spins (user_id, spin_number) VALUES (?, ?)", (user_id, spin_number))
        cursor = await conn.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        await conn.commit()
        return int(row[0]), spin_number


def roulette_info_text() -> str:
    rows = []
    for prize in ROULETTE_PRIZES:
        chance = f"{prize.chance_percent:g}%"
        rows.append(f"<b>{prize.name}</b> — {chance}\n{prize.description}")
    return (
        "<b>Рулетка Taurus Mafia</b>\n"
        f"Стоимость прокрута: <b>{ROULETTE_SPIN_COST} T</b>.\n"
        "Здесь можно выиграть бонусы и специальные призы.\n\n"
        "<blockquote expandable>"
        + "\n\n".join(rows)
        + "\n\n<b>Гарантия:</b> каждый 50-й прокрут игрока даёт <b>ТГ NFT</b>. "
        "Каждый 100-й прокрут игрока тоже попадает в эту гарантию."
        "</blockquote>"
    )
