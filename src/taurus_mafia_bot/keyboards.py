from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Профиль"), KeyboardButton(text="Магазин")],
            [KeyboardButton(text="Мои бонусы"), KeyboardButton(text="Мои задания")],
        ],
        resize_keyboard=True,
    )


def convert_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Конвертировать", callback_data="convert")]])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Управление валютой", callback_data="admin_currency")],
            [InlineKeyboardButton(text="Управление заданиями", callback_data="admin_missions")],
            [InlineKeyboardButton(text="Управление бонусами", callback_data="admin_bonuses")],
            [InlineKeyboardButton(text="Закрыть", callback_data="admin_close")],
        ]
    )


def back_keyboard(target: str = "admin_back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=target)]])
