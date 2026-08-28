#!/usr/bin/env python3
"""План Б на истории: сравнение четырёх вариантов стратегии.

Живой тест показал два провала: тесные стопы выбивает шумом (все выходы —
стоп-лосс) и возврат к среднему торгуется даже в устойчивом тренде.
Здесь оба лекарства проверяются на одной и той же истории:

  базовый      — как в живом тесте сейчас
  atr-стоп     — стоп на расстоянии ATR×2 вместо фиксированного процента
  фильтр ADX   — режим auto: в тренде возврат не торгуется
  оба вместе   — кандидат на новую конфигурацию

Запуск:  ./.venv/bin/python3 tools/planb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest import backtest, fetch_klines  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
BARS = 2000            # ~83 дня часовых свечей: захватывает и тренд, и боковик
FEE_BPS = 5.5          # тейкер: консервативно, как в живом тесте фактически
NOTIONAL = 25.0

BASE = {
    "take_profit_pct": 1.2,
    "stop_loss_pct": 0.8,
    "min_confluence": 2,
    "order_notional_usdt": NOTIONAL,
    "mode": "reversion",
}
VARIANTS = [
    ("базовый",    {}),
    ("atr-стоп",   {"stop_loss_atr_mult": 2.0}),
    ("фильтр ADX", {"mode": "auto"}),
    ("оба вместе", {"stop_loss_atr_mult": 2.0, "mode": "auto"}),
]


def main() -> int:
    totals = {name: {"trades": 0, "net": 0.0, "wins": 0.0} for name, _ in VARIANTS}
    print(f"История: {BARS} часовых свечей на символ, комиссия {FEE_BPS} bp за сторону\n")
    for sym in SYMBOLS:
        try:
            rows = fetch_klines(sym, "60", BARS, testnet=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: не удалось получить историю ({exc}) — пропуск")
            continue
        print(f"{sym} ({len(rows)} свечей)")
        print(f"  {'вариант':<12}{'сделок':>7}{'винрейт':>9}{'итог USDT':>11}{'просадка':>10}")
        for name, over in VARIANTS:
            r = backtest(rows, {**BASE, **over}, FEE_BPS, NOTIONAL)
            totals[name]["trades"] += r["trades"]
            totals[name]["net"] += r["net_usdt"]
            totals[name]["wins"] += r["win_rate"] * r["trades"]
            print(f"  {name:<12}{r['trades']:>7}{r['win_rate']*100:>8.0f}%"
                  f"{r['net_usdt']:>11.2f}{r['max_drawdown']:>10.2f}")
        print()

    print("=" * 52)
    print(f"  {'ИТОГО':<12}{'сделок':>7}{'винрейт':>9}{'итог USDT':>11}")
    for name, _ in VARIANTS:
        t = totals[name]
        wr = t["wins"] / t["trades"] * 100 if t["trades"] else 0
        print(f"  {name:<12}{t['trades']:>7}{wr:>8.0f}%{t['net']:>11.2f}")
    print("\nЧитать так: если «оба вместе» в плюсе на большинстве символов —")
    print("есть смысл перезапускать бумажный тест с этой конфигурацией.")
    print("Если все варианты в минусе — честный вывод: стратегию закрываем.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
