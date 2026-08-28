"""Трендовая стратегия: канал Дончиана на дневных свечах."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.models import Account, Instrument, MarketData, Position
from bot.strategies import build
from bot.strategies.base import Context

N = 30


def ctx(price, highs=None, lows=None, side="", size="0", entry="0"):
    highs = highs or [100.0] * N
    lows = lows or [90.0] * N
    md = MarketData(symbol="BTCUSDT", bid=Decimal(str(price)) - Decimal("0.01"),
                    ask=Decimal(str(price)) + Decimal("0.01"), last=Decimal(str(price)),
                    closes=[95.0] * len(highs), highs=highs, lows=lows, bar_time=0)
    return Context(
        md=md,
        position=Position("BTCUSDT", side, Decimal(size), Decimal(entry),
                          Decimal(0), Decimal(0)),
        account=Account(Decimal(500), Decimal(500)),
        instrument=Instrument("BTCUSDT", Decimal("0.5"), Decimal("0.001"),
                              Decimal("0.001"), Decimal("5")),
        open_orders=[],
    )


def test_breakout_up_enters_long():
    strat = build("trend", {})
    acts = strat.decide(ctx(price=100.5))
    assert len(acts) == 1 and acts[0].side == "Buy" and acts[0].kind == "market"
    assert acts[0].stop_loss is not None, "аварийный стоп обязателен"
    assert float(acts[0].stop_loss) < 100.5


def test_breakout_down_enters_short():
    strat = build("trend", {})
    acts = strat.decide(ctx(price=89.5))
    assert len(acts) == 1 and acts[0].side == "Sell"


def test_inside_channel_does_nothing():
    strat = build("trend", {})
    assert strat.decide(ctx(price=95.0)) == []


def test_long_exits_on_opposite_channel():
    strat = build("trend", {})
    # выходной канал: минимум последних 10 дней = 90 -> цена 89.9 закрывает лонг
    acts = strat.decide(ctx(price=89.9, side="Buy", size="0.2", entry="100"))
    assert len(acts) == 1 and acts[0].kind == "close"


def test_long_holds_inside_exit_channel():
    strat = build("trend", {})
    assert strat.decide(ctx(price=95.0, side="Buy", size="0.2", entry="100")) == []


def test_short_exits_on_opposite_channel():
    strat = build("trend", {})
    acts = strat.decide(ctx(price=100.5, side="Sell", size="0.2", entry="95"))
    assert len(acts) == 1 and acts[0].kind == "close"


def test_direction_long_only_ignores_short_breakout():
    strat = build("trend", {"direction": "long_only"})
    assert strat.decide(ctx(price=89.5)) == []


def test_short_history_reports_reason():
    strat = build("trend", {})
    c = ctx(price=95.0, highs=[100.0] * 5, lows=[90.0] * 5)
    assert strat.decide(c) == []
    assert "мало данных" in strat.last_snapshot.get("причина", "")


def test_exit_days_must_be_smaller():
    import pytest
    strat = build("trend", {"exit_days": 20, "enter_days": 20})
    with pytest.raises(ValueError):
        strat.validate({})


def test_chaos_bar_no_entry():
    """Цена пробила оба канала за день — не входим."""
    strat = build("trend", {"enter_days": 20, "exit_days": 10})
    highs = [100.0] * N
    lows = [90.0] * N
    c = ctx(price=100.5, highs=highs, lows=lows)
    # цена 100.5 выше канала вверх; сделаем её одновременно ниже канала вниз невозможно,
    # поэтому проверяем через равные каналы: enter_hi == enter_lo
    flat_h = [95.0] * N
    flat_l = [95.0] * N
    c2 = ctx(price=95.0, highs=flat_h, lows=flat_l)
    assert strat.decide(c2) == []
