#!/usr/bin/env python3
"""Сетка Фибоначчи по двум экстремумам — и обратная задача.

Прямая: даны минимум и максимум -> все уровни отката.
Обратная: даны два подписанных уровня с чужого графика -> восстановить,
от каких экстремумов построена сетка. Нужна, чтобы проверить чужую
разметку, а не принимать её на веру.

    python tools/fib.py --low 41127 --high 125873 --price 78000
    python tools/fib.py --solve 0.382=73500 0.618=93500
"""
from __future__ import annotations

import argparse
import sys

RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def grid(low: float, high: float) -> list[tuple[float, float]]:
    rng = high - low
    return [(r, low + r * rng) for r in RATIOS]


def solve(a_ratio: float, a_price: float, b_ratio: float, b_price: float) -> tuple[float, float]:
    """Восстановить low/high по двум подписанным уровням."""
    if a_ratio == b_ratio:
        raise SystemExit("нужны два РАЗНЫХ коэффициента")
    rng = (b_price - a_price) / (b_ratio - a_ratio)
    low = a_price - a_ratio * rng
    return low, low + rng


def main() -> int:
    ap = argparse.ArgumentParser(description="Сетка Фибоначчи и восстановление чужой разметки")
    ap.add_argument("--low", type=float)
    ap.add_argument("--high", type=float)
    ap.add_argument("--price", type=float, help="текущая цена — покажет её положение в сетке")
    ap.add_argument("--solve", nargs=2, metavar="RATIO=PRICE",
                    help="восстановить сетку по двум уровням, напр. 0.382=73500 0.618=93500")
    args = ap.parse_args()

    if args.solve:
        try:
            (ar, ap_), (br, bp) = [(float(x.split("=")[0]), float(x.split("=")[1]))
                                   for x in args.solve]
        except (ValueError, IndexError):
            raise SystemExit("формат: --solve 0.382=73500 0.618=93500")
        low, high = solve(ar, ap_, br, bp)
        print(f"\nСетка восстановлена: низ {low:,.0f} | верх {high:,.0f} "
              f"| диапазон {high-low:,.0f}\n")
    elif args.low is not None and args.high is not None:
        low, high = args.low, args.high
        if high <= low:
            raise SystemExit("--high должен быть больше --low")
        print()
    else:
        raise SystemExit("укажите --low и --high либо --solve")

    for r, price in grid(low, high):
        mark = ""
        if args.price and abs(args.price - price) / args.price < 0.01:
            mark = "  <- текущая цена в этой зоне"
        print(f"  {r:.3f}  {price:>12,.0f}{mark}")

    if args.price:
        pos = (args.price - low) / (high - low)
        print(f"\n  Текущая {args.price:,.0f} = откат {pos:.3f}")
        ahead = [(r, p) for r, p in grid(low, high) if p > args.price]
        below = [(r, p) for r, p in grid(low, high) if p < args.price]
        if ahead:
            r, p = ahead[0]
            print(f"  Ближайшее сопротивление: {p:,.0f} (фибо {r:.3f}), "
                  f"{(p/args.price-1)*100:+.1f}%")
        if below:
            r, p = below[-1]
            print(f"  Ближайшая поддержка:     {p:,.0f} (фибо {r:.3f}), "
                  f"{(p/args.price-1)*100:+.1f}%")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
