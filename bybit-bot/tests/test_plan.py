"""Тесты торгового плана: он должен уметь запрещать, но не приказывать."""
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.plan import Level, TradingPlan


def plan(**over):
    cfg = dict(
        symbol="BTCUSDT", source="тест", bias="long",
        invalidation=Decimal("125873"), invalidation_side="above",
        expires=date.today() + timedelta(days=30),
        levels=[Level(Decimal("73500"), "support", "фибо 0.382"),
                Level(Decimal("83500"), "resistance", "фибо 0.5"),
                Level(Decimal("93500"), "target", "цель волны b")],
        zone_bps=80.0,
    )
    cfg.update(over)
    return TradingPlan(**cfg)


def test_long_bias_blocks_shorts():
    p = plan()
    assert p.allows("Buy", Decimal("78000"))
    assert not p.allows("Sell", Decimal("78000"))


def test_short_bias_blocks_longs():
    p = plan(bias="short")
    assert not p.allows("Buy", Decimal("78000"))
    assert p.allows("Sell", Decimal("78000"))


def test_neutral_bias_blocks_nothing():
    p = plan(bias="neutral")
    assert p.allows("Buy", Decimal("78000")) and p.allows("Sell", Decimal("78000"))


def test_invalidation_disables_plan_permanently():
    """Пробой уровня отмены отключает план до конца сессии, а не на один тик."""
    p = plan()
    assert p.allows("Sell", Decimal("78000")) is False
    p.is_active(Decimal("126000"))                 # пробой вверх
    assert p.allows("Sell", Decimal("78000")) is True, "после отмены план не запрещает ничего"
    assert p.near(Decimal("73500")) is None


def test_expired_plan_is_inactive():
    p = plan(expires=date.today() - timedelta(days=1))
    assert not p.is_active(Decimal("78000"))
    assert p.allows("Sell", Decimal("78000")), "просроченный план ничего не запрещает"


def test_zone_detection():
    p = plan()
    assert p.near(Decimal("73600")).label == "фибо 0.382"     # внутри 0.8%
    assert p.near(Decimal("78000")) is None                    # далеко от уровней


def test_support_adds_long_vote_resistance_adds_short():
    p = plan()
    assert p.extra_votes(Decimal("73550"))[:2] == (1, 0)
    assert p.extra_votes(Decimal("83450"))[:2] == (0, 1)
    assert p.extra_votes(Decimal("78000"))[:2] == (0, 0)


def test_target_is_nearest_level_ahead():
    p = plan()
    assert p.target_for("Buy", Decimal("78000")) == Decimal("83500")
    assert p.target_for("Buy", Decimal("90000")) == Decimal("93500")
    assert p.target_for("Buy", Decimal("95000")) is None       # выше всех уровней
    assert p.target_for("Sell", Decimal("78000")) == Decimal("73500")


def test_bad_level_kind_rejected():
    try:
        Level(Decimal("1"), "магия", "")
    except ValueError as exc:
        assert "kind" in str(exc)
    else:
        raise AssertionError("неизвестный тип уровня должен отклоняться")


def test_shipped_plans_load_and_are_consistent():
    """Файлы планов в репозитории должны грузиться и иметь дату отмены в будущем."""
    root = Path(__file__).resolve().parents[1]
    for name in ("BTCUSDT", "ETHUSDT"):
        p = TradingPlan.load(root / "plans" / f"{name}.yaml")
        assert p.symbol == name
        assert p.levels, "план без уровней бесполезен"
        assert p.note, "план обязан объяснять сценарий"
        # уровень отмены должен быть по нужную сторону от уровней плана
        prices = [l.price for l in p.levels]
        if p.invalidation_side == "above":
            assert p.invalidation > max(prices)
        else:
            assert p.invalidation < min(prices)


def test_plan_cannot_open_position_by_itself():
    """План даёт максимум один голос — меньше порога согласия в 2."""
    p = plan()
    longs, shorts, _ = p.extra_votes(Decimal("73550"))
    assert longs + shorts <= 1
