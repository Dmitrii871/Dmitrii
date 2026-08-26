#!/usr/bin/env python3
"""Поиск инструментов для дельта-нейтрального сбора фандинга.

Схема: покупка на споте + шорт в бессрочном контракте того же размера.
Движение цены компенсируется между ногами, доход — чистая ставка фандинга.
Предсказывать направление не нужно.

Снимок ставки ничего не решает: фандинг разворачивается, и инструмент
с 60% годовых сегодня завтра может платить вам в минус. Поэтому берётся
ИСТОРИЯ и считается, сколько бы вы реально накопили.

Ключей не требует:
    python tools/funding_scan.py --capital 500 --periods 200
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Обе ноги должны быть ликвидны, иначе вход и выход съедят фандинг
MIN_TURNOVER_24H = 20_000_000.0
SPOT_TAKER = 0.0010
PERP_TAKER = 0.00055


def spot_symbols(http) -> set[str]:
    """Какие активы вообще доступны на споте — без этого ноги не собрать."""
    out: set[str] = set()
    cursor = None
    while True:
        params = {"category": "spot", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        res = http.get_instruments_info(**params)["result"]
        for it in res["list"]:
            out.add(it["symbol"])
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    return out


def funding_history(http, symbol: str, periods: int) -> list[float]:
    res = http.get_funding_rate_history(
        category="linear", symbol=symbol, limit=min(periods, 200))
    return [float(r["fundingRate"]) for r in res["result"]["list"]]


def evaluate(rates: list[float], interval_h: float, leg: float) -> dict:
    """Что бы реально принесла позиция за наблюдаемый период."""
    if not rates:
        return {}
    per_year = 8760 / interval_h
    # накопленный доход шорта: положительная ставка платит шорту
    gross = sum(rates) * leg
    hours = len(rates) * interval_h
    fees = leg * (SPOT_TAKER + PERP_TAKER) * 2      # вход и выход обеих ног
    positive = sum(1 for r in rates if r > 0) / len(rates)
    med = statistics.median(rates)
    # разворот ставки: сколько раз меняется знак
    flips = sum(1 for i in range(1, len(rates))
                if (rates[i] > 0) != (rates[i - 1] > 0)) / max(len(rates) - 1, 1)
    return {
        "periods": len(rates),
        "days": hours / 24,
        "median_apr": med * per_year * 100,
        "share_positive": positive,
        "flip_rate": flips,
        "gross_usdt": gross,
        "net_usdt": gross - fees,
        "fees_usdt": fees,
        "net_per_month": (gross - fees) / max(hours / 24, 1) * 30 if hours else 0,
        "worst_period_bps": min(rates) * 10_000,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Дельта-нейтральный сбор фандинга")
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--periods", type=int, default=200, help="сколько выплат истории брать")
    ap.add_argument("--top", type=int, default=15, help="сколько кандидатов проверять историей")
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    leg = args.capital / 2                     # половина на спот, половина на маржу шорта

    print("Загружаю контракты и спотовые пары...")
    tickers = http.get_tickers(category="linear")["result"]["list"]
    spots = spot_symbols(http)

    instruments: dict[str, dict] = {}
    cursor = None
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        res = http.get_instruments_info(**params)["result"]
        for it in res["list"]:
            instruments[it["symbol"]] = it
        cursor = res.get("nextPageCursor")
        if not cursor:
            break

    # Отбор: есть спот, достаточная ликвидность, положительный фандинг сейчас
    cands = []
    for t in tickers:
        sym = t["symbol"]
        if sym not in spots or sym not in instruments:
            continue                            # без спота дельта-нейтраль не собрать
        try:
            turnover = float(t.get("turnover24h", 0))
            funding = float(t.get("fundingRate") or 0)
            interval_h = int(instruments[sym].get("fundingInterval") or 480) / 60
        except (TypeError, ValueError):
            continue
        if turnover < MIN_TURNOVER_24H or funding <= 0:
            continue                            # шорт зарабатывает на ПОЛОЖИТЕЛЬНОЙ ставке
        cands.append((funding * (8760 / interval_h) * 100, sym, interval_h, turnover))

    cands.sort(reverse=True)
    print(f"Со спотом, ликвидные, с положительным фандингом: {len(cands)}")
    print(f"Проверяю историю по {min(args.top, len(cands))} лучшим "
          f"(нога {leg:.0f}$, комиссия цикла {leg*(SPOT_TAKER+PERP_TAKER)*2:.2f}$)\n")

    rows = []
    for apr_now, sym, interval_h, turnover in cands[:args.top]:
        try:
            rates = funding_history(http, sym, args.periods)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {sym}: {exc}")
            continue
        ev = evaluate(rates, interval_h, leg)
        if not ev:
            continue
        ev.update(symbol=sym, apr_now=apr_now, turnover_m=turnover / 1e6)
        rows.append(ev)
        time.sleep(0.15)                        # бережём лимит запросов

    if not rows:
        print("Кандидатов не осталось.")
        return 1

    rows.sort(key=lambda r: r["net_per_month"], reverse=True)
    print("=" * 78)
    print(f"  ИСТОРИЯ ФАНДИНГА — что бы реально принесло за наблюдаемый период")
    print("=" * 78)
    print(f"  {'символ':<14} {'дней':>5} {'медиана':>9} {'полож.':>7} {'разв.':>6} "
          f"{'чисто$':>8} {'$/мес':>8}")
    print("  " + "-" * 74)
    for r in rows:
        print(f"  {r['symbol']:<14} {r['days']:>5.0f} {r['median_apr']:>8.0f}% "
              f"{r['share_positive']:>6.0%} {r['flip_rate']:>5.0%} "
              f"{r['net_usdt']:>7.2f}$ {r['net_per_month']:>7.2f}$")
    print("=" * 78)
    print("  медиана — годовых по медианной ставке | полож. — доля выплат в вашу пользу")
    print("  разв. — как часто ставка меняет знак | чисто — за вычетом комиссии цикла\n")

    best = rows[0]
    print("  КАК ЧИТАТЬ")
    print("  Доля положительных ниже 80% означает, что часть времени вы ПЛАТИТЕ.")
    print("  Частые развороты знака делают доход непредсказуемым.")
    print(f"  Худшая выплата у лидера: {best['worst_period_bps']:.1f} bp против вас.\n")
    print("  ЧЕГО ЭТОТ РАСЧЁТ НЕ УЧИТЫВАЕТ")
    print("  - расхождение цен спота и бессрочного контракта (базисный риск)")
    print("  - ликвидацию шорта при резком росте, если не хватит маржи")
    print("  - делистинг инструмента, что особенно вероятно у мелких альтов")
    print("  - что прошлая ставка не обещает будущую\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
