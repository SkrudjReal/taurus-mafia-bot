from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.keyboards import back_keyboard
from taurus_mafia_bot.services.economy import EconomyError, EconomyService
from taurus_mafia_bot.services.shop import BonusType, ShopService

router = Router(name="shop")


class ShopState(StatesGroup):
    waiting_bonus_data = State()


def shop_keyboard(items: list[BonusType]) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Купить {item.name} - {item.price} T", callback_data=f"confirm_buy:{item.id}")]
            for item in items
        ]
    )


async def notify_admins(
    callback: CallbackQuery,
    settings: Settings,
    text: str,
    *,
    log_chat_id: int | None = None,
    log_thread_id: int | None = None,
) -> None:
    targets = list(settings.admin_ids)
    target_log_chat_id = log_chat_id if log_chat_id is not None else settings.log_chat_id
    if target_log_chat_id:
        try:
            kwargs = {"chat_id": target_log_chat_id, "text": text}
            thread_id = log_thread_id if log_thread_id is not None else settings.log_thread_id
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await callback.bot.send_message(**kwargs)
        except Exception:
            pass
    for admin_id in targets:
        if callback.from_user and admin_id == callback.from_user.id:
            continue
        try:
            await callback.bot.send_message(admin_id, text)
        except Exception:
            pass


@router.message(F.text == "Магазин")
async def shop_menu(message: Message, economy: EconomyService, shop: ShopService) -> None:
    assert message.from_user is not None
    await economy.ensure_user(message.from_user)
    profile = await economy.profile(message.from_user.id)
    if profile is None:
        await message.reply("<b>Профиль не найден. Попробуй команду /start</b>")
        return
    items = await shop.list_bonus_types()
    if not items:
        await message.reply("<b>Магазин временно пуст. Загляните позже!</b>")
        return
    text = "<b>Магазин Taurus Mafia</b>\n━━━━━━━━━━━━━━━━━━━━\n" f"Ваш баланс: <b>{profile['taurons']} T</b>\n\n"
    for item in items:
        text += f"<b>{item.name}</b>\n - <i>{item.description}</i>\nЦена: <i>{item.price} T</i>\n\n"
    await message.reply(text, reply_markup=shop_keyboard(items))


@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_purchase(callback: CallbackQuery, economy: EconomyService, shop: ShopService) -> None:
    assert callback.from_user is not None
    bonus_id = int((callback.data or "").split(":")[1])
    bonus = await shop.get_bonus_type(bonus_id)
    if bonus is None:
        await callback.answer("Этот бонус больше не доступен", show_alert=True)
        return
    profile = await economy.profile(callback.from_user.id)
    if profile is None:
        await economy.ensure_user(callback.from_user)
        profile = await economy.profile(callback.from_user.id)
    assert profile is not None
    if int(profile["taurons"]) < bonus.price:
        await callback.answer("Недостаточно средств для покупки", show_alert=True)
        if callback.message:
            await callback.message.answer(
                f"<b>Недостаточно средств</b>\n\n<b>Цена:</b> <i>{bonus.price} T</i>\n<b>Ваш баланс:</b> <i>{profile['taurons']} T</i>"
            )
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить", callback_data=f"buy_confirm:{bonus.id}"),
        InlineKeyboardButton(text="Отмена", callback_data="cancel_purchase"),
    ]])
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"<b>Подтвердите покупку:</b>\n"
            f"• {bonus.name}\n"
            f"• Цена: <i>{bonus.price} T</i>\n\n"
            f"Ваш баланс: <i>{profile['taurons']} T</i> → <i>{int(profile['taurons']) - bonus.price} T</i>",
            reply_markup=markup,
        )


@router.callback_query(F.data == "cancel_purchase")
async def cancel_purchase(callback: CallbackQuery) -> None:
    await callback.answer("Покупка отменена")
    if callback.message:
        await callback.message.delete()


@router.callback_query(F.data.startswith("buy_confirm:"))
async def process_purchase(callback: CallbackQuery, economy: EconomyService, shop: ShopService, settings: Settings) -> None:
    assert callback.from_user is not None
    bonus_id = int((callback.data or "").split(":")[1])
    bonus = await shop.get_bonus_type(bonus_id)
    if bonus is None:
        await callback.answer("Этот бонус больше не доступен", show_alert=True)
        return
    profile = await economy.profile(callback.from_user.id)
    if profile is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return
    if int(profile["taurons"]) < bonus.price:
        await callback.answer(f"Недостаточно средств\n\nЦена: {bonus.price} T\nВаш баланс: {profile['taurons']} T", show_alert=True)
        return
    await economy.add_taurons(callback.from_user.id, -bonus.price, f"buy_bonus:{bonus.id}")
    await economy.grant_prize(callback.from_user.id, str(bonus.id), bonus.name, 1)
    updated = await economy.profile(callback.from_user.id)
    assert updated is not None
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"<b>Покупка успешно завершена!</b>\n"
            f"• {bonus.name}\n"
            f"• Потрачено: <i>{bonus.price} T</i>\n"
            f"<b>Новый баланс:</b> <i>{updated['taurons']} T</i>\n\n"
            f"Куплено 1 шт. {bonus.name} за {bonus.price} Taurons!"
        )
    await notify_admins(callback, settings, f"<b>Покупка бонуса</b>\nПользователь: <code>{callback.from_user.id}</code>\nБонус: {bonus.name}\nЦена: {bonus.price}T")


@router.message(F.text == "Мои бонусы")
async def my_bonuses(message: Message, economy: EconomyService) -> None:
    assert message.from_user is not None
    await economy.ensure_user(message.from_user)
    rows = await economy.db.fetch_all(
        "SELECT prize_code, prize_name, count FROM user_prizes WHERE user_id = ? AND count > 0 ORDER BY prize_name",
        (message.from_user.id,),
    )
    if not rows:
        await message.reply("<b>У вас пока нет купленных бонусов.</b>")
        return
    text = "<b>Ваши бонусы</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    keyboard_rows = []
    for row in rows:
        text += f"• <b>{row['prize_name']}</b> - {row['count']} шт.\n\n"
        keyboard_rows.append([InlineKeyboardButton(text=f"Использовать {row['prize_name']}", callback_data=f"use_bonus:{message.from_user.id}:{row['prize_code']}")])
    await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows))


@router.callback_query(F.data.startswith("use_bonus:"))
async def use_bonus(callback: CallbackQuery, economy: EconomyService, settings: Settings) -> None:
    _, user_id_s, code = (callback.data or "").split(":", 2)
    user_id = int(user_id_s)
    if callback.from_user.id != user_id:
        await callback.answer("Вы можете использовать только свои бонусы.", show_alert=True)
        return
    row = await economy.db.fetch_one("SELECT prize_name FROM user_prizes WHERE user_id = ? AND prize_code = ? AND count > 0", (user_id, code))
    if row is None:
        await callback.answer("Бонус не найден.", show_alert=True)
        return
    buttons = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm_use:{user_id}:{code}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"reject_use:{user_id}:{code}"),
    ]])
    await callback.answer("Заявка отправлена", show_alert=True)
    text = f"<b>Заявка на использование бонуса</b>\nПользователь: <code>{user_id}</code>\nБонус: {row['prize_name']}"
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(admin_id, text, reply_markup=buttons)
        except Exception:
            pass
    if settings.log_chat_id:
        try:
            kwargs = {"chat_id": settings.log_chat_id, "text": text, "reply_markup": buttons}
            if settings.log_thread_id:
                kwargs["message_thread_id"] = settings.log_thread_id
            await callback.bot.send_message(**kwargs)
        except Exception:
            pass


@router.callback_query(F.data.startswith("confirm_use:") | F.data.startswith("reject_use:"))
async def process_bonus_use(callback: CallbackQuery, economy: EconomyService, settings: Settings) -> None:
    if not await economy.is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    action, user_id_s, code = (callback.data or "").split(":", 2)
    user_id = int(user_id_s)
    row = await economy.db.fetch_one("SELECT prize_name FROM user_prizes WHERE user_id = ? AND prize_code = ? AND count > 0", (user_id, code))
    if row is None:
        await callback.answer("Бонус не найден.", show_alert=True)
        return
    if action == "confirm_use":
        try:
            await economy.use_prize(user_id, code)
        except EconomyError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer("Использование подтверждено", show_alert=True)
        text = f"<b>Бонус использован</b>\nПользователь: <code>{user_id}</code>\nБонус: {row['prize_name']}"
        try:
            await callback.bot.send_message(user_id, f"Использование бонуса <b>{row['prize_name']}</b> подтверждено.")
        except Exception:
            pass
    else:
        await callback.answer("Использование отклонено", show_alert=True)
        text = f"<b>Бонус отклонён</b>\nПользователь: <code>{user_id}</code>\nБонус: {row['prize_name']}"
        try:
            await callback.bot.send_message(user_id, f"Использование бонуса <b>{row['prize_name']}</b> отклонено.")
        except Exception:
            pass
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=None)
        except Exception:
            await callback.message.answer(text)


@router.callback_query(F.data == "admin_bonuses")
async def admin_bonuses(callback: CallbackQuery, economy: EconomyService, settings: Settings) -> None:
    if not await economy.is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Список бонусов", callback_data="list_bonuses")],
        [InlineKeyboardButton(text="Добавить бонус", callback_data="add_bonus")],
        [InlineKeyboardButton(text="Удалить бонус", callback_data="delete_bonus")],
        [InlineKeyboardButton(text="Назад", callback_data="admin_back")],
    ])
    if callback.message:
        await callback.message.edit_text("<b>Управление бонусами</b>", reply_markup=markup)


@router.callback_query(F.data == "list_bonuses")
async def list_bonuses(callback: CallbackQuery, shop: ShopService) -> None:
    await callback.answer()
    items = await shop.list_bonus_types()
    text = "<b>Список бонусов:</b>\n\n" + ("\n".join(f"<code>{i.id}</code>. <b>{i.name}</b> — {i.price}T\n{i.description}" for i in items) or "Бонусов нет.")
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_keyboard("admin_bonuses"))


@router.callback_query(F.data == "add_bonus")
async def add_bonus_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ShopState.waiting_bonus_data)
    if callback.message:
        await callback.message.answer("Отправь бонус в формате:\n<code>Название | Описание | Цена</code>")


@router.message(ShopState.waiting_bonus_data)
async def add_bonus_process(message: Message, state: FSMContext, shop: ShopService) -> None:
    try:
        name, description, price_s = [part.strip() for part in (message.text or "").split("|", 2)]
        bonus = await shop.create_bonus_type(name, description, int(price_s))
    except Exception as exc:
        await message.answer(f"Ошибка добавления бонуса: {exc}\nФормат: <code>Название | Описание | Цена</code>")
        return
    await state.clear()
    await message.answer(f"<b>Бонус добавлен:</b> <code>{bonus.id}</code>. {bonus.name} — {bonus.price}T")


@router.callback_query(F.data == "delete_bonus")
async def delete_bonus_list(callback: CallbackQuery, shop: ShopService) -> None:
    await callback.answer()
    items = await shop.list_bonus_types()
    rows = [[InlineKeyboardButton(text=f"Удалить {i.name}", callback_data=f"confirm_delete_bonus:{i.id}")] for i in items]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="admin_bonuses")])
    if callback.message:
        await callback.message.edit_text("Выбери бонус для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("confirm_delete_bonus:"))
async def confirm_delete_bonus(callback: CallbackQuery, shop: ShopService) -> None:
    bonus_id = int((callback.data or "").split(":")[1])
    bonus = await shop.get_bonus_type(bonus_id)
    if bonus is None:
        await callback.answer("Бонус не найден", show_alert=True)
        return
    await callback.answer()
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, удалить", callback_data=f"delete_bonus_confirm:{bonus_id}"),
        InlineKeyboardButton(text="Отмена", callback_data="admin_bonuses"),
    ]])
    if callback.message:
        await callback.message.edit_text(f"Удалить бонус <b>{bonus.name}</b>?", reply_markup=markup)


@router.callback_query(F.data.startswith("delete_bonus_confirm:"))
async def delete_bonus_confirm(callback: CallbackQuery, shop: ShopService) -> None:
    bonus_id = int((callback.data or "").split(":")[1])
    deleted = await shop.delete_bonus_type(bonus_id)
    await callback.answer("Бонус удалён" if deleted else "Бонус не найден", show_alert=True)
    if callback.message:
        await callback.message.edit_text("Бонус удалён." if deleted else "Бонус не найден.", reply_markup=back_keyboard("admin_bonuses"))
