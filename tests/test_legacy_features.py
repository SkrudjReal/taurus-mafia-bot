from types import SimpleNamespace

import pytest

from taurus_mafia_bot.config import Settings
from taurus_mafia_bot.db import Database
from taurus_mafia_bot.routers.admin import parse_reset_spins_target
from taurus_mafia_bot.routers.roulette import format_admin_spin_result, format_user_mention
from taurus_mafia_bot.routers.shop import notify_admins
from taurus_mafia_bot.routers.start import format_taurons_top
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
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))


async def seed(db: Database, user_id: int, taurons: int = 0, taurcoins: int = 0, username: str = "user", full_name: str | None = None) -> None:
    await db.execute(
        """
        INSERT INTO users (telegram_id, full_name, username, taurons, taurcoins, is_admin)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (user_id, full_name or f"User {user_id}", username, taurons, taurcoins),
    )


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
        " 1. папочка кенни — <b>2</b>\n"
        " 2. ಣsasha — <b>2</b>\n"
        " 3. IliaSlime — <b>1</b>\n"
        "\n"
        "Всего тауронов: <b>5</b>"
    )
    await db.close()


@pytest.mark.asyncio
async def test_top_taurons_escapes_names(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=1, full_name="<bad&name>")
    economy = EconomyService(db)

    text = format_taurons_top(await economy.top_taurons(), await economy.total_taurons())

    assert "&lt;bad&amp;name&gt;" in text
    await db.close()


def test_settings_default_log_topics_match_legacy_bot_py() -> None:
    settings = Settings(BOT_TOKEN="123456:REALISH", OWNER_ID=1, ADMIN_IDS="1")

    assert settings.log_chat_id == -1003333957923
    assert settings.log_thread_id == 2215
    assert settings.roulette_log_chat_id == -1003333957923
    assert settings.roulette_log_thread_id == 18657


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
    callback = SimpleNamespace(bot=bot, from_user=SimpleNamespace(id=1))
    settings = Settings(
        BOT_TOKEN="123456:REALISH",
        OWNER_ID=1,
        ADMIN_IDS="1",
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
    assert "каждый 50-й прокрут" in text


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
    await seed(db, 1, taurons=250)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))
    for _ in range(49):
        await roulette.spin(1, economy)

    result = await roulette.spin(1, economy)

    assert result.spin_number == 50
    assert result.prize.code == "tg_nft"
    assert result.guaranteed is True
    row = await db.fetch_one("SELECT prize_code, prize_name, guaranteed FROM roulette_spins WHERE id = 50")
    assert dict(row) == {"prize_code": "tg_nft_50", "prize_name": "ТГ NFT #50", "guaranteed": 1}
    assert await db.fetch_val("SELECT count FROM user_prizes WHERE user_id = 1 AND prize_code = 'tg_nft_50'") == 1
    await db.close()


@pytest.mark.asyncio
async def test_roulette_reset_player_spins_only_for_target_user(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 1, taurons=20)
    await seed(db, 2, taurons=20)
    economy = EconomyService(db)
    roulette = RouletteService(db, rng=FixedRng(0.0))
    await roulette.spin(1, economy)
    await roulette.spin(1, economy)
    await roulette.spin(2, economy)

    deleted = await roulette.reset_player_spins(1)
    result = await roulette.spin(1, economy)

    assert deleted == 2
    assert result.spin_number == 1
    assert await roulette.player_spins_count(1) == 1
    assert await roulette.player_spins_count(2) == 1
    await db.close()


@pytest.mark.asyncio
async def test_reset_spins_admin_command_parses_username_target(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.migrate()
    await seed(db, 42, username="target")
    economy = EconomyService(db)
    message = SimpleNamespace(text="-прокрут @target", reply_to_message=None)

    assert await parse_reset_spins_target(message, economy) == 42
    await db.close()
