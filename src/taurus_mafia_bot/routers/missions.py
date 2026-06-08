from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.keyboards import back_keyboard
from taurus_mafia_bot.services.economy import EconomyService
from taurus_mafia_bot.services.missions import MissionService

router = Router(name="missions")


class MissionState(StatesGroup):
    waiting_proof = State()
    waiting_new_mission = State()
    waiting_import = State()


async def require_admin(message_or_callback, economy: EconomyService, settings: Settings) -> bool:
    user = message_or_callback.from_user
    return bool(user and await economy.is_admin(user.id, settings))


@router.message(F.text == "Мои задания")
async def my_tasks(message: Message, economy: EconomyService, missions: MissionService) -> None:
    assert message.from_user is not None
    await economy.ensure_user(message.from_user)
    await missions.ensure_for_user(message.from_user.id)
    active = await missions.active_missions(message.from_user.id)
    all_missions = missions.load()
    if not active:
        await message.reply("У тебя нет активных заданий")
        return
    rows = []
    text = "<b>Твои активные задания:</b>\n\n"
    for item in active:
        mission = all_missions.get(str(item["mission_id"]))
        if not mission:
            continue
        mid = item["mission_id"]
        is_reported = item["status"] == "reported"
        status_text = "\n<b>Статус:</b> <i>отчет на проверке</i>" if is_reported else ""
        text += (
            f"<b>{mission['name']} (ID: <i>{mid}</i>)</b>\n"
            f"<b>Цель:</b> <i>{mission['description']}</i>\n"
            f"<b>Награда:</b> <i>{mission.get('reward_taurons', 0)} Taurons, {mission.get('reward_taurcoins', 0)} Taurcoins</i>{status_text}\n\n"
        )
        if not is_reported:
            rows.append([InlineKeyboardButton(text=f"Отчет по заданию {mid}", callback_data=f"report_mission:{mid}")])
    await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("report_mission:"))
async def report_mission(callback: CallbackQuery, state: FSMContext) -> None:
    mission_id = int((callback.data or "").split(":")[1])
    await state.update_data(mission_id=mission_id)
    await state.set_state(MissionState.waiting_proof)
    await callback.answer()
    if callback.message:
        await callback.message.answer("<b>Пожалуйста, отправьте скриншот, документ или текстовое подтверждение выполнения задания.</b>")


@router.message(MissionState.waiting_proof)
async def handle_proof(message: Message, state: FSMContext, missions: MissionService, settings: Settings) -> None:
    assert message.from_user is not None
    data = await state.get_data()
    mission_id = int(data["mission_id"])
    file_id = None
    kind = "text"
    if message.photo:
        file_id = message.photo[-1].file_id
        kind = "photo"
    elif message.document:
        file_id = message.document.file_id
        kind = "document"
    report_data = json.dumps({"kind": kind, "file_id": file_id, "text": message.text or message.caption or ""}, ensure_ascii=False)
    await missions.report(message.from_user.id, mission_id, report_data)
    buttons = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm_task:{message.from_user.id}:{mission_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"rejecttask:{message.from_user.id}:{mission_id}"),
    ]])
    admin_text = (
        f"<b>Новый отчет о выполнении задания</b>\n"
        f"Пользователь: {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
        f"Задание: <code>{mission_id}</code>\n"
        f"Описание: {message.text or message.caption or 'Без описания'}"
    )
    if settings.log_chat_id:
        try:
            kwargs = {"chat_id": settings.log_chat_id, "reply_markup": buttons}
            if settings.log_thread_id:
                kwargs["message_thread_id"] = settings.log_thread_id
            if kind == "photo":
                await message.bot.send_photo(photo=file_id, caption=admin_text, **kwargs)
            elif kind == "document":
                await message.bot.send_document(document=file_id, caption=admin_text, **kwargs)
            else:
                await message.bot.send_message(text=admin_text, **kwargs)
        except Exception:
            pass
    await state.clear()
    await message.answer("<b>Отчет отправлен на модерацию. Ожидайте подтверждения от администратора.</b>")


@router.callback_query(F.data.startswith("confirm_task:") | F.data.startswith("rejecttask:"))
async def process_task(callback: CallbackQuery, economy: EconomyService, missions: MissionService, settings: Settings) -> None:
    if not await require_admin(callback, economy, settings):
        await callback.answer("У вас нет прав для выполнения этого действия.", show_alert=True)
        return
    action, user_id_s, mission_id_s = (callback.data or "").split(":")
    user_id, mission_id = int(user_id_s), int(mission_id_s)
    all_missions = missions.load()
    mission = all_missions.get(str(mission_id), {"name": f"#{mission_id}", "reward_taurons": 0, "reward_taurcoins": 0})
    if action == "confirm_task":
        ok = await missions.complete(user_id, mission_id, economy)
        if not ok:
            await callback.answer("Ошибка: задание не найдено", show_alert=True)
            return
        await callback.answer("Задание подтверждено!", show_alert=True)
        try:
            await callback.bot.send_message(user_id, f"Задание <b>{mission['name']}</b> подтверждено! Награда: {mission.get('reward_taurons', 0)} Taurons, {mission.get('reward_taurcoins', 0)} Taurcoins")
        except Exception:
            pass
        text = f"<b>Задание подтверждено</b>\nID пользователя: <code>{user_id}</code>\nЗадание: {mission['name']}"
    else:
        await missions.reject(user_id, mission_id)
        await callback.answer("Задание отклонено", show_alert=True)
        try:
            await callback.bot.send_message(user_id, f"<b>Задание отклонено</b>\nЗадание: {mission['name']}\nМожно отправить отчет заново.")
        except Exception:
            pass
        text = f"<b>Задание отклонено</b>\nID пользователя: <code>{user_id}</code>\nЗадание: {mission['name']}"
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=None)
        except Exception:
            await callback.message.answer(text)


@router.callback_query(F.data == "admin_missions")
async def admin_missions(callback: CallbackQuery, missions: MissionService) -> None:
    await callback.answer()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Просмотреть задания", callback_data="view_missions")],
        [InlineKeyboardButton(text="Экспорт заданий (JSON)", callback_data="export_missions")],
        [InlineKeyboardButton(text="Импорт/добавить задание (JSON)", callback_data="import_missions")],
        [InlineKeyboardButton(text="Назад", callback_data="admin_back")],
    ])
    if callback.message:
        await callback.message.edit_text(f"<b>Управление заданиями</b>\nВсего заданий: {len(missions.load())}", reply_markup=markup)


@router.callback_query(F.data == "view_missions")
async def view_missions(callback: CallbackQuery, missions: MissionService) -> None:
    await callback.answer()
    data = missions.load()
    text = "<b>Список заданий:</b>\n\n" + "\n".join(
        f"<code>{mid}</code>. <b>{m.get('name')}</b> — {m.get('description')} ({m.get('reward_taurons',0)}T/{m.get('reward_taurcoins',0)}TC)"
        for mid, m in data.items()
    )
    if callback.message:
        await callback.message.edit_text(text or "Заданий нет.", reply_markup=back_keyboard("admin_missions"))


@router.callback_query(F.data == "export_missions")
async def export_missions(callback: CallbackQuery, missions: MissionService) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer("<pre>" + json.dumps(missions.load(), ensure_ascii=False, indent=2) + "</pre>")


@router.callback_query(F.data == "import_missions")
async def import_missions(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(MissionState.waiting_import)
    if callback.message:
        await callback.message.answer("Отправь JSON словарь заданий или одно задание: <code>{\"name\":..., \"description\":..., \"reward_taurons\":10, \"reward_taurcoins\":0}</code>")


@router.message(MissionState.waiting_import)
async def process_mission_import(message: Message, state: FSMContext, missions: MissionService) -> None:
    try:
        payload = json.loads(message.text or "")
        current = missions.load()
        if all(k in payload for k in ("name", "description")):
            new_id = str(max([int(k) for k in current] or [0]) + 1)
            current[new_id] = payload
        elif isinstance(payload, dict):
            current = payload
        else:
            raise ValueError("Ожидался JSON-объект")
        missions.save(current)
    except Exception as exc:
        await message.answer(f"Ошибка импорта: {exc}")
        return
    await state.clear()
    await message.answer(f"<b>Задания сохранены.</b> Всего: {len(current)}")


@router.message(Command("reset_missions"))
async def reset_missions(message: Message, economy: EconomyService, missions: MissionService, settings: Settings) -> None:
    assert message.from_user is not None
    if not await economy.is_admin(message.from_user.id, settings):
        await message.reply("Нет доступа.")
        return
    count = await missions.reset_completed()
    await message.reply(f"<b>Сброшено выполненных заданий:</b> {count}")
