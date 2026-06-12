import json
import sqlite3
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aiogram_dialog import ShowMode, StartMode

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.db import Database
from taurus_mafia_bot.routers.admin import extract_game_user_ids, format_admin_action_log, format_broadcast_summary, format_game_reward_log, format_player_transfer_log
from taurus_mafia_bot.routers.missions import apply_mission_import, format_mission_report_text, proof_report_data, proof_text
from taurus_mafia_bot.routers.roulette import format_admin_spin_result, format_spin_result, format_user_mention
from taurus_mafia_bot.routers.shop import notify_admins, send_bonus_use_request
from taurus_mafia_bot.routers.start import TopDialogSG, extract_user_identifier, format_taurons_top, split_text_by_lines, start_top_dialog, user_info_text
from taurus_mafia_bot.services.economy import EconomyError, EconomyService
from taurus_mafia_bot.services.missions import MissionService
from taurus_mafia_bot.services.roulette import RouletteService, roulette_info_text
from taurus_mafia_bot.services.shop import ShopService


class FixedRng:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


class FakeBot:
    def __init__(self, *, send_message_exception: Exception | None = None) -> None:
        self.messages = []
        self.photos = []
        self.documents = []
        self.send_message_exception = send_message_exception

    async def send_message(self, *args, **kwargs):
        if self.send_message_exception is not None:
            raise self.send_message_exception
        self.messages.append((args, kwargs))

    async def send_photo(self, *args, **kwargs):
        self.photos.append((args, kwargs))

    async def send_document(self, *args, **kwargs):
        self.documents.append((args, kwargs))


class FakeTopDialogBgManager:
    def __init__(self) -> None:
        self.starts = []

    async def start(self, *args, **kwargs):
        self.starts.append((args, kwargs))


class FakeTopDialogManager:
    def __init__(self) -> None:
        self.bg_calls = []
        self.bg_manager = FakeTopDialogBgManager()

    def bg(self, *args, **kwargs):
        self.bg_calls.append((args, kwargs))
        return self.bg_manager


async def seed(db: Database, user_id: int, taurons: int = 0, taurcoins: int = 0, username: str = "user", full_name: str | None = None) -> None:
    await db.execute(
        """
        INSERT INTO users (telegram_id, full_name, username, taurons, taurcoins, is_admin)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (user_id, full_name or f"User {user_id}", username, taurons, taurcoins),
    )


@pytest.mark.asyncio
async def test_migrate_converts_legacy_taurus_db_users_schema(tmp_path):
    db_path = tmp_path / "taurus.db"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            taurons INTEGER DEFAULT 0,
            taurcoins INTEGER DEFAULT 0
        );
        CREATE TABLE parametrs (name TEXT PRIMARY KEY, value REAL);
        CREATE TABLE user_missions (
            user_id INTEGER,
            mission_id INTEGER,
            status TEXT DEFAULT 'pending',
            report_data TEXT DEFAULT '',
            timestamp INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, mission_id)
        );
        INSERT INTO users (user_id, first_name, username, is_admin, taurons, taurcoins)
        VALUES (1234, 'Legacy Name', 'legacy_user', 1, 77, 5);
        INSERT INTO user_missions (user_id, mission_id, status, report_data, timestamp)
        VALUES (1234, 2, 'reported', 'proof', 1710000000);
        INSERT INTO parametrs (name, value) VALUES ('convert_rate', 10);
        """
    )
    legacy.commit()
    legacy.close()

    db = Database(db_path)
    await db.migrate()
    economy = EconomyService(db)

    columns = await db.fetch_all('PRAGMA table_info("users")')
    column_names = {row["name"] for row in columns}
    profile = await economy.profile(1234)
    mission = await db.fetch_one("SELECT * FROM user_missions WHERE user_id = ? AND mission_id = ?", (1234, 2))

    assert "telegram_id" in column_names
    assert "full_name" in column_names
    assert "user_id" not in column_names
    assert profile is not None
    assert profile["telegram_id"] == 1234
    assert profile["full_name"] == "Legacy Name"
    assert profile["username"] == "legacy_user"
    assert profile["is_admin"] == 1
    assert profile["taurons"] == 77
    assert profile["taurcoins"] == 5
    assert mission is not None
    assert mission["status"] == "reported"
    assert await economy.total_taurons() == 77
    await db.close()


@pytest.mark.asyncio
async def test_top_taurons_orders_users_and_formats_total(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=2, full_name="папочка кенни")
    await seed(db, 2, taurons=2, full_name="ಣsasha")
    await seed(db, 3, taurons=1, full_name="IliaSlime")
    await seed(db, 4, taurons=0, full_name="Zero")
    economy = EconomyService(db)

    text = format_taurons_top(await economy.top_taurons(limit=10), await economy.total_taurons())

    assert text == (
        "📊 <b>Топ богатых пользователей по Тауронам</b>\n"
        "\n"
        ' 1. <a href="tg://openmessage?user_id=1">папочка кенни</a> — <b>2</b>\n'
        ' 2. <a href="tg://openmessage?user_id=2">ಣsasha</a> — <b>2</b>\n'
        ' 3. <a href="tg://openmessage?user_id=3">IliaSlime</a> — <b>1</b>\n'
        "\n"
        "Всего тауронов: <b>5</b>"
    )
    await db.close()


@pytest.mark.asyncio
async def test_top_taurons_can_fetch_all_rows_for_dialog_pagination(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    for user_id in range(1, 13):
        await seed(db, user_id, taurons=user_id, full_name=f"User {user_id}")
    economy = EconomyService(db)

    rows = await economy.top_taurons(limit=None)
    text = format_taurons_top(rows, await economy.total_taurons())

    assert len(rows) == 12
    assert ' 1. <a href="tg://openmessage?user_id=12">User 12</a> — <b>12</b>' in text
    assert ' 12. <a href="tg://openmessage?user_id=1">User 1</a> — <b>1</b>' in text
    assert "Всего тауронов: <b>78</b>" in text
    assert TopDialogSG.TEXT.state == "TopDialogSG:TEXT"
    await db.close()


def test_top_dialog_pagination_does_not_split_html_mention_tags() -> None:
    text = "\n".join(
        ["📊 <b>Топ богатых пользователей по Тауронам</b>", ""]
        + [
            f' {index}. <a href="tg://openmessage?user_id={index}">User {index}</a> — <b>{index}</b>'
            for index in range(1, 30)
        ]
        + ["", "Всего тауронов: <b>435</b>"]
    )

    pages = split_text_by_lines(text, page_size=350)

    assert len(pages) > 1
    assert "<a " not in pages[0] or "</a>" in pages[0]
    for page in pages:
        assert len(page) <= 350
        assert page.count("<a ") == page.count("</a>")
        assert page.count("<b>") == page.count("</b>")


@pytest.mark.asyncio
async def test_top_dialog_starts_new_thread_specific_background_stack() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=777),
        message_thread_id=42,
    )
    manager = FakeTopDialogManager()

    await start_top_dialog(cast(Any, message), cast(Any, manager), "top text")

    assert len(manager.bg_calls) == 1
    _, bg_kwargs = manager.bg_calls[0]
    assert bg_kwargs["chat_id"] == -100123
    assert bg_kwargs["user_id"] == 777
    assert bg_kwargs["thread_id"] == 42
    assert bg_kwargs["stack_id"]
    assert len(manager.bg_manager.starts) == 1
    start_args, start_kwargs = manager.bg_manager.starts[0]
    assert start_args == (TopDialogSG.TEXT,)
    assert start_kwargs == {
        "mode": StartMode.NORMAL,
        "show_mode": ShowMode.SEND,
        "data": {"top_text": "top text"},
    }


@pytest.mark.asyncio
async def test_top_taurons_escapes_names(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=1, full_name="<bad&name>")
    economy = EconomyService(db)

    text = format_taurons_top(await economy.top_taurons(), await economy.total_taurons())

    assert "&lt;bad&amp;name&gt;" in text
    assert '<a href="tg://openmessage?user_id=1">&lt;bad&amp;name&gt;</a>' in text
    await db.close()


def test_settings_default_log_topics_match_legacy_bot_py() -> None:
    settings = Settings(BOT_TOKEN="123456:REALISH", OWNER_ID=1, ADMIN_IDS="1")

    assert settings.log_chat_id == -1003333957923
    assert settings.log_thread_id == 2215
    assert settings.admin_action_log_chat_id == -1003333957923
    assert settings.admin_action_log_thread_id == 2213
    assert settings.player_action_log_chat_id == -1003333957923
    assert settings.player_action_log_thread_id == 2217
    assert settings.roulette_log_chat_id == -1003333957923
    assert settings.roulette_log_thread_id == 18657
    assert settings.bonus_request_log_chat_id == -1003333957923
    assert settings.bonus_request_log_thread_id == 1


@pytest.mark.asyncio
async def test_notify_admins_can_override_log_topic_for_roulette() -> None:
    bot = FakeBot()
    callback = SimpleNamespace(bot=bot, from_user=SimpleNamespace(id=1))
    settings = Settings(
        BOT_TOKEN="123456:REALISH",
        OWNER_ID=1,
        ADMIN_IDS="1",
        LOG_CHAT_ID=-1002093104375,
        LOG_THREAD_ID=2215,
        ROULETTE_LOG_CHAT_ID=-1003333957923,
        ROULETTE_LOG_THREAD_ID=18657,
    )

    await notify_admins(
        callback,
        settings,
        "roulette log",
        log_chat_id=settings.roulette_log_chat_id,
        log_thread_id=settings.roulette_log_thread_id,
    )

    assert bot.messages == [
        (
            (),
            {"chat_id": -1003333957923, "text": "roulette log", "message_thread_id": 18657},
        )
    ]


@pytest.mark.asyncio
async def test_notify_admins_uses_default_log_topic_without_override() -> None:
    bot = FakeBot()
    callback = SimpleNamespace(bot=bot, from_user=SimpleNamespace(id=999))
    settings = Settings(
        BOT_TOKEN="123456:REALISH",
        OWNER_ID=1,
        ADMIN_IDS="2,3",
        LOG_CHAT_ID=-1002093104375,
        LOG_THREAD_ID=2215,
        ROULETTE_LOG_THREAD_ID=18657,
    )

    await notify_admins(callback, settings, "default log")

    assert bot.messages == [
        (
            (),
            {"chat_id": -1002093104375, "text": "default log", "message_thread_id": 2215},
        )
    ]


@pytest.mark.asyncio
async def test_bonus_use_request_routes_to_dedicated_topic_only() -> None:
    bot = FakeBot()
    callback = SimpleNamespace(bot=bot, from_user=SimpleNamespace(id=999))
    settings = Settings(
        BOT_TOKEN="123456:REALISH",
        OWNER_ID=1,
        ADMIN_IDS="2,3",
        LOG_CHAT_ID=-1002093104375,
        LOG_THREAD_ID=2215,
        BONUS_REQUEST_LOG_CHAT_ID=-1003333957923,
        BONUS_REQUEST_LOG_THREAD_ID=1,
    )
    buttons = SimpleNamespace()

    sent = await send_bonus_use_request(cast(Any, callback), settings, "bonus request", cast(Any, buttons))

    assert sent is True
    assert bot.messages == [
        (
            (),
            {
                "chat_id": -1003333957923,
                "text": "bonus request",
                "reply_markup": buttons,
                "message_thread_id": 1,
            },
        )
    ]


@pytest.mark.asyncio
async def test_convert_uses_configured_rate(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=2, taurcoins=25)
    economy = EconomyService(db)

    await economy.set_rate(10)
    rate, taurons, taurcoins = await economy.convert_one(1)

    assert rate == 10
    assert taurons == 3
    assert taurcoins == 15
    await db.close()


@pytest.mark.asyncio
async def test_transfer_rejects_low_balance_and_keeps_balances(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=3, username="sender")
    await seed(db, 2, taurons=0, username="receiver")
    economy = EconomyService(db)

    with pytest.raises(EconomyError, match="Недостаточно"):
        await economy.transfer(1, 2, "T", 5)

    assert await db.fetch_val("SELECT taurons FROM users WHERE telegram_id = 1") == 3
    assert await db.fetch_val("SELECT taurons FROM users WHERE telegram_id = 2") == 0
    await db.close()


@pytest.mark.asyncio
async def test_mission_report_stays_active_until_admin_approves(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1)
    missions = MissionService(db)
    missions.save({"7": {"name": "Test", "description": "Proof", "reward_taurons": 4, "reward_taurcoins": 6}})

    await missions.ensure_for_user(1)
    assert [row["mission_id"] for row in await missions.active_missions(1)] == [7]

    await missions.report(1, 7, "proof")
    assert [row["mission_id"] for row in await missions.active_missions(1)] == [7]
    assert [row["mission_id"] for row in await missions.user_missions(1, "pending")] == []
    assert [row["mission_id"] for row in await missions.user_missions(1, "reported")] == [7]

    await missions.reject(1, 7)
    assert [row["mission_id"] for row in await missions.active_missions(1)] == [7]
    assert [row["mission_id"] for row in await missions.user_missions(1, "pending")] == [7]
    await db.close()


@pytest.mark.asyncio
async def test_mission_complete_awards_and_hides_from_active(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1)
    economy = EconomyService(db)
    missions = MissionService(db)
    missions.save({"7": {"name": "Test", "description": "Proof", "reward_taurons": 4, "reward_taurcoins": 6}})

    await missions.ensure_for_user(1)
    await missions.report(1, 7, "proof")
    assert await missions.complete(1, 7, economy) is True

    row = await economy.profile(1)
    mission_status = await db.fetch_val("SELECT status FROM user_missions WHERE user_id = 1 AND mission_id = 7")
    assert row["taurons"] == 4
    assert row["taurcoins"] == 6
    assert mission_status == "completed"
    assert await missions.active_missions(1) == []
    await db.close()


@pytest.mark.asyncio
async def test_mission_complete_is_idempotent_after_completion(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1)
    economy = EconomyService(db)
    missions = MissionService(db)
    missions.save({"7": {"name": "Test", "description": "Proof", "reward_taurons": 4, "reward_taurcoins": 6}})

    await missions.ensure_for_user(1)
    await missions.report(1, 7, "proof")
    assert await missions.complete(1, 7, economy) is True
    assert await missions.complete(1, 7, economy) is False

    row = await economy.profile(1)
    assert row["taurons"] == 4
    assert row["taurcoins"] == 6
    assert await missions.active_missions(1) == []
    await db.close()


@pytest.mark.asyncio
async def test_mission_complete_requires_reported_status(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1)
    economy = EconomyService(db)
    missions = MissionService(db)
    missions.save({"7": {"name": "Test", "description": "Proof", "reward_taurons": 4, "reward_taurcoins": 6}})

    await missions.ensure_for_user(1)
    assert await missions.complete(1, 7, economy) is False

    row = await economy.profile(1)
    assert row["taurons"] == 0
    assert row["taurcoins"] == 0
    assert [row["mission_id"] for row in await missions.active_missions(1)] == [7]
    await db.close()


def test_apply_mission_import_accepts_numbered_json_dict():
    payload = {
        "1": {
            "name": "Сержант",
            "description": "Стать комиссаром и найти мафию",
            "reward_taurcoins": 0,
            "reward_taurons": 2,
        },
        "2": {
            "name": "Бариста",
            "description": "Исцелить 2-х за игру",
            "reward_taurcoins": 0,
            "reward_taurons": 2,
        },
    }

    imported = apply_mission_import({}, payload)

    assert imported["1"]["name"] == "Сержант"
    assert imported["2"]["reward_taurons"] == 2


def test_mission_report_text_uses_mission_name_instead_of_id():
    text = format_mission_report_text("🧸", 457430106, 6, {"name": "Сержант"}, None)

    assert "Задание: <b>Сержант</b> (<code>6</code>)" in text
    assert "Описание: Без описания" in text


def test_mission_album_report_data_keeps_all_media_items():
    messages = [
        SimpleNamespace(photo=[SimpleNamespace(file_id="photo-1")], document=None, video=None, text=None, caption="proof"),
        SimpleNamespace(photo=[SimpleNamespace(file_id="photo-2")], document=None, video=None, text=None, caption=None),
    ]

    data = json.loads(proof_report_data(cast(Any, messages)))

    assert data["kind"] == "album"
    assert [item["file_id"] for item in data["items"]] == ["photo-1", "photo-2"]
    assert proof_text(cast(Any, messages)) == "proof"


def test_id_command_helpers_match_epidemic_style():
    assert extract_user_identifier(".ид @velunae") == "@velunae"
    assert extract_user_identifier("/id tg://openmessage?user_id=457430106") == "457430106"
    text = user_info_text(
        user_id=457430106,
        full_name="шома",
        username="shominaaa",
        is_admin=False,
        taurons=44,
        taurcoins=13,
    )

    assert "Информация о пользователе" in text
    assert "Имя: шома" in text
    assert "ID: <code>457430106</code>" in text
    assert "Юзернейм: @shominaaa" in text
    assert "Админ: Нет" in text
    assert "Баланс Taurons: <code>44</code>" in text
    assert "Баланс Taurcoins: <code>13</code>" in text


def test_broadcast_summary_includes_error_reasons_and_examples():
    text = format_broadcast_summary(
        delivered=200,
        failed=2,
        skipped=1,
        reasons={"chat_not_found": 2, "invalid_user_id": 1},
        examples={"chat_not_found": [111, 222], "invalid_user_id": [-100]},
    )

    assert "Доставлено: <code>200</code>" in text
    assert "<code>chat_not_found</code>: <code>2</code>" in text
    assert "<code>111, 222</code>" in text


def test_action_log_formatters_match_requested_topics():
    admin = SimpleNamespace(id=1792913275, full_name="Ü", username="yaosseef")
    target = {"telegram_id": 803090264, "full_name": "𝐀𝐦𝐢𝐲𝐬𝐡𝐤𝐚", "username": "amiyshka"}
    receiver = {"telegram_id": 8403355074, "full_name": "Alona", "username": "I_snagovskyaya"}

    admin_log = format_admin_action_log(admin, target, "Выдача Taurons", "1Т")
    transfer_log = format_player_transfer_log(target, receiver, "TC", 1)
    reward_log = format_game_reward_log(admin, [target, receiver], "TC", 2, "победителям")

    assert "Админ: 1792913275 (Ü (@yaosseef))" in admin_log
    assert "Пользователь: 803090264 (𝐀𝐦𝐢𝐲𝐬𝐡𝐤𝐚 (@amiyshka))" in admin_log
    assert "Действие: Выдача Taurons" in admin_log
    assert "Пользователь 1: 803090264 (𝐀𝐦𝐢𝐲𝐬𝐡𝐤𝐚 (@amiyshka))" in transfer_log
    assert "Действие: Передача Taurcoins" in transfer_log
    assert "Детали: 1TC" in transfer_log
    assert 'tg://openmessage?user_id=1792913275' in reward_log
    assert "выдал(а) по 2 ТС 🌟 победителям (2 чел.)" in reward_log
    assert 'tg://openmessage?user_id=803090264' in reward_log


def utf16_offset(text: str, marker: str) -> int:
    return len(text[: text.index(marker)].encode("utf-16-le")) // 2


def test_extract_game_user_ids_reads_mentions_and_limits_winners_section():
    text = (
        "🏆 Игра окончена!\n\n"
        "Победители:\n"
        "Анна — 🎙 Журналист\n"
        "Yulia👄 — 💃🏼 Любовница\n\n"
        "Другие игроки:\n"
        "Sasha — 🧨 Террорист\n"
        "Visible 457430106 — 👮🏼 Сержант"
    )
    message = SimpleNamespace(
        text=text,
        caption=None,
        entities=[
            SimpleNamespace(offset=utf16_offset(text, "Анна"), length=4, user=SimpleNamespace(id=111111111), url=None),
            SimpleNamespace(offset=utf16_offset(text, "Yulia"), length=5, user=None, url="tg://user?id=222222222"),
            SimpleNamespace(offset=utf16_offset(text, "Sasha"), length=5, user=None, url="tg://openmessage?user_id=333333333"),
        ],
        caption_entities=None,
    )

    assert extract_game_user_ids(cast(Any, message), "победителям") == [111111111, 222222222]
    assert extract_game_user_ids(cast(Any, message), "участникам") == [111111111, 222222222, 333333333, 457430106]


@pytest.mark.asyncio
async def test_shop_bonus_types_are_dynamic_not_roulette_defaults(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    shop = ShopService(db)

    assert await shop.list_bonus_types() == []
    bonus = await shop.create_bonus_type("Преф-1", "Преф на 1 день", 5)

    items = await shop.list_bonus_types()
    assert [(item.id, item.name, item.description, item.price) for item in items] == [
        (bonus.id, "Преф-1", "Преф на 1 день", 5)
    ]
    assert not any(item.name in {"Рулетка", "Telegram Premium", "ТГ NFT"} for item in items)
    await db.close()


@pytest.mark.asyncio
async def test_roulette_info_contains_expandable_prize_list_and_special_chances():
    text = roulette_info_text()

    assert "<blockquote expandable>" in text
    assert "Стоимость прокрута: <b>5 T</b>" in text
    assert "<b>АТ-1</b> — 9.7%" in text
    assert "<b>Telegram Premium</b> — 1%" in text
    assert "<b>ТГ NFT</b> — 2%" in text
    assert "каждый 50-й общий прокрут" in text


@pytest.mark.asyncio
async def test_roulette_spin_grants_regular_prize(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=5)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))

    result = await roulette.spin(1, economy)

    assert result.spin_number == 1
    assert result.prize.code == "anti_target_1"
    assert result.guaranteed is False
    prize_count = await db.fetch_val(
        "SELECT count FROM user_prizes WHERE user_id = 1 AND prize_code = 'anti_target_1'"
    )
    assert prize_count == 1
    assert await db.fetch_val("SELECT taurons FROM users WHERE telegram_id = 1") == 0
    await db.close()


def test_roulette_admin_notification_uses_user_mention():
    result = SimpleNamespace(
        spin_number=10,
        prize=SimpleNamespace(code="anti_target_1", name="АТ-1"),
        guaranteed=False,
    )

    text = format_admin_spin_result(format_user_mention(1224362805, "Scrooge"), result)

    assert 'Пользователь: <a href="tg://user?id=1224362805">Scrooge</a>' in text
    assert "Пользователь: <code>1224362805</code>" not in text
    assert "Прокрут: <code>10</code>" in text


def test_roulette_user_notification_hides_spin_number():
    result = SimpleNamespace(
        spin_number=41,
        prize=SimpleNamespace(code="remove_warn", name="Снять варн", description="Удалить одно предупреждение у пользователя"),
        guaranteed=False,
    )

    text = format_spin_result(result)

    assert "Прокрут #41" not in text
    assert "Вы выиграли: <b>Снять варн</b>" in text


@pytest.mark.asyncio
async def test_roulette_spin_grants_taurons_cashback(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=5)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.30))

    result = await roulette.spin(1, economy)

    assert result.prize.code == "taurons_cashback"
    assert await db.fetch_val("SELECT taurons FROM users WHERE telegram_id = 1") == 3
    assert await db.fetch_val("SELECT COUNT(*) FROM user_prizes WHERE user_id = 1") == 0
    await db.close()


@pytest.mark.asyncio
async def test_roulette_spin_requires_5_taurons_and_does_not_create_spin(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=4)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))

    with pytest.raises(EconomyError, match="Недостаточно Taurons"):
        await roulette.spin(1, economy)

    assert await db.fetch_val("SELECT taurons FROM users WHERE telegram_id = 1") == 4
    assert await db.fetch_val("SELECT COUNT(*) FROM roulette_spins") == 0
    assert await db.fetch_val("SELECT COUNT(*) FROM user_prizes WHERE user_id = 1") == 0
    await db.close()


@pytest.mark.asyncio
async def test_roulette_every_50th_spin_is_guaranteed_unique_nft(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=245)
    await seed(db, 2, taurons=5)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))
    for _ in range(49):
        await roulette.spin(1, economy)

    result = await roulette.spin(2, economy)

    assert result.spin_number == 50
    assert result.prize.code == "tg_nft"
    assert result.guaranteed is True
    row = await db.fetch_one("SELECT user_id, prize_code, prize_name, guaranteed FROM roulette_spins WHERE id = 50")
    assert row is not None
    assert dict(row) == {"user_id": 2, "prize_code": "tg_nft_50", "prize_name": "ТГ NFT #50", "guaranteed": 1}
    assert await db.fetch_val("SELECT count FROM user_prizes WHERE user_id = 2 AND prize_code = 'tg_nft_50'") == 1
    await db.close()


@pytest.mark.asyncio
async def test_roulette_spins_are_global_across_players(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=10)
    await seed(db, 2, taurons=10)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))

    first = await roulette.spin(1, economy)
    second = await roulette.spin(2, economy)

    assert first.spin_number == 1
    assert second.spin_number == 2
    assert await roulette.total_spins() == 2
    assert await roulette.player_spins_count(1) == 1
    assert await roulette.player_spins_count(2) == 1
    await db.close()


@pytest.mark.asyncio
async def test_roulette_reset_all_spins_resets_global_counter(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=20)
    await seed(db, 2, taurons=20)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))
    await roulette.spin(1, economy)
    await roulette.spin(1, economy)
    await roulette.spin(2, economy)

    deleted = await roulette.reset_all_spins()
    result = await roulette.spin(2, economy)

    assert deleted == 3
    assert result.spin_number == 1
    assert await roulette.total_spins() == 1
    assert await roulette.player_spins_count(1) == 0
    assert await roulette.player_spins_count(2) == 1
    await db.close()


@pytest.mark.asyncio
async def test_reset_spins_admin_command_is_global_without_target(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 42, username="target")
    roulette = RouletteService(db)

    assert await roulette.reset_all_spins() == 0
    await db.close()
