from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.keyboards import convert_keyboard, main_menu
from taurus_mafia_bot.services.economy import EconomyError, EconomyService

router = Router(name="start")

HELP_TEXT = """
<b>Список команд <i>Taur Bot</i></b>

<blockquote expandable><code>/start</code> - регистрация и запуск меню
<code>хелп</code> / <code>/help</code> - показать это меню
<code>/info</code> - показать ID текущего чата
<code>/tw</code> - посмотреть баланс
<code>/top</code> / <code>топ</code> - топ пользователей по <b>Taurons</b>
<code>/convert</code> - конвертация <b>TC</b> в <b>T</b>
<code>Профиль</code> - открыть профиль
<code>Магазин</code> - открыть магазин
<code>Мои бонусы</code> - список купленных бонусов
<code>Мои задания</code> - список доступных заданий
<code>рулетка</code> / <code>/spin</code> - открыть рулетку за <b>5 T</b> и список призов
<code>/муу @user/ID сумма</code> - перевести <b>T</b>
<code>/буи @user/ID сумма</code> - перевести <b>TC</b>
<code>/муу сумма</code> / <code>/буи сумма</code> - перевод реплаем

<b>Админ-команды</b>
<code>/tm @user/ID сумма</code> - выдать <b>T</b>
<code>/tc @user/ID сумма</code> - выдать <b>TC</b>
<code>/tm сумма</code> / <code>/tc сумма</code> - выдача реплаем
<code>/apanel</code> - открыть админ-панель
<code>/admin @user/ID</code> - выдать или снять админку
<code>/rass</code> - создать текстовую рассылку
<code>/users</code> - список пользователей
<code>/user @user/ID</code> - карточка пользователя
<code>/reset_missions</code> - сбросить выполненные задания
<code>выдать т победителям 10</code> - начислить <b>T</b> победителям игры
<code>выдать тс участникам 5</code> - начислить <b>TC</b> всем участникам игры
<code>/cancel</code> - отменить текущее действие</blockquote>

<b>Кратко</b>
<blockquote expandable>Для регистрации используй <code>/start</code> в личке с ботом.
Переводы и админ-выдача работают по <b>ID</b>, <b>@username</b> или <b>реплаю</b>.
Команды выдачи наград победителям/участникам нужно отправлять <b>реплаем</b> на сообщение бота с завершённой игрой.</blockquote>
"""


@router.message(CommandStart())
async def start(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    existing = await economy.profile(message.from_user.id)
    await economy.ensure_user(message.from_user, is_admin=message.from_user.id in settings.admin_ids)
    greeting = "создан" if existing is None else "обновлён"
    await message.answer(f"<b>Профиль {greeting}.</b> Выбери действие:", reply_markup=main_menu())


@router.message(Command("help"))
@router.message(F.text.lower() == "хелп")
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT, disable_web_page_preview=True)


@router.message(Command("info"))
async def chat_info(message: Message) -> None:
    thread = f"\nThread ID: <code>{message.message_thread_id}</code>" if message.message_thread_id else ""
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>{thread}")


@router.message(F.text == "Профиль")
async def profile(message: Message, economy: EconomyService) -> None:
    assert message.from_user is not None
    await economy.ensure_user(message.from_user)
    row = await economy.profile(message.from_user.id)
    bonuses = await economy.db.fetch_all(
        "SELECT prize_code, prize_name, count FROM user_prizes WHERE user_id = ? AND count > 0 ORDER BY prize_name",
        (message.from_user.id,),
    )
    bonus_text = "\n".join(f"• {b['prize_name']} — {b['count']} шт." for b in bonuses) or "пусто"
    await message.answer(
        "<b>Ваш профиль:</b>\n"
        f"Пользователь: <b>{row['full_name']}</b> (<code>{row['telegram_id']}</code>)\n"
        f"Статус: {'Админ' if row['is_admin'] else 'Игрок'}\n\n"
        f"<b>Taurons:</b> <i>{row['taurons']}</i> T\n"
        f"<b>Taurcoins:</b> <i>{row['taurcoins']}</i> TC\n\n"
        f"<b>Инвентарь:</b>\n{bonus_text}"
    )


@router.message(Command("tw", prefix="!/"))
async def balance(message: Message, economy: EconomyService) -> None:
    assert message.from_user is not None
    row = await economy.profile(message.from_user.id)
    if row is None:
        await message.reply("<b>Твой профиль не найден. Попробуй команду /start.</b>")
        return
    await message.reply(f"<b>Твой баланс:</b> {row['taurons']}Т, {row['taurcoins']}TC")


def format_taurons_top(rows, total: int) -> str:
    if not rows:
        return "📊 <b>Топ богатых пользователей по Тауронам</b>\n\nПока нет пользователей с тауронами.\n\nВсего тауронов: <b>0</b>"
    lines = ["📊 <b>Топ богатых пользователей по Тауронам</b>", ""]
    for index, row in enumerate(rows, start=1):
        name = escape(row["full_name"] or row["username"] or str(row["telegram_id"]))
        lines.append(f" {index}. {name} — <b>{int(row['taurons'])}</b>")
    lines.extend(["", f"Всего тауронов: <b>{total}</b>"])
    return "\n".join(lines)


@router.message(Command("top"))
@router.message(Command("topt"))
@router.message(F.text.casefold() == "топ")
@router.message(F.text.casefold() == "топ тауронов")
async def taurons_top(message: Message, economy: EconomyService) -> None:
    rows = await economy.top_taurons(limit=10)
    total = await economy.total_taurons()
    await message.reply(format_taurons_top(rows, total))


@router.message(Command("convert"))
async def convert_menu(message: Message, economy: EconomyService) -> None:
    assert message.from_user is not None
    row = await economy.profile(message.from_user.id)
    if row is None:
        await message.reply("Профиль не найден.")
        return
    rate = await economy.get_rate()
    possible = int(row["taurcoins"]) // rate
    text = (
        f"<b>У тебя:</b>\n <i>{row['taurcoins']}</i> <b>Taurcoins (TC)</b>\n <i>{row['taurons']}</i> <b>Taurons (T)</b>\n\n"
        f"<b>Курс обмена:</b> <i>{rate}</i><b> TC = 1 T</b>\n"
        f"<b>Ты можешь конвертировать:</b> <i>{possible}</i> <b>T</b>"
    )
    await message.reply(text, reply_markup=convert_keyboard() if possible > 0 else None)


@router.callback_query(F.data == "convert")
async def convert_callback(callback: CallbackQuery, economy: EconomyService) -> None:
    try:
        rate, taurons, taurcoins = await economy.convert_one(callback.from_user.id)
    except EconomyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    markup = convert_keyboard() if taurcoins >= rate else None
    if callback.message:
        await callback.message.edit_text(
            f"<b>Успешно конвертировано!</b>\n"
            f"<b>Списано:</b> <i>{rate}</i> <b>TC</b>\n"
            f"<b>Зачислено:</b> <i>1</i> <b>T</b>\n\n"
            f"<b>Твой баланс:</b>\n<b>Taurcoins:</b> <i>{taurcoins}</i> TC\n<b>Taurons:</b> <i>{taurons}</i> T\n\n"
            f"<b>Курс:</b> <i>{rate}</i> <b>TC = 1 T</b>",
            reply_markup=markup,
        )
    await callback.answer()
