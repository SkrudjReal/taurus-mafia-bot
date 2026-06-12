from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.keyboards import admin_panel_keyboard, back_keyboard
from taurus_mafia_bot.services.economy import EconomyError, EconomyService
from taurus_mafia_bot.services.roulette import RouletteService

router = Router(name="admin")


class AdminState(StatesGroup):
    waiting_rate = State()
    waiting_broadcast = State()


async def is_admin_user(user_id: int, economy: EconomyService, settings: Settings) -> bool:
    return await economy.is_admin(user_id, settings)


def currency_name(currency: str) -> str:
    return "Taurons" if currency == "T" else "Taurcoins"


def currency_amount(amount: int, currency: str) -> str:
    return f"{amount}Т" if currency == "T" else f"{amount}TC"


def currency_short_ru(currency: str) -> str:
    return "Т" if currency == "T" else "ТС"


def user_identity(user_id: int, full_name: str | None, username: str | None) -> str:
    username_part = f" (@{escape(username)})" if username else ""
    return f"<code>{user_id}</code> ({escape(full_name or str(user_id))}{username_part})"


def user_identity_from_row(row) -> str:
    return user_identity(int(row["telegram_id"]), row["full_name"], row["username"])


def user_identity_from_aiogram(user: User) -> str:
    return user_identity(user.id, user.full_name, user.username)


def user_openmessage_link(user_id: int, label: str | None) -> str:
    return f'<a href="tg://openmessage?user_id={user_id}">{escape(label or str(user_id))}</a>'


def user_openmessage_link_from_row(row) -> str:
    label = f"@{row['username']}" if row["username"] else row["full_name"]
    return user_openmessage_link(int(row["telegram_id"]), label)


def user_openmessage_link_from_aiogram(user: User) -> str:
    label = f"@{user.username}" if user.username else user.full_name
    return user_openmessage_link(user.id, label)


async def send_topic_log(bot, chat_id: int | None, thread_id: int | None, text: str) -> None:
    if not chat_id:
        return
    kwargs = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    try:
        await bot.send_message(**kwargs)
    except Exception:
        pass


async def send_admin_action_log(message: Message, settings: Settings, text: str) -> None:
    await send_topic_log(message.bot, settings.admin_action_log_chat_id, settings.admin_action_log_thread_id, text)


async def send_player_action_log(message: Message, settings: Settings, text: str) -> None:
    await send_topic_log(message.bot, settings.player_action_log_chat_id, settings.player_action_log_thread_id, text)


def format_admin_action_log(admin: User, target_row, action: str, details: str) -> str:
    return (
        "[LOG]\n"
        f"Админ: {user_identity_from_aiogram(admin)}\n"
        f"Пользователь: {user_identity_from_row(target_row)}\n"
        f"Действие: {escape(action)}\n"
        f"Детали: {escape(details)}"
    )


def format_player_transfer_log(sender_row, receiver_row, currency: str, amount: int) -> str:
    return (
        "[LOG]\n"
        f"Пользователь 1: {user_identity_from_row(sender_row)}\n"
        f"Пользователь 2: {user_identity_from_row(receiver_row)}\n"
        f"Действие: Передача {currency_name(currency)}\n"
        f"Детали: {currency_amount(amount, currency)}"
    )


def format_game_reward_log(admin: User, recipient_rows: list, currency: str, amount: int, scope: str) -> str:
    recipients = "\n".join(user_openmessage_link_from_row(row) for row in recipient_rows) or "нет"
    return (
        f"{user_openmessage_link_from_aiogram(admin)} выдал(а) по {amount} {currency_short_ru(currency)} 🌟 {scope} "
        f"({len(recipient_rows)} чел.)\n\n"
        f"Получатели:\n{recipients}"
    )


def classify_broadcast_error(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, TelegramRetryAfter):
        return "rate_limit"
    if isinstance(exc, TelegramForbiddenError):
        if "bot was blocked" in message or "user is deactivated" in message:
            return "blocked_or_deactivated"
        return "forbidden"
    if isinstance(exc, TelegramBadRequest):
        if "chat not found" in message:
            return "chat_not_found"
        if "user not found" in message:
            return "user_not_found"
        if "can't parse entities" in message or "unsupported start tag" in message:
            return "bad_html"
        return "bad_request"
    if isinstance(exc, TelegramAPIError):
        return "telegram_api"
    return type(exc).__name__


def format_broadcast_summary(delivered: int, failed: int, skipped: int, reasons: Counter[str], examples: dict[str, list[int]]) -> str:
    reasons = Counter(reasons)
    lines = [
        "<b>Рассылка завершена.</b>",
        f"Доставлено: <code>{delivered}</code>",
        f"Ошибок: <code>{failed}</code>",
    ]
    if skipped:
        lines.append(f"Пропущено битых ID: <code>{skipped}</code>")
    if reasons:
        lines.append("\n<b>Причины ошибок:</b>")
        for reason, count in reasons.most_common():
            sample = ", ".join(str(user_id) for user_id in examples.get(reason, [])[:5])
            sample_text = f" | примеры: <code>{escape(sample)}</code>" if sample else ""
            lines.append(f"• <code>{escape(reason)}</code>: <code>{count}</code>{sample_text}")
    return "\n".join(lines)


def utf16_offset_to_index(text: str, offset: int) -> int:
    if offset <= 0:
        return 0
    utf16_pos = 0
    for index, char in enumerate(text):
        if utf16_pos >= offset:
            return index
        utf16_pos += len(char.encode("utf-16-le")) // 2
    return len(text)


def game_scope_range(text: str, scope: str) -> tuple[int, int]:
    lowered = text.lower()
    winners_match = re.search(r"победител[ьи]\s*:", lowered)
    if scope == "победителям":
        if not winners_match:
            return 0, len(text)
        start = winners_match.end()
        other_match = re.search(r"другие\s+игроки\s*:", lowered[start:])
        end = start + other_match.start() if other_match else len(text)
        return start, end
    if winners_match:
        return winners_match.end(), len(text)
    return 0, len(text)


def user_id_from_entity(entity: Any) -> int | None:
    user = getattr(entity, "user", None)
    if user is not None and getattr(user, "id", None):
        return int(user.id)

    url = getattr(entity, "url", None)
    if not url:
        return None
    parsed = urlparse(str(url))
    query = parse_qs(parsed.query)
    for key in ("id", "user_id"):
        values = query.get(key)
        if values and str(values[0]).isdigit():
            return int(values[0])
    return None


def extract_game_user_ids(message: Message, scope: str) -> list[int]:
    source = message.text or message.caption or ""
    start, end = game_scope_range(source, scope)
    ids: set[int] = set()

    for match in re.finditer(r"(?<!\d)(\d{5,15})(?!\d)", source):
        if start <= match.start() < end:
            ids.add(int(match.group(1)))

    entities = list(message.entities or message.caption_entities or [])
    for entity in entities:
        entity_start = utf16_offset_to_index(source, int(getattr(entity, "offset", 0) or 0))
        if start <= entity_start < end:
            user_id = user_id_from_entity(entity)
            if user_id is not None:
                ids.add(user_id)

    return sorted(ids)


async def parse_target_and_amount(message: Message, economy: EconomyService) -> tuple[int, int]:
    parts = (message.text or "").split()
    if message.reply_to_message and message.reply_to_message.from_user:
        if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
            raise EconomyError("Формат реплаем: /tm сумма или /tc сумма")
        return message.reply_to_message.from_user.id, int(parts[1])
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        raise EconomyError("Формат: /tm @user/ID сумма или /tc @user/ID сумма")
    row = await economy.find_user(parts[1])
    if row is None:
        raise EconomyError("Пользователь не найден в базе.")
    return int(row["telegram_id"]), int(parts[2])


async def grant_currency(message: Message, economy: EconomyService, settings: Settings, currency: str) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("<b>У тебя нет доступа к этой команде.</b>")
        return
    await economy.ensure_user(message.from_user, is_admin=message.from_user.id in settings.admin_ids)
    try:
        target_id, amount = await parse_target_and_amount(message, economy)
        if currency == "T":
            await economy.add_taurons(target_id, amount, f"admin:{message.from_user.id}")
        else:
            await economy.add_taurcoins(target_id, amount, f"admin:{message.from_user.id}")
    except EconomyError as exc:
        await message.reply(f"<b>Ошибка:</b> {exc}")
        return
    target_row = await economy.profile(target_id)
    if target_row is not None:
        await send_admin_action_log(
            message,
            settings,
            format_admin_action_log(
                message.from_user,
                target_row,
                f"Выдача {currency_name(currency)}",
                currency_amount(amount, currency),
            ),
        )
    await message.reply(f"<b>Успешно выдано {amount}{currency} пользователю <code>{target_id}</code>.</b>")


@router.message(Command("tm"))
async def give_taurons_admin(message: Message, economy: EconomyService, settings: Settings) -> None:
    await grant_currency(message, economy, settings, "T")


@router.message(Command("tc"))
async def give_taurcoins_admin(message: Message, economy: EconomyService, settings: Settings) -> None:
    await grant_currency(message, economy, settings, "TC")


@router.message(Command("admin", "adm"))
async def manage_admin(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if message.from_user.id != settings.owner_id:
        await message.reply("<b>Только владелец может менять админку.</b>")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.reply("Формат: /admin @user/ID")
        return
    row = await economy.find_user(parts[1])
    if row is None:
        await message.reply("Пользователь не найден в базе.")
        return
    new_value = not bool(row["is_admin"])
    await economy.set_admin(row["telegram_id"], new_value)
    updated_row = await economy.profile(row["telegram_id"])
    if updated_row is not None:
        await send_admin_action_log(
            message,
            settings,
            format_admin_action_log(
                message.from_user,
                updated_row,
                "Выдача админки" if new_value else "Снятие админки",
                "Админ: Да" if new_value else "Админ: Нет",
            ),
        )
    await message.reply(f"Админка для <code>{row['telegram_id']}</code>: {'выдана' if new_value else 'снята'}.")


async def parse_transfer_target_and_amount(message: Message, economy: EconomyService) -> tuple[int, int]:
    parts = (message.text or "").split()
    if message.reply_to_message and message.reply_to_message.from_user:
        if len(parts) != 2 or not parts[1].isdigit():
            raise EconomyError("Формат реплаем: /муу сумма или /буи сумма")
        return message.reply_to_message.from_user.id, int(parts[1])
    if len(parts) != 3 or not parts[2].isdigit():
        raise EconomyError("Формат: /муу @user/ID сумма или /буи @user/ID сумма")
    row = await economy.find_user(parts[1])
    if row is None:
        raise EconomyError("Получатель не найден")
    return int(row["telegram_id"]), int(parts[2])


async def transfer_currency(message: Message, economy: EconomyService, settings: Settings, currency: str) -> None:
    assert message.from_user is not None
    try:
        receiver_id, amount = await parse_transfer_target_and_amount(message, economy)
        await economy.transfer(message.from_user.id, receiver_id, currency, amount)
    except EconomyError as exc:
        await message.reply(f"<b>Ошибка:</b> {exc}")
        return
    sender_row = await economy.profile(message.from_user.id)
    receiver_row = await economy.profile(receiver_id)
    if sender_row is not None and receiver_row is not None:
        transfer_log = format_player_transfer_log(sender_row, receiver_row, currency, amount)
        await send_player_action_log(message, settings, transfer_log)
    await message.reply(f"<b>Успешная передача:</b> {amount}{currency} → <code>{receiver_id}</code>")


@router.message(Command("муу"))
async def transfer_taurons(message: Message, economy: EconomyService, settings: Settings) -> None:
    await transfer_currency(message, economy, settings, "T")


@router.message(Command("буи"))
async def transfer_taurcoins(message: Message, economy: EconomyService, settings: Settings) -> None:
    await transfer_currency(message, economy, settings, "TC")


@router.message(F.text.regexp(r"^[-–—]прокрут(?:\s|$)"))
async def reset_roulette_spins(message: Message, roulette: RouletteService, settings: Settings) -> None:
    assert message.from_user is not None
    if message.from_user.id != settings.owner_id:
        await message.reply("<b>Только владелец может обнулять прокрутки рулетки.</b>")
        return
    if (message.text or "").split()[1:] or message.reply_to_message:
        await message.reply("<b>Формат:</b> <code>-прокрут</code>")
        return
    deleted = await roulette.reset_all_spins()
    await message.reply(f"<b>Общие прокрутки рулетки обнулены.</b>\nУдалено записей: <code>{deleted}</code>")


@router.message(Command("apanel"))
async def admin_panel(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("<b>У вас нет доступа к админ панели.</b>")
        return
    rate = await economy.get_rate()
    await message.reply(
        f"<b>Админ Панель</b>\n\nID: <code>{message.from_user.id}</code>\nКурс обмена: 1Т = {rate}TC\n\nВыберите действие:",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data == "admin_close")
async def admin_close(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.delete()


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, economy: EconomyService) -> None:
    await callback.answer()
    if callback.message:
        rate = await economy.get_rate()
        await callback.message.edit_text(
            f"<b>Админ Панель</b>\n\nID: <code>{callback.from_user.id}</code>\nКурс обмена: 1Т = {rate}TC\n\nВыберите действие:",
            reply_markup=admin_panel_keyboard(),
        )


@router.callback_query(F.data == "admin_currency")
async def admin_currency(callback: CallbackQuery, economy: EconomyService) -> None:
    await callback.answer()
    rate = await economy.get_rate()
    if callback.message:
        await callback.message.edit_text(
            f"<b>Управление валютой</b>\n\nТекущий курс: <i>1Т = {rate}TC</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Установить курс обмена", callback_data="admin_set_rate")],
                [InlineKeyboardButton(text="Назад", callback_data="admin_back")],
            ]),
        )


@router.callback_query(F.data == "admin_set_rate")
async def admin_set_rate(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminState.waiting_rate)
    if callback.message:
        await callback.message.answer("Введите новый курс: сколько TC нужно за 1 T. Например: <code>10</code>")


@router.message(AdminState.waiting_rate)
async def process_rate(message: Message, state: FSMContext, economy: EconomyService) -> None:
    try:
        rate = int((message.text or "").strip())
        await economy.set_rate(rate)
    except (ValueError, EconomyError) as exc:
        await message.answer(f"Ошибка: {exc}")
        return
    await state.clear()
    await message.answer(f"<b>Курс обновлён:</b> 1T = {rate}TC")


@router.callback_query(F.data.startswith("admin_users:"))
async def users_page_callback(callback: CallbackQuery, economy: EconomyService) -> None:
    page = int((callback.data or "admin_users:1").split(":")[1])
    await callback.answer()
    await send_users_page(callback.message, economy, page, edit=True)


@router.message(Command("users"))
async def users_command(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("Нет доступа.")
        return
    await send_users_page(message, economy, 1, edit=False)


async def send_users_page(message: Message | None, economy: EconomyService, page: int, *, edit: bool) -> None:
    if message is None:
        return
    per_page = 20
    users = await economy.all_users(page=page, per_page=per_page)
    total = await economy.user_count()
    if not users:
        text = "Пользователей нет."
    else:
        lines = [f"<b>Пользователи</b> — страница {page}, всего {total}\n"]
        for u in users:
            username = f"@{u['username']}" if u["username"] else "без username"
            lines.append(f"• <code>{u['telegram_id']}</code> — {u['full_name']} ({username}), {u['taurons']}T/{u['taurcoins']}TC")
        text = "\n".join(lines)
    buttons = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"admin_users:{page-1}"))
    if page * per_page < total:
        nav.append(InlineKeyboardButton(text="Вперёд", callback_data=f"admin_users:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="В админ-панель", callback_data="admin_back")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(Command("user"))
async def user_info(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("Нет доступа.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.reply("Формат: /user @user/ID")
        return
    row = await economy.find_user(parts[1])
    if row is None:
        await message.reply("Пользователь не найден.")
        return
    await message.reply(
        f"<b>Пользователь</b>\nID: <code>{row['telegram_id']}</code>\nИмя: {row['full_name']}\n"
        f"Username: @{row['username'] if row['username'] else '-'}\nБаланс: {row['taurons']}T / {row['taurcoins']}TC\nАдмин: {bool(row['is_admin'])}"
    )


@router.message(Command("rass"))
async def rass_start(message: Message, state: FSMContext, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("Нет доступа.")
        return
    await state.set_state(AdminState.waiting_broadcast)
    await message.reply("Отправь текст рассылки. Для отмены: /cancel")


@router.message(AdminState.waiting_broadcast)
async def rass_send(message: Message, state: FSMContext, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    text = message.html_text or message.text or ""
    if len(text.strip()) < 3:
        await message.reply("Текст слишком короткий.")
        return
    delivered = failed = skipped = 0
    reasons: Counter[str] = Counter()
    examples: dict[str, list[int]] = defaultdict(list)
    for row in await economy.all_users():
        user_id = int(row["telegram_id"])
        if user_id <= 0:
            skipped += 1
            reasons["invalid_user_id"] += 1
            if len(examples["invalid_user_id"]) < 5:
                examples["invalid_user_id"].append(user_id)
            continue
        try:
            await message.bot.send_message(user_id, text)
            delivered += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(getattr(exc, "retry_after", 1)))
            try:
                await message.bot.send_message(user_id, text)
                delivered += 1
            except Exception as retry_exc:
                reason = classify_broadcast_error(retry_exc)
                reasons[reason] += 1
                if len(examples[reason]) < 5:
                    examples[reason].append(user_id)
                failed += 1
        except Exception as exc:
            reason = classify_broadcast_error(exc)
            reasons[reason] += 1
            if len(examples[reason]) < 5:
                examples[reason].append(user_id)
            failed += 1
        if (delivered + failed + skipped) % 25 == 0:
            await asyncio.sleep(0.05)
    summary = format_broadcast_summary(delivered, failed, skipped, reasons, examples)
    await economy.db.execute(
        "INSERT INTO broadcast_log (admin_id, text, delivered, failed) VALUES (?, ?, ?, ?)",
        (message.from_user.id, f"{text}\n\n--- broadcast summary ---\n{summary}", delivered, failed),
    )
    await state.clear()
    await message.reply(summary)


@router.message(Command("cancel"))
async def cancel_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.reply("Действие отменено.")


@router.message(F.text.regexp(r"(?i)^выдать\s+тс?\s+(победителям|участникам)\s+\-?\d+"))
async def give_prizes_from_game_reply(message: Message, economy: EconomyService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await is_admin_user(message.from_user.id, economy, settings):
        await message.reply("Нет доступа.")
        return
    if not message.reply_to_message or not (message.reply_to_message.text or message.reply_to_message.caption):
        await message.reply("Команду нужно отправить реплаем на сообщение с завершённой игрой.")
        return
    m = re.match(r"(?i)^выдать\s+(тс|т)\s+(победителям|участникам)\s+(\-?\d+)", message.text or "")
    if not m:
        return
    currency = "TC" if m.group(1).lower() == "тс" else "T"
    scope = m.group(2).lower()
    amount = int(m.group(3))
    ids = extract_game_user_ids(message.reply_to_message, scope)
    if not ids:
        await message.reply("Не нашла ID пользователей в сообщении игры. Нужны Telegram mentions или числовые ID.")
        return
    ok = fail = 0
    successful_ids: list[int] = []
    for uid in ids:
        try:
            if currency == "T":
                await economy.add_taurons(uid, amount, f"game_reward:{scope}")
            else:
                await economy.add_taurcoins(uid, amount, f"game_reward:{scope}")
            successful_ids.append(uid)
            ok += 1
        except EconomyError:
            fail += 1
    recipient_rows = []
    for uid in successful_ids:
        row = await economy.profile(uid)
        if row is not None:
            recipient_rows.append(row)
    if recipient_rows:
        await send_admin_action_log(
            message,
            settings,
            format_game_reward_log(message.from_user, recipient_rows, currency, amount, scope),
        )
    await message.reply(f"Начислено {amount}{currency}: успешно {ok}, ошибок {fail}.")
