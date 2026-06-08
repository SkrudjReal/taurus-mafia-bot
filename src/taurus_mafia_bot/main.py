from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram_dialog import setup_dialogs

from taurus_mafia_bot.config import get_settings
from taurus_mafia_bot.db import Database
from taurus_mafia_bot.routers import admin, missions, roulette, shop, start
from taurus_mafia_bot.services.economy import EconomyService
from taurus_mafia_bot.services.missions import MissionService
from taurus_mafia_bot.services.roulette import RouletteService
from taurus_mafia_bot.services.shop import ShopService


async def create_dispatcher(settings=None) -> Dispatcher:
    settings = settings or get_settings()
    db = Database(settings.database_path)
    await db.migrate()

    shop_service = ShopService(db)
    economy_service = EconomyService(db)
    mission_service = MissionService(db)
    roulette_service = RouletteService(db)

    dp = Dispatcher(
        settings=settings,
        db=db,
        shop=shop_service,
        economy=economy_service,
        missions=mission_service,
        roulette=roulette_service,
    )
    dp.include_router(start.router)
    dp.include_router(start.top_dialog)
    dp.include_router(shop.router)
    dp.include_router(roulette.router)
    dp.include_router(admin.router)
    dp.include_router(missions.router)
    setup_dialogs(dp)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if settings.token_is_placeholder:
        raise RuntimeError("Заполните BOT_TOKEN в .env перед запуском бота")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = await create_dispatcher(settings)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await dp["db"].close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
