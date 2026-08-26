#!/usr/bin/env python3
"""Разбор одного инструмента: ровный ли доход или он из всплесков.

Медиана и доля положительных выплат не различают два очень разных случая:
ровный ручеёк и несколько редких всплесков. Для повторяемости это
решающая разница, поэтому здесь считается КОНЦЕНТРАЦИЯ дохода.

    python tools/funding_detail.py --symbol TACUSDT --capital 500
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SPOT_TAKER, PERP_TAKER = 0.0010, 0.00055


def concentration(rates: list[float]) -> dict:
    """Какая доля дохода пришла из верхних 10% и 25% выплат.

    Ровный доход даёт около 10% и 25% соответственно. Если верхняя
    десятая часть выплат принесла больше половины — это всплески,
    и рассчитывать на их повторение нельзя.
    """
    pos = sorted((r for r in rates if r > 0), reverse=True)
    total = sum(pos)
    if not pos or total <= 0:
        return {"top10": 0.0, "top25": 0.0}
    n10 = max(1, len(pos) // 10)
    n25 = max(1, len(pos) // 4)
    return {"top10": sum(pos[:n10]) / total, "top25": sum(pos[:n25]) / total}


def main() -> int:
    ap = argparse.ArgumentParser(description="Разбор фандинга по одному инструменту")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--periods", type=int, default=200)
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    leg = args.capital / 2

    inst = http.get_instruments_info(category="linear", symbol=args.symbol)["result"]["list"][0]
    interval_h = int(inst.get("fundingInterval") or 480) / 60
    hist = http.get_funding_rate_history(
        category="linear", symbol=args.symbol, limit=min(args.periods, 200))["result"]["list"]
    rates = [float(r["fundingRate"]) for r in hist]
    if not rates:
        print("Нет истории фандинга.")
        return 1

    per_year = 8760 / interval_h
    days = len(rates) * interval_h / 24
    fees = leg * (SPOT_TAKER + PERP_TAKER) * 2
    gross = sum(rates) * leg
    con = concentration(rates)

    # Спред обеих ног — это реальная цена входа сверх комиссии
    legs = {}
    for cat in ("linear", "spot"):
        try:
            t = http.get_tickers(category=cat, symbol=args.symbol)["result"]["list"][0]
            bid, ask = float(t["bid1Price"]), float(t["ask1Price"])
            legs[cat] = (ask - bid) / ((ask + bid) / 2) * 10_000
        except Exception:  # noqa: BLE001
            legs[cat] = None

    q = statistics.quantiles(rates, n=10)
    print("\n" + "=" * 66)
    print(f"  {args.symbol} — {len(rates)} выплат, интервал {interval_h:g} ч, {days:.0f} дней")
    print("=" * 66)
    print(f"  Медианная ставка   {statistics.median(rates)*100:>9.4f}%  "
          f"({statistics.median(rates)*per_year*100:>6.1f}% годовых)")
    print(f"  Средняя ставка     {statistics.mean(rates)*100:>9.4f}%  "
          f"({statistics.mean(rates)*per_year*100:>6.1f}% годовых)")
    print(f"  10-й перцентиль    {q[0]*100:>9.4f}%")
    print(f"  90-й перцентиль    {q[-1]*100:>9.4f}%")
    print(f"  Максимум           {max(rates)*100:>9.4f}%")
    print(f"  Минимум            {min(rates)*100:>9.4f}%")
    print("  " + "-" * 62)
    print(f"  РОВНЫЙ ЛИ ДОХОД")
    print(f"  Верхние 10% выплат дали {con['top10']:>5.0%} всего дохода  (ровно — около 10%)")
    print(f"  Верхние 25% выплат дали {con['top25']:>5.0%} всего дохода  (ровно — около 25%)")
    print("  " + "-" * 62)
    print(f"  Валовый доход за период  {gross:>8.2f}$ на ноге {leg:.0f}$")
    print(f"  Комиссия цикла           {fees:>8.2f}$")
    print(f"  Чистыми                  {gross-fees:>8.2f}$  "
          f"({(gross-fees)/days*30:>6.2f}$ в месяц)")
    print("  " + "-" * 62)
    for cat, name in (("linear", "бессрочный"), ("spot", "спот      ")):
        v = legs.get(cat)
        print(f"  Спред {name}  {'нет данных' if v is None else f'{v:>8.2f} bp'}")
    total_spread = sum(v for v in legs.values() if v)
    if total_spread:
        print(f"  Спред обеих ног суммарно {total_spread:>6.2f} bp = "
              f"{leg*total_spread/10_000:.2f}$ сверх комиссии")
    print("=" * 66)

    print("\n  ВЫВОД")
    if con["top10"] > 0.5:
        print(f"  ! {con['top10']:.0%} дохода пришло из верхних 10% выплат — это ВСПЛЕСКИ.")
        print("    Повторение зависит от того, случится ли снова такой же перекос")
        print("    рынка. Планировать на этом доход нельзя.")
    elif con["top10"] > 0.25:
        print(f"  ~ {con['top10']:.0%} дохода из верхних 10% выплат — доход неровный,")
        print("    но и не полностью держится на редких событиях.")
    else:
        print(f"  Доход ровный: верхние 10% выплат дали {con['top10']:.0%}.")
        print("    Такой поток повторяется предсказуемее всплесков.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
