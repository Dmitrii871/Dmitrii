"""Индикаторы на чистом Python — без numpy и TA-Lib.

Формулы совпадают с TradingView: RSI по Уайлдеру, MACD на EMA,
Bollinger %B по популяционному (не выборочному) стандартному отклонению.

Каждая функция возвращает серию, выровненную по ПРАВОМУ краю:
результат[-1] всегда относится к closes[-1].
"""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float]:
    """Экспоненциальная скользящая. Затравка — SMA первых `period` значений."""
    if period <= 0:
        raise ValueError("period должен быть > 0")
    if len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    acc = sum(values[:period]) / period
    out = [acc]
    for v in values[period:]:
        acc = v * k + acc * (1.0 - k)
        out.append(acc)
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    """RSI по методу Уайлдера (сглаживание 1/period)."""
    if len(closes) < period + 1:
        return []
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(delta if delta > 0 else 0.0)
        losses.append(-delta if delta < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _value(g: float, l: float) -> float:
        if l == 0.0:
            return 100.0 if g > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out = [_value(avg_gain, avg_loss)]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out.append(_value(avg_gain, avg_loss))
    return out


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]]:
    """Возвращает (линия MACD, сигнальная линия, гистограмма), выровненные по правому краю."""
    if fast >= slow:
        raise ValueError("fast должен быть меньше slow")
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if not ema_fast or not ema_slow:
        return [], [], []
    offset = len(ema_fast) - len(ema_slow)
    line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig = ema(line, signal)
    if not sig:
        return line, [], []
    offset2 = len(line) - len(sig)
    hist = [line[i + offset2] - sig[i] for i in range(len(sig))]
    return line, sig, hist


def bollinger_pct_b(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> list[float]:
    """%B: 0 — на нижней полосе, 1 — на верхней. Выход за [0,1] — пробой полосы."""
    if len(closes) < period:
        return []
    out: list[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = var ** 0.5
        upper, lower = mean + mult * sd, mean - mult * sd
        out.append(0.5 if upper == lower else (closes[i] - lower) / (upper - lower))
    return out
