#!/usr/bin/env python3
"""Подбор параметров с честной проверкой вне выборки.

Любой перебор по истории найдёт настройку, которая красиво заработала
в прошлом. Это ничего не значит: при 60 вариантах несколько окажутся
прибыльными случайно. Поэтому история делится на две части:

  ОБУЧЕНИЕ (первые 60%) — здесь идёт перебор;
  ПРОВЕРКА (последние 40%) — эти данные оптимизатор не видел.

Доверять можно только настройке, которая заработала на ОБЕИХ частях.

    python tools/optimize.py --symbol ETHUSDT --interval 60 --bars 10000
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest import backtest, csv_klines, fetch_klines  # noqa: E402

TP_GRID = (0.8, 1.0, 1.3, 1.6, 2.0, 2.5)
SL_GRID = (0.5, 0.7, 0.9, 1.2, 1.5)
CONFLUENCE_GRID = (2, 3)


def run(rows, tp: float, sl: float, conf: int, mode: str,
        fee: float, notional: float) -> dict:
    return backtest(
        rows,
        {"take_profit_pct": tp, "stop_loss_pct": sl, "min_confluence": conf,
         "order_notional_usdt": notional, "mode": mode},
        fee, notional,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Подбор параметров с проверкой вне выборки")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="60")
    ap.add_argument("--bars", type=int, default=10_000)
    ap.add_argument("--csv")
    ap.add_argument("--mode", choices=["reversion", "momentum", "both"],
                    default="reversion",
                    help="reversion — покупать перепроданность; momentum — покупать силу")
    ap.add_argument("--notional", type=float, default=25.0)
    ap.add_argument("--fee-bps", type=float, default=3.75,
                    help="комиссия за сторону: 3.75 = вход мейкером, выход тейкером")
    ap.add_argument("--min-trades", type=int, default=30,
                    help="меньше сделок — статистика недостоверна, вариант отбрасывается")
    ap.add_argument("--split", type=float, default=0.6, help="доля истории на обучение")
    args = ap.parse_args()

    rows = csv_klines(args.csv, args.bars) if args.csv else \
        fetch_klines(args.symbol, args.interval, args.bars, False)
    cut = int(len(rows) * args.split)
    train, test = rows[:cut], rows[cut:]
    print(f"\nЗагружено {len(rows)} свечей {args.symbol} {args.interval}m")
    print(f"Обучение: {len(train)} свечей | Проверка: {len(test)} свечей (оптимизатор их не видит)")

    results = []
    for tp in TP_GRID:
        for sl in SL_GRID:
            if tp <= sl:
                continue                      # тейк не больше стопа — бессмысленно
            for conf in CONFLUENCE_GRID:
              for mode in (("reversion", "momentum") if args.mode == "both" else (args.mode,)):
                tr = run(train, tp, sl, conf, mode, args.fee_bps, args.notional)
                if tr["trades"] < args.min_trades:
                    continue
                te = run(test, tp, sl, conf, mode, args.fee_bps, args.notional)
                results.append({
                    "tp": tp, "sl": sl, "conf": conf, "mode": mode,
                    "train_pf": tr["profit_factor"], "train_net": tr["net_usdt"],
                    "train_trades": tr["trades"],
                    "test_pf": te["profit_factor"], "test_net": te["net_usdt"],
                    "test_trades": te["trades"], "test_dd": te["max_drawdown"],
                })

    if not results:
        print(f"\nНи один вариант не дал {args.min_trades}+ сделок. Возьмите больше истории.")
        return 1

    results.sort(key=lambda d: d["train_pf"], reverse=True)

    print("\n" + "=" * 78)
    print("  ЛУЧШИЕ 10 ПО ОБУЧЕНИЮ — и что они дали на непросмотренных данных")
    print("=" * 78)
    print(f"  {'режим':>9} {'TP%':>5} {'SL%':>5} {'сгл':>4} | {'ОБУЧ pf':>8} {'итог$':>8} {'сдел':>5} "
          f"| {'ПРОВ pf':>8} {'итог$':>8} {'сдел':>5}")
    print("  " + "-" * 76)
    for d in results[:10]:
        holds = "" if d["test_net"] > 0 else "  <- убыток"
        print(f"  {d['mode']:>9} {d['tp']:>5.1f} {d['sl']:>5.1f} {d['conf']:>4} "
              f"| {d['train_pf']:>8.2f} {d['train_net']:>8.2f} {d['train_trades']:>5} "
              f"| {d['test_pf']:>8.2f} {d['test_net']:>8.2f} {d['test_trades']:>5}{holds}")

    survivors = [d for d in results if d["train_net"] > 0 and d["test_net"] > 0
                 and d["test_pf"] >= 1.2 and d["test_trades"] >= args.min_trades // 2]

    print("\n" + "=" * 78)
    total = len(results)
    prof_test = len([d for d in results if d["test_net"] > 0])
    print(f"  Проверено вариантов: {total}")
    print(f"  Прибыльных на обучении:  {len([d for d in results if d['train_net'] > 0])}")
    print(f"  Прибыльных на проверке:  {prof_test} ({prof_test/total:.0%})")
    print(f"  Прошли оба этапа:        {len(survivors)}")
    print("=" * 78)

    if prof_test / total < 0.3:
        print("\n  ! Меньше трети вариантов прибыльны вне выборки.")
        print("    Это признак того, что у стратегии нет устойчивого преимущества")
        print("    на этом рынке, а отдельные удачные строки выше — случайность.")

    if not survivors:
        print("\n  ВЫВОД: ни одна настройка не подтвердилась вне выборки.")
        print("  Запускать бота на реальные деньги нельзя. Варианты: другой символ,")
        print("  другой таймфрейм, другая стратегия — но не эта на этих данных.\n")
        return 2

    survivors.sort(key=lambda d: d["test_pf"], reverse=True)
    best = survivors[0]
    med_test_pf = statistics.median(d["test_pf"] for d in survivors)
    print(f"\n  ВЫВОД: {len(survivors)} настроек подтвердились вне выборки, "
          f"медиана профит-фактора {med_test_pf:.2f}")
    print(f"\n  Рекомендуемая настройка для config.yaml:")
    print(f"      mode: {best['mode']}")
    print(f"      take_profit_pct: {best['tp']}")
    print(f"      stop_loss_pct: {best['sl']}")
    print(f"      min_confluence: {best['conf']}")
    print(f"\n  На непросмотренных данных: профит-фактор {best['test_pf']:.2f}, "
          f"{best['test_trades']} сделок, итог {best['test_net']:+.2f} USDT, "
          f"просадка {best['test_dd']:.2f} USDT")
    print("\n  Даже подтверждённая настройка не гарантирует прибыль: рынок меняется,")
    print("  а проверка была всего на одном отрезке. Прогоните на testnet.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
