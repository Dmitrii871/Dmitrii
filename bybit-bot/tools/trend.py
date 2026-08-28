#!/usr/bin/env python3
"""Следование за трендом на дневных свечах — последний неизмеренный класс.

Возврат к среднему на часовиках закрыт: край 6.7 bp против 7.5 bp
издержек, и это не чинится (живой тест + 4 варианта на истории).
Тренд на дневках — противоположная ставка: редкие сделки, удержание
неделями, комиссия ничтожна рядом с размером движения. Классическая
механика Дончиана: вход на пробое N-дневного экстремума, выход на
обратном M-дневном. Без предсказаний — чистая механика.

Честность модели:
- комиссия тейкера с обеих сторон (пробой не взять лимиткой);
- фандинг 3 bp за КАЖДЫЙ день удержания любой позиции — консервативно:
  лонги обычно платят около того, шорты то получают, то платят;
- свеча, задевшая и вход и выход, считается против нас.

Запуск:  ./.venv/bin/python3 tools/trend.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest import fetch_klines  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
FEE_BPS = 5.5              # тейкер за сторону
FUNDING_BPS_DAY = 3.0      # за день удержания, консервативно для обеих сторон
NOTIONAL = 25.0


def donchian(rows, enter_n=20, exit_n=10):
    """Сделки (сторона, дней, доходность до издержек, ts выхода, мс)."""
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    trades, side, entry, entry_i = [], None, 0.0, 0
    for i in range(enter_n, len(closes)):
        hi = max(highs[i - enter_n:i])
        lo = min(lows[i - enter_n:i])
        ex_hi = max(highs[max(0, i - exit_n):i])
        ex_lo = min(lows[max(0, i - exit_n):i])
        ts = int(rows[i][0])
        if side == "long":
            if lows[i] <= ex_lo:                      # выход по обратному экстремуму
                trades.append(("long", i - entry_i, (ex_lo - entry) / entry, ts))
                side = None
        elif side == "short":
            if highs[i] >= ex_hi:
                trades.append(("short", i - entry_i, (entry - ex_hi) / entry, ts))
                side = None
        if side is None:
            long_break = highs[i] >= hi
            short_break = lows[i] <= lo
            if long_break and short_break:            # хаос-свеча: не входим
                continue
            if long_break:
                side, entry, entry_i = "long", hi, i
            elif short_break:
                side, entry, entry_i = "short", lo, i
    return trades


def main() -> int:
    fee = FEE_BPS / 10_000
    fund = FUNDING_BPS_DAY / 10_000
    grand = {"trades": 0, "net": 0.0, "wins": 0}
    by_year: dict[int, float] = {}
    print(f"Дневные свечи, вся доступная история. Комиссия {FEE_BPS} bp/сторона, "
          f"фандинг {FUNDING_BPS_DAY} bp/день удержания\n")
    for sym in SYMBOLS:
        try:
            rows = fetch_klines(sym, "D", 3000, testnet=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: история недоступна ({exc})")
            continue
        trades = donchian(rows)
        nets = [(g - 2 * fee - d * fund) * NOTIONAL for _, d, g, _ in trades]
        wins = [n for n in nets if n > 0]
        gross_loss = abs(sum(n for n in nets if n <= 0))
        pf = sum(wins) / gross_loss if gross_loss else float("inf")
        years = len(rows) / 365
        grand["trades"] += len(nets)
        grand["net"] += sum(nets)
        grand["wins"] += len(wins)
        # максимальная просадка по кривой капитала — цена, которой куплен итог
        eq = peak = max_dd = 0.0
        streak = worst_streak = 0
        for n in nets:
            eq += n
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
            streak = streak + 1 if n <= 0 else 0
            worst_streak = max(worst_streak, streak)
        print(f"{sym:<10} {len(rows):>5} дн ({years:.1f} г) | сделок {len(nets):>3} | "
              f"винрейт {len(wins)/len(nets)*100 if nets else 0:>3.0f}% | "
              f"PF {pf:>5.2f} | итог {sum(nets):>+8.2f} USDT | "
              f"{sum(nets)/years/NOTIONAL*100 if years else 0:>+6.1f}%/год на позицию")
        print(f"{'':10} просадка {max_dd:.2f} USDT ({max_dd/NOTIONAL*100:.0f}% позиции), "
              f"худшая серия {worst_streak} убыточных подряд")
        for _, d, g, ts in trades:
            y = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).year
            by_year.setdefault(y, 0.0)
            by_year[y] += (g - 2 * fee - d * fund) * NOTIONAL
    print("\n" + "=" * 60)
    wr = grand["wins"] / grand["trades"] * 100 if grand["trades"] else 0
    print(f"ИТОГО: сделок {grand['trades']} | винрейт {wr:.0f}% | "
          f"итог {grand['net']:+.2f} USDT на {NOTIONAL:.0f} USDT позиции")
    print("\nПО ГОДАМ (все символы вместе) — жив ли край после бума 2020-21:")
    for y in sorted(by_year):
        bar = "#" * min(40, int(abs(by_year[y]) / 2))
        print(f"  {y}  {by_year[y]:>+8.2f} USDT  {bar}")
    print("\nУ тренда винрейт всегда низкий (30-45%): много мелких стопов,")
    print("редкие крупные выигрыши. Смотреть надо на итог и PF, не на винрейт.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
