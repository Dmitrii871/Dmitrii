"""Тесты индикаторов и логики стратегии. Запуск: python -m pytest tests -q"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import bollinger_pct_b, ema, macd, rsi
from bot.strategies.signal import SignalStrategy


def test_ema_constant_series_equals_constant():
    assert all(math.isclose(v, 7.0) for v in ema([7.0] * 50, 10))


def test_ema_alignment_length():
    assert len(ema(list(range(50)), 10)) == 50 - 10 + 1


def test_rsi_monotonic_up_is_100():
    assert math.isclose(rsi([float(i) for i in range(1, 60)], 14)[-1], 100.0)


def test_rsi_monotonic_down_is_0():
    assert math.isclose(rsi([float(i) for i in range(60, 1, -1)], 14)[-1], 0.0)


def test_rsi_right_aligned():
    closes = [float(i) for i in range(1, 60)]
    assert len(rsi(closes, 14)) == len(closes) - 14


def test_rsi_flat_series_is_neutral():
    assert math.isclose(rsi([100.0] * 40, 14)[-1], 50.0)


def test_macd_histogram_is_line_minus_signal():
    closes = [100 + 10 * math.sin(i / 5) for i in range(200)]
    line, sig, hist = macd(closes)
    offset = len(line) - len(sig)
    assert all(math.isclose(hist[i], line[i + offset] - sig[i]) for i in range(len(sig)))


def test_macd_all_series_end_on_same_bar():
    closes = [100 + 10 * math.sin(i / 5) for i in range(200)]
    line, sig, hist = macd(closes)
    assert len(sig) == len(hist)
    assert len(line) >= len(sig)


def test_pct_b_bounds_on_flat_market():
    assert math.isclose(bollinger_pct_b([50.0] * 40)[-1], 0.5)


def test_pct_b_above_one_when_breaking_upper_band():
    closes = [100.0] * 30 + [130.0]
    assert bollinger_pct_b(closes, 20, 2.0)[-1] > 1.0


def test_short_series_returns_empty():
    assert ema([1.0, 2.0], 10) == []
    assert rsi([1.0, 2.0], 14) == []
    assert bollinger_pct_b([1.0, 2.0], 20) == []


def test_strategy_rejects_take_profit_below_fees():
    strat = SignalStrategy({"take_profit_pct": 0.05, "stop_loss_pct": 0.5})
    try:
        strat.validate({"taker_bps": 5.5})
    except ValueError as exc:
        assert "комисси" in str(exc)
    else:
        raise AssertionError("должна была быть ошибка: тейк меньше комиссии")


def test_strategy_accepts_sane_take_profit():
    SignalStrategy({"take_profit_pct": 1.2, "stop_loss_pct": 0.8}).validate({"taker_bps": 5.5})


def test_oversold_market_votes_long():
    # резкое падение -> низкий RSI и низкий %B -> голоса в лонг
    closes = [100.0] * 60 + [100 - i * 1.5 for i in range(1, 41)]
    longs, shorts, snap = SignalStrategy({}).votes(closes)
    assert longs >= 2, f"ожидали голоса в лонг, получили {snap}"
    assert shorts == 0


def test_overbought_market_votes_short():
    closes = [100.0] * 60 + [100 + i * 1.5 for i in range(1, 41)]
    longs, shorts, snap = SignalStrategy({}).votes(closes)
    assert shorts >= 2, f"ожидали голоса в шорт, получили {snap}"
    assert longs == 0


def test_maker_rejects_spread_below_fee():
    from bot.strategies.maker import MakerStrategy
    try:
        MakerStrategy({"spread_bps": 1.0}).validate({"maker_bps": 2.0})
    except ValueError as exc:
        assert "комиссию мейкера" in str(exc)
    else:
        raise AssertionError("должна была быть ошибка: спред меньше комиссии")
