from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.keyboards import back_keyboard
from taurus_mafia_bot.services.economy import EconomyService
from taurus_mafia_bot.services.missions import MissionService

router = Router(name="missions")


MISSION_REQUIRED_FIELDS = ("name", "description")
ALBUM_COLLECT_DELAY = 1.0


@dataclass
class PendingMissionAlbum:
    messages: list[Message] = field(default_factory=list)
    state: FSMContext | None = None
    missions: MissionService | None = None
    settings: Settings | None = None
    task: asyncio.Task | None = None


PENDING_MISSION_ALBUMS: dict[tuple[int, int, str], PendingMissionAlbum] = {}


class MissionState(StatesGroup):
    waiting_proof = State()
    waiting_new_mission = State()
    waiting_import = State()


async def require_admin(message_or_callback, economy: EconomyService, settings: Settings) -> bool:
    user = message_or_callback.from_user
    return bool(user and await economy.is_admin(user.id, settings))


def normalize_mission(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Задание должно быть JSON-объектом")
    if not all(payload.get(field) for field in MISSION_REQUIRED_FIELDS):
        raise ValueError("У задания должны быть поля name и description")

    mission = dict(payload)
    mission["name"] = str(mission["name"]).strip()
    mission["description"] = str(mission["description"]).strip()
    mission["reward_taurons"] = int(mission.get("reward_taurons", 0) or 0)
    mission["reward_taurcoins"] = int(mission.get("reward_taurcoins", 0) or 0)
    return mission


def apply_mission_import(current: dict[str, dict[str, Any]], payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Ожидался JSON-объект")

    if all(field in payload for field in MISSION_REQUIRED_FIELDS):
        new_id = str(max([int(k) for k in current if str(k).isdigit()] or [0]) + 1)
        updated = dict(current)
        updated[new_id] = normalize_mission(payload)
        return updated

    updated = {}
    for mission_id, mission in payload.items():
        mission_id_s = str(mission_id).strip()
        if not mission_id_s.isdigit():
            raise ValueError(f"ID задания должен быть числом: {mission_id_s}")
        updated[mission_id_s] = normalize_mission(mission)
    return updated


async def read_import_payload(message: Message) -> Any:
    if message.text:
        raw = message.text
    elif message.document:
        if message.document.file_size and message.document.file_size > 1024 * 1024:
            raise ValueError("JSON-файл слишком большой. Максимум: 1 MB")
        buffer = io.BytesIO()
        await message.bot.download(message.document, destination=buffer)
        raw = buffer.getvalue().decode("utf-8-sig")
    else:
        raise ValueError("Отправь JSON текстом или прикрепи .json файл")
    return json.loads(raw)


def format_mission_report_text(user_full_name: str, user_id: int, mission_id: int, mission: dict[str, Any] | None, proof_text: str | None) -> str:
    mission_name = str((mission or {}).get("name") or f"#{mission_id}")
    return (
        f"<b>Новый отчет о выполнении задания</b>\n"
        f"Пользователь: {escape(user_full_name)} (<code>{user_id}</code>)\n"
        f"Задание: <b>{escape(mission_name)}</b> (<code>{mission_id}</code>)\n"
        f"Описание: {escape(proof_text or 'Без описания')}"
    )


def mission_report_buttons(user_id: int, mission_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm_task:{user_id}:{mission_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"rejecttask:{user_id}:{mission_id}"),
    ]])


def proof_item(message: Message) -> dict[str, Any]:
    file_id = None
    kind = "text"
    if message.photo:
        file_id = message.photo[-1].file_id
        kind = "photo"
    elif message.document:
        file_id = message.document.file_id
        kind = "document"
    elif message.video:
        file_id = message.video.file_id
        kind = "video"
    return {"kind": kind, "file_id": file_id, "text": message.text or message.caption or ""}


def proof_report_data(messages: list[Message]) -> str:
    items = [proof_item(message) for message in messages]
    payload: dict[str, Any]
    if len(items) == 1:
        payload = items[0]
    else:
        payload = {"kind": "album", "items": items}
    return json.dumps(payload, ensure_ascii=False)


def proof_text(messages: list[Message]) -> str | None:
    return next((item["text"] for item in (proof_item(message) for message in messages) if item["text"]), None)


def input_media_from_message(message: Message, caption: str | None = None):
    if message.photo:
        return InputMediaPhoto(media=message.photo[-1].file_id, caption=caption)
    if message.document:
        return InputMediaDocument(media=message.document.file_id, caption=caption)
    if message.video:
        return InputMediaVideo(media=message.video.file_id, caption=caption)
    return None


async def send_mission_report_to_admin(messages: list[Message], admin_text: str, buttons: InlineKeyboardMarkup, settings: Settings) -> None:
    if not settings.log_chat_id:
        return

    first = messages[0]
    kwargs: dict[str, Any] = {"chat_id": settings.log_chat_id}
    if settings.log_thread_id:
        kwargs["message_thread_id"] = settings.log_thread_id

    try:
        if len(messages) > 1:
            media = [
                input_media_from_message(message, admin_text if index == 0 else None)
                for index, message in enumerate(messages)
            ]
            media = [item for item in media if item is not None]
            if len(media) > 1:
                await first.bot.send_media_group(media=media, **kwargs)
                await first.bot.send_message(text=admin_text, reply_markup=buttons, **kwargs)
                return

        item = proof_item(first)
        if item["kind"] == "photo":
            await first.bot.send_photo(photo=item["file_id"], caption=admin_text, reply_markup=buttons, **kwargs)
        elif item["kind"] == "document":
            await first.bot.send_document(document=item["file_id"], caption=admin_text, reply_markup=buttons, **kwargs)
        elif item["kind"] == "video":
            await first.bot.send_video(video=item["file_id"], caption=admin_text, reply_markup=buttons, **kwargs)
        else:
            await first.bot.send_message(text=admin_text, reply_markup=buttons, **kwargs)
    except Exception:
        pass


async def submit_mission_proof(messages: list[Message], state: FSMContext, missions: MissionService, settings: Settings) -> None:
    messages = sorted(messages, key=lambda message: message.message_id)
    first = messages[0]
    assert first.from_user is not None
    data = await state.get_data()
    mission_id = int(data["mission_id"])
    await missions.report(first.from_user.id, mission_id, proof_report_data(messages))

    mission = missions.load().get(str(mission_id))
    admin_text = format_mission_report_text(
        first.from_user.full_name,
        first.from_user.id,
        mission_id,
        mission,
        proof_text(messages),
    )
    await send_mission_report_to_admin(
        messages,
        admin_text,
        mission_report_buttons(first.from_user.id, mission_id),
        settings,
    )
    await state.clear()
    await first.answer("<b>Отчет отправлен на модерацию. Ожидайте подтверждения от администратора.</b>")


async def process_album_after_delay(key: tuple[int, int, str]) -> None:
    try:
        await asyncio.sleep(ALBUM_COLLECT_DELAY)
        pending = PENDING_MISSION_ALBUMS.pop(key, None)
        if not pending or not pending.state or not pending.missions or not pending.settings or not pending.messages:
            return
        await submit_mission_proof(pending.messages, pending.state, pending.missions, pending.settings)
    except asyncio.CancelledError:
        raise
    except Exception:
        PENDING_MISSION_ALBUMS.pop(key, None)


def queue_album_proof(message: Message, state: FSMContext, missions: MissionService, settings: Settings) -> None:
    assert message.from_user is not None
    key = (message.chat.id, message.from_user.id, str(message.media_group_id))
    pending = PENDING_MISSION_ALBUMS.setdefault(key, PendingMissionAlbum())
    pending.messages.append(message)
    pending.state = state
    pending.missions = missions
    pending.settings = settings
    if pending.task and not pending.task.done():
        pending.task.cancel()
    pending.task = asyncio.create_task(process_album_after_delay(key))


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
        await callback.message.answer("<b>Пожалуйста, отправьте скриншот, альбом, документ или текстовое подтверждение выполнения задания.</b>")


@router.message(MissionState.waiting_proof)
async def handle_proof(message: Message, state: FSMContext, missions: MissionService, settings: Settings) -> None:
    assert message.from_user is not None
    if message.media_group_id:
        queue_album_proof(message, state, missions, settings)
        return
    await submit_mission_proof([message], state, missions, settings)


@router.callback_query(F.data.startswith("confirm_task:") | F.data.startswith("rejecttask:"))
async def process_task(callback: CallbackQuery, economy: EconomyService, missions: MissionService, settings: Settings) -> None:
    if not await require_admin(callback, economy, settings):
        await callback.answer("У вас нет прав для выполнения этого действия.", show_alert=True)
        return
    action, user_id_s, mission_id_s = (callback.data or "").split(":")
    user_id, mission_id = int(user_id_s), int(mission_id_s)
    all_missions = missions.load()
    mission = all_missions.get(str(mission_id), {"name": f"#{mission_id}", "reward_taurons": 0, "reward_taurcoins": 0})
    mission_name = escape(str(mission["name"]))
    if action == "confirm_task":
        ok = await missions.complete(user_id, mission_id, economy)
        if not ok:
            await callback.answer("Ошибка: задание не найдено, не отправлено или уже выполнено", show_alert=True)
            return
        await callback.answer("Задание подтверждено!", show_alert=True)
        try:
            await callback.bot.send_message(user_id, f"Задание <b>{mission_name}</b> подтверждено! Награда: {mission.get('reward_taurons', 0)} Taurons, {mission.get('reward_taurcoins', 0)} Taurcoins")
        except Exception:
            pass
        text = f"<b>Задание подтверждено</b>\nID пользователя: <code>{user_id}</code>\nЗадание: {mission_name}"
    else:
        await missions.reject(user_id, mission_id)
        await callback.answer("Задание отклонено", show_alert=True)
        try:
            await callback.bot.send_message(user_id, f"<b>Задание отклонено</b>\nЗадание: {mission_name}\nМожно отправить отчет заново.")
        except Exception:
            pass
        text = f"<b>Задание отклонено</b>\nID пользователя: <code>{user_id}</code>\nЗадание: {mission_name}"
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
        [InlineKeyboardButton(text="Создать задание", callback_data="create_mission")],
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


@router.callback_query(F.data == "create_mission")
async def create_mission(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(MissionState.waiting_new_mission)
    if callback.message:
        await callback.message.answer(
            "<b>Создание задания</b>\n"
            "Отправь одной строкой:\n"
            "<code>Название | Описание | Taurons | Taurcoins</code>\n\n"
            "Пример:\n"
            "<code>Сержант | Стать комиссаром и найти мафию | 2 | 0</code>"
        )


@router.message(MissionState.waiting_new_mission)
async def process_new_mission(message: Message, state: FSMContext, missions: MissionService) -> None:
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) not in {2, 4} or not all(parts[:2]):
        await message.answer("Формат: <code>Название | Описание | Taurons | Taurcoins</code>")
        return

    try:
        reward_taurons = int(parts[2]) if len(parts) == 4 else 0
        reward_taurcoins = int(parts[3]) if len(parts) == 4 else 0
    except ValueError:
        await message.answer("Награды должны быть числами.")
        return

    current = missions.load()
    new_id = str(max([int(k) for k in current if str(k).isdigit()] or [0]) + 1)
    current[new_id] = normalize_mission(
        {
            "name": parts[0],
            "description": parts[1],
            "reward_taurons": reward_taurons,
            "reward_taurcoins": reward_taurcoins,
        }
    )
    missions.save(current)
    await state.clear()
    await message.answer(f"<b>Задание создано.</b>\nID: <code>{new_id}</code>\nНазвание: <b>{escape(parts[0])}</b>")


@router.callback_query(F.data == "import_missions")
async def import_missions(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(MissionState.waiting_import)
    if callback.message:
        await callback.message.answer(
            "Отправь JSON текстом или прикрепи .json файл.\n"
            "Можно отправить словарь заданий или одно задание: "
            "<code>{\"name\":\"...\", \"description\":\"...\", \"reward_taurons\":10, \"reward_taurcoins\":0}</code>"
        )


@router.message(MissionState.waiting_import)
async def process_mission_import(message: Message, state: FSMContext, missions: MissionService) -> None:
    try:
        payload = await read_import_payload(message)
        current = apply_mission_import(missions.load(), payload)
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
