from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.routers.shop import notify_admins
from taurus_mafia_bot.services.economy import EconomyError, EconomyService
from taurus_mafia_bot.services.roulette import ROULETTE_SPIN_COST, RouletteResult, RouletteService, roulette_info_text

router = Router(name="roulette")


def roulette_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"Крутить рулетку — {ROULETTE_SPIN_COST} T", callback_data="roulette:spin")]]
    )


@router.message(Command("spin"))
@router.message(F.text.casefold() == "рулетка")
async def roulette_menu(message: Message, economy: EconomyService) -> None:
    assert message.from_user is not None
    await economy.ensure_user(message.from_user)
    await message.reply(roulette_info_text(), reply_markup=roulette_keyboard())


@router.callback_query(F.data == "roulette:spin")
async def spin_roulette(
    callback: CallbackQuery,
    economy: EconomyService,
    roulette: RouletteService,
    settings: Settings,
) -> None:
    assert callback.from_user is not None
    await economy.ensure_user(callback.from_user)
    try:
        result = await roulette.spin(callback.from_user.id, economy)
    except EconomyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(format_spin_result(result))
    await notify_admins(
        callback,
        settings,
        format_admin_spin_result(format_user_mention(callback.from_user.id, callback.from_user.full_name), result),
        log_chat_id=settings.roulette_log_chat_id,
        log_thread_id=settings.roulette_log_thread_id,
    )


def format_spin_result(result: RouletteResult) -> str:
    prefix = "<b>Гарантированный приз!</b>\n" if result.guaranteed else ""
    prize_name = RouletteService.prize_display_name(result.prize, result.spin_number)
    return (
        f"{prefix}Вы выиграли: <b>{prize_name}</b>\n"
        f"<i>{result.prize.description}</i>"
    )


def format_user_mention(user_id: int, full_name: str) -> str:
    name = escape(full_name or str(user_id))
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def format_admin_spin_result(user_mention: str, result: RouletteResult) -> str:
    prize_name = RouletteService.prize_display_name(result.prize, result.spin_number)
    guaranteed = "\nГарантия: да" if result.guaranteed else ""
    return (
        f"<b>Рулетка</b>\n"
        f"Пользователь: {user_mention}\n"
        f"Прокрут: <code>{result.spin_number}</code>\n"
        f"Приз: <b>{prize_name}</b>{guaranteed}"
    )
