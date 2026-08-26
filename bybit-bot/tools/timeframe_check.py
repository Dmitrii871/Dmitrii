#!/usr/bin/env python3
"""Проверка пригодности таймфрейма: хватает ли движения, чтобы окупить комиссию.

Главный вопрос коротких таймфреймов — не "есть ли сигнал", а "больше ли
типичное движение, чем стоимость входа и выхода". Если нет, стратегия
убыточна независимо от качества сигналов.

    python tools/timeframe_check.py --csv ETHUSDT_3.csv
    python tools/timeframe_check.py --demo --vol-bps 5
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest import csv_klines, fetch_klines, synthetic_klines  # noqa: E402


def atr(rows: list[list[str]], period: int = 14) -> list[float]:
    """Average True Range в абсолютных единицах цены."""
    trs: list[float] = []
    for i in range(1, len(rows)):
        h, l = float(rows[i][2]), float(rows[i][3])
        pc = float(rows[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return []
    out, acc = [], sum(trs[:period]) / period
    out.append(acc)
    for tr in trs[period:]:
        acc = (acc * (period - 1) + tr) / period
        out.append(acc)
    return out


def analyse(rows: list[list[str]], maker_bps: float, taker_bps: float) -> dict:
    closes = [float(r[4]) for r in rows]
    ranges_bps = [
        (float(r[2]) - float(r[3])) / float(r[4]) * 10_000 for r in rows if float(r[4]) > 0
    ]
    moves_bps = [
        abs(closes[i] - closes[i - 1]) / closes[i - 1] * 10_000 for i in range(1, len(closes))
    ]
    a = atr(rows)
    atr_bps = a[-1] / closes[-1] * 10_000 if a else 0.0

    rt_taker = 2 * taker_bps
    rt_maker = 2 * maker_bps

    # сколько баров подряд в одну сторону нужно, чтобы перекрыть комиссию
    med_move = statistics.median(moves_bps) if moves_bps else 0.0
    bars_taker = rt_taker / med_move if med_move else float("inf")
    bars_maker = rt_maker / med_move if med_move else float("inf")

    return {
        "bars": len(rows),
        "median_range_bps": statistics.median(ranges_bps) if ranges_bps else 0.0,
        "mean_range_bps": statistics.mean(ranges_bps) if ranges_bps else 0.0,
        "median_move_bps": med_move,
        "p90_move_bps": statistics.quantiles(moves_bps, n=10)[-1] if len(moves_bps) > 10 else 0.0,
        "atr_bps": atr_bps,
        "rt_taker_bps": rt_taker,
        "rt_maker_bps": rt_maker,
        "bars_to_cover_taker": bars_taker,
        "bars_to_cover_maker": bars_maker,
        "range_vs_taker": (statistics.median(ranges_bps) / rt_taker) if ranges_bps else 0.0,
        "range_vs_maker": (statistics.median(ranges_bps) / rt_maker) if ranges_bps else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Пригоден ли таймфрейм для торговли после комиссий")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="3")
    ap.add_argument("--bars", type=int, default=2000)
    ap.add_argument("--csv")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--vol-bps", type=float, default=6.0, help="волатильность бара для --demo")
    ap.add_argument("--maker-bps", type=float, default=2.0)
    ap.add_argument("--taker-bps", type=float, default=5.5)
    args = ap.parse_args()

    if args.demo:
        rows = synthetic_klines(args.bars, vol_bps=args.vol_bps)
        src = f"демо, волатильность {args.vol_bps} bp/бар"
    elif args.csv:
        rows = csv_klines(args.csv, args.bars)
        src = args.csv
    else:
        rows = fetch_klines(args.symbol, args.interval, args.bars, False)
        src = f"{args.symbol} {args.interval}m с биржи"

    r = analyse(rows, args.maker_bps, args.taker_bps)

    print("\n" + "=" * 62)
    print(f"  ПРИГОДНОСТЬ ТАЙМФРЕЙМА — {src}")
    print("=" * 62)
    print(f"  Баров в выборке          {r['bars']:>10}")
    print(f"  Медианный диапазон бара  {r['median_range_bps']:>10.2f} bp  ({r['median_range_bps']/100:.4f}%)")
    print(f"  ATR(14)                  {r['atr_bps']:>10.2f} bp")
    print(f"  Медианное движение бара  {r['median_move_bps']:>10.2f} bp")
    print(f"  Движение 90-й перцентиль {r['p90_move_bps']:>10.2f} bp")
    print("  " + "-" * 58)
    print(f"  Круг ТЕЙКЕРОМ            {r['rt_taker_bps']:>10.2f} bp")
    print(f"  Круг МЕЙКЕРОМ            {r['rt_maker_bps']:>10.2f} bp")
    print("  " + "-" * 58)
    print(f"  Диапазон бара / комиссия тейкера   {r['range_vs_taker']:>6.2f}x")
    print(f"  Диапазон бара / комиссия мейкера   {r['range_vs_maker']:>6.2f}x")
    print(f"  Баров подряд, чтобы окупить тейкера {r['bars_to_cover_taker']:>5.1f}")
    print(f"  Баров подряд, чтобы окупить мейкера {r['bars_to_cover_maker']:>5.1f}")
    print("=" * 62)

    verdict_taker = r["range_vs_taker"]
    if verdict_taker < 1.0:
        print("  ТЕЙКЕРОМ ТОРГОВАТЬ НЕЛЬЗЯ: типичная свеча меньше комиссии за круг.")
        print("  Вход рыночным ордером здесь убыточен по построению.")
    elif verdict_taker < 3.0:
        print("  Тейкером — на грани: комиссия съедает большую часть движения.")
    else:
        print("  Тейкером допустимо: движение заметно превышает комиссию.")

    if r["range_vs_maker"] >= 3.0:
        print("  МЕЙКЕРОМ работать можно: используйте PostOnly-вход (entry_type: post_only).")
    elif r["range_vs_maker"] >= 1.0:
        print("  Мейкером — на грани, нужен строгий отбор сигналов.")
    else:
        print("  Мейкером тоже нет запаса. Возьмите таймфрейм выше.")

    print(f"\n  Вывод: тейк-профит должен быть минимум {r['rt_taker_bps']*3:.0f} bp "
          f"({r['rt_taker_bps']*3/100:.3f}%) при входе тейкером")
    print(f"         или минимум {r['rt_maker_bps']*3:.0f} bp "
          f"({r['rt_maker_bps']*3/100:.3f}%) при входе мейкером.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
