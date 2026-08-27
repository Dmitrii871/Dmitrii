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


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> list[float]:
    """Средний истинный диапазон по Уайлдеру — мера волатильности.

    Нужен и сам по себе (ширина стопа), и как знаменатель в ADX.
    """
    if len(closes) < period + 1 or len(highs) != len(closes) or len(lows) != len(closes):
        return []
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    if len(trs) < period:
        return []
    acc = sum(trs[:period]) / period
    out = [acc]
    for tr in trs[period:]:
        acc = (acc * (period - 1) + tr) / period
        out.append(acc)
    return out


def adx(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> tuple[list[float], list[float], list[float]]:
    """ADX и направленные индикаторы: (ADX, +DI, -DI).

    ADX меряет СИЛУ тренда, а не его направление. Именно это нам и нужно:
    ниже 20 рынок в боковике и работает возврат к среднему, выше 25 идёт
    тренд и работает движение по нему. Прогон одного режима всегда даёт
    убыток в той половине времени, когда режим не тот.
    """
    n = len(closes)
    if n < 2 * period + 1 or len(highs) != n or len(lows) != n:
        return [], [], []

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))

    def wilder(values: list[float]) -> list[float]:
        acc = sum(values[:period])
        out = [acc]
        for v in values[period:]:
            acc = acc - acc / period + v
            out.append(acc)
        return out

    tr_s, pdm_s, mdm_s = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    plus_di, minus_di, dx = [], [], []
    for i in range(len(tr_s)):
        if tr_s[i] <= 0:
            plus_di.append(0.0); minus_di.append(0.0); dx.append(0.0)
            continue
        p_di = 100.0 * pdm_s[i] / tr_s[i]
        m_di = 100.0 * mdm_s[i] / tr_s[i]
        plus_di.append(p_di)
        minus_di.append(m_di)
        total = p_di + m_di
        dx.append(100.0 * abs(p_di - m_di) / total if total > 0 else 0.0)

    if len(dx) < period:
        return [], plus_di, minus_di
    acc = sum(dx[:period]) / period
    adx_out = [acc]
    for v in dx[period:]:
        acc = (acc * (period - 1) + v) / period
        adx_out.append(acc)
    return adx_out, plus_di, minus_di
