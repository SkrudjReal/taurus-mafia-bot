from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_LOG_CHAT_ID = -1003333957923
DEFAULT_LOG_THREAD_ID = 2215
DEFAULT_ADMIN_ACTION_LOG_CHAT_ID = -1003333957923
DEFAULT_ADMIN_ACTION_LOG_THREAD_ID = 2213
DEFAULT_PLAYER_ACTION_LOG_CHAT_ID = -1003333957923
DEFAULT_PLAYER_ACTION_LOG_THREAD_ID = 2217
DEFAULT_ROULETTE_LOG_CHAT_ID = -1003333957923
DEFAULT_ROULETTE_LOG_THREAD_ID = 18657
DEFAULT_BONUS_REQUEST_LOG_CHAT_ID = -1003333957923
DEFAULT_BONUS_REQUEST_LOG_THREAD_ID = 1


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    database_path: Path = Field(default=Path("data/taurus_mafia.db"), alias="DATABASE_PATH")
    owner_id: int = Field(default=0, alias="OWNER_ID")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    log_chat_id: int | None = Field(default=DEFAULT_LOG_CHAT_ID, alias="LOG_CHAT_ID")
    log_thread_id: int | None = Field(default=DEFAULT_LOG_THREAD_ID, alias="LOG_THREAD_ID")
    admin_action_log_chat_id: int | None = Field(default=DEFAULT_ADMIN_ACTION_LOG_CHAT_ID, alias="ADMIN_ACTION_LOG_CHAT_ID")
    admin_action_log_thread_id: int | None = Field(default=DEFAULT_ADMIN_ACTION_LOG_THREAD_ID, alias="ADMIN_ACTION_LOG_THREAD_ID")
    player_action_log_chat_id: int | None = Field(default=DEFAULT_PLAYER_ACTION_LOG_CHAT_ID, alias="PLAYER_ACTION_LOG_CHAT_ID")
    player_action_log_thread_id: int | None = Field(default=DEFAULT_PLAYER_ACTION_LOG_THREAD_ID, alias="PLAYER_ACTION_LOG_THREAD_ID")
    roulette_log_chat_id: int | None = Field(default=DEFAULT_ROULETTE_LOG_CHAT_ID, alias="ROULETTE_LOG_CHAT_ID")
    roulette_log_thread_id: int | None = Field(default=DEFAULT_ROULETTE_LOG_THREAD_ID, alias="ROULETTE_LOG_THREAD_ID")
    bonus_request_log_chat_id: int | None = Field(default=DEFAULT_BONUS_REQUEST_LOG_CHAT_ID, alias="BONUS_REQUEST_LOG_CHAT_ID")
    bonus_request_log_thread_id: int | None = Field(default=DEFAULT_BONUS_REQUEST_LOG_THREAD_ID, alias="BONUS_REQUEST_LOG_THREAD_ID")

    @field_validator(
        "log_chat_id",
        "log_thread_id",
        "admin_action_log_chat_id",
        "admin_action_log_thread_id",
        "player_action_log_chat_id",
        "player_action_log_thread_id",
        "roulette_log_chat_id",
        "roulette_log_thread_id",
        "bonus_request_log_chat_id",
        "bonus_request_log_thread_id",
        mode="before",
    )
    @classmethod
    def empty_string_means_none(cls, value):
        if value == "":
            return None
        return value

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        if self.owner_id:
            ids.add(self.owner_id)
        for part in self.admin_ids_raw.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return ids

    @property
    def token_is_placeholder(self) -> bool:
        token = (self.bot_token or "").strip()
        lowered = token.lower()
        return (
            not token
            or token in {"PUT_TELEGRAM_BOT_TOKEN_HERE", "YOUR_BOT_TOKEN"}
            or "placeholder" in lowered
            or "test" in lowered
            or token.startswith("токен_")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
