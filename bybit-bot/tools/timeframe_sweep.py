#!/usr/bin/env python3
"""Сравнение таймфреймов: где движение лучше всего перекрывает комиссию.

Считает по каждому таймфрейму отношение "типичная свеча / стоимость круга",
число сделок в месяц и месячные расходы на комиссию. Оптимальный таймфрейм —
тот, где запас над комиссией достаточен, а сделок хватает для статистики.

С реальными данными:
    python tools/timeframe_sweep.py --symbol ETHUSDT
Офлайн (волатильность масштабируется как корень из времени):
    python tools/timeframe_sweep.py --demo --base-vol-bps 3.5
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest import fetch_klines, synthetic_klines  # noqa: E402
from tools.timeframe_check import analyse  # noqa: E402

# (метка, минут в баре, кулдаун в барах для этого ТФ)
FRAMES = [
    ("3m", 3, 10), ("5m", 5, 8), ("15m", 15, 4),
    ("30m", 30, 2), ("1h", 60, 2), ("4h", 240, 1), ("1d", 1440, 1),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Какой таймфрейм оптимален после комиссий")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--base-vol-bps", type=float, default=3.5,
                    help="волатильность 3-минутного бара для --demo")
    ap.add_argument("--maker-bps", type=float, default=2.0)
    ap.add_argument("--taker-bps", type=float, default=5.5)
    ap.add_argument("--notional", type=float, default=25.0)
    args = ap.parse_args()

    rows_out = []
    for label, minutes, cooldown in FRAMES:
        if args.demo:
            # волатильность растёт как корень из горизонта
            vol = args.base_vol_bps * math.sqrt(minutes / 3.0)
            rows = synthetic_klines(args.bars, seed=11, vol_bps=vol)
        else:
            interval = str(minutes) if minutes < 1440 else "D"
            try:
                rows = fetch_klines(args.symbol, interval, args.bars, False)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {label}: {exc}")
                continue

        r = analyse(rows, args.maker_bps, args.taker_bps)
        bars_month = 43_200 / minutes
        # грубая оценка: вход не чаще, чем раз в cooldown баров, и не на каждом сигнале
        trades_month = bars_month / max(cooldown, 1) * 0.06
        fee_month_maker = trades_month * (args.maker_bps + args.taker_bps) / 10_000 * args.notional
        fee_month_taker = trades_month * 2 * args.taker_bps / 10_000 * args.notional

        rows_out.append({
            "tf": label,
            "range_bps": r["median_range_bps"],
            "vs_taker": r["range_vs_taker"],
            "vs_maker": r["range_vs_maker"],
            "trades_month": trades_month,
            "fee_taker": fee_month_taker,
            "fee_maker": fee_month_maker,
        })

    src = f"демо (база {args.base_vol_bps} bp на 3m)" if args.demo else f"{args.symbol} с биржи"
    print("\n" + "=" * 78)
    print(f"  СРАВНЕНИЕ ТАЙМФРЕЙМОВ — {src} | нотионал {args.notional:.0f} USDT")
    print("=" * 78)
    print(f"  {'ТФ':<5} {'свеча':>8} {'/тейкер':>9} {'/мейкер':>9} "
          f"{'сделок/мес':>11} {'комис.тейк':>11} {'комис.мейк':>11}")
    print("  " + "-" * 74)
    for d in rows_out:
        flag = "  " if d["vs_taker"] >= 3 else ("!!" if d["vs_taker"] < 1 else " ~")
        print(f"{flag}{d['tf']:<5} {d['range_bps']:>7.1f}bp {d['vs_taker']:>8.2f}x "
              f"{d['vs_maker']:>8.2f}x {d['trades_month']:>10.0f} "
              f"{d['fee_taker']:>10.2f}$ {d['fee_maker']:>10.2f}$")
    print("=" * 78)
    print("  !! запас меньше 1x — торговать тейкером нельзя")
    print("   ~ запас 1-3x — на грани, только мейкером и со строгим отбором")
    print("     без метки — запас 3x и выше, рабочий диапазон\n")

    # Два кандидата: лучший при входе тейкером и лучший при входе мейкером.
    # Критерий один: запас над комиссией >= 3x И хотя бы 15 сделок в месяц,
    # иначе статистику придётся копить годами.
    MIN_RATIO, MIN_TRADES = 3.0, 15.0

    def pick(key: str):
        ok = [d for d in rows_out if d[key] >= MIN_RATIO and d["trades_month"] >= MIN_TRADES]
        # из подходящих берём с наибольшим запасом
        return max(ok, key=lambda d: d[key]) if ok else None

    taker_best, maker_best = pick("vs_taker"), pick("vs_maker")

    print("  РЕКОМЕНДАЦИЯ")
    print("  " + "-" * 74)
    if maker_best:
        print(f"  Вход мейкером (entry_type: post_only) -> {maker_best['tf']}")
        print(f"    запас над комиссией {maker_best['vs_maker']:.1f}x, "
              f"{maker_best['trades_month']:.0f} сделок в месяц, "
              f"комиссия ~{maker_best['fee_maker']:.2f}$ в месяц")
    else:
        print("  Мейкером: ни один таймфрейм не даёт запас 3x при 15+ сделках.")

    if taker_best:
        print(f"  Вход тейкером (entry_type: market)    -> {taker_best['tf']}")
        print(f"    запас над комиссией {taker_best['vs_taker']:.1f}x, "
              f"{taker_best['trades_month']:.0f} сделок в месяц, "
              f"комиссия ~{taker_best['fee_taker']:.2f}$ в месяц")
    else:
        print("  Тейкером: ни один таймфрейм не проходит оба порога одновременно.")
        near = max(rows_out, key=lambda d: d["vs_taker"])
        print(f"    Ближе всего {near['tf']} ({near['vs_taker']:.1f}x), "
              f"но там всего {near['trades_month']:.0f} сделок в месяц —")
        print("    на такой выборке оценить стратегию можно будет через год.")

    print()
    print("  Почему не короче: комиссия фиксирована, а движение падает как корень")
    print("  из времени. Ниже 15m запас исчезает — сигналы могут быть верными,")
    print("  но прибыль уходит бирже.")
    print("  Почему не длиннее: сделок становится слишком мало, чтобы отличить")
    print("  работающую стратегию от везения, плюс растёт стоимость фандинга.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
