#!/usr/bin/env python3
"""Сканер инструментов: где вообще возможно преимущество при вашей комиссии.

Обе классические стратегии проваливаются на ETHUSDT не из-за логики,
а из-за инструмента: у самой ликвидной пары самый узкий спред. Этот
сканер обходит ВСЕ бессрочные контракты Bybit и меряет три вещи:

  СПРЕД     — шире ли он комиссии мейкера в обе стороны (маркет-мейкинг);
  ФАНДИНГ   — сколько платят за удержание позиции (доход без предсказания);
  ДОСТУПНОСТЬ — влезает ли минимальный лот в ваш депозит.

Данные публичные, ключи не нужны:
    python tools/scan.py --capital 40 --leverage 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ниже этого оборота инструмент неликвиден: выйти из позиции будет нечем,
# а цену легко двигают отдельные участники.
MIN_TURNOVER_24H = 5_000_000.0


def fetch_all() -> tuple[list[dict], dict[str, dict]]:
    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    tickers = http.get_tickers(category="linear")["result"]["list"]
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
    return tickers, instruments


def analyse(tickers, instruments, capital: float, leverage: int,
            maker_bps: float, taker_bps: float) -> list[dict]:
    budget = capital * leverage * 0.6      # 60% плеча — запас на просадку
    rows = []
    for t in tickers:
        sym = t["symbol"]
        inst = instruments.get(sym)
        if not inst or not sym.endswith("USDT"):
            continue
        try:
            bid, ask = float(t["bid1Price"]), float(t["ask1Price"])
            last = float(t["lastPrice"])
            turnover = float(t.get("turnover24h", 0))
            funding = float(t.get("fundingRate", 0) or 0)
            min_qty = float(inst["lotSizeFilter"]["minOrderQty"])
            # Интервал фандинга в минутах. У Bybit он РАЗНЫЙ у разных
            # контрактов: 480 (8ч), 240 (4ч), у волатильных альтов часто 60 (1ч).
            # Без него годовые считаются с ошибкой в разы.
            interval_min = int(inst.get("fundingInterval") or 480)
        except (KeyError, TypeError, ValueError):
            continue
        if interval_min <= 0:
            continue
        if bid <= 0 or ask <= bid or last <= 0 or turnover < MIN_TURNOVER_24H:
            continue

        min_notional = min_qty * last
        if min_notional > budget:
            continue                        # минимальный лот не влезает в депозит

        spread_bps = (ask - bid) / ((ask + bid) / 2) * 10_000
        # Знак > 0 значит лонги платят шортам.
        periods_per_year = 525_600 / interval_min
        funding_apr = funding * periods_per_year * 100
        # Сколько это в деньгах на вашем депозите за месяц, если держать
        # позицию на весь бюджет. Годовые проценты на малом счёте вводят
        # в заблуждение — рубли нагляднее.
        month_usdt = budget * funding * (periods_per_year / 12)
        rows.append({
            "symbol": sym,
            "price": last,
            "spread_bps": spread_bps,
            "mm_edge_bps": spread_bps - 2 * maker_bps,
            "funding_apr": funding_apr,
            "funding_interval_h": interval_min / 60,
            "month_usdt": month_usdt,
            "turnover_m": turnover / 1e6,
            "min_notional": min_notional,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Где возможно преимущество при вашей комиссии")
    ap.add_argument("--capital", type=float, default=40.0, help="депозит, USDT")
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument("--maker-bps", type=float, default=2.0)
    ap.add_argument("--taker-bps", type=float, default=5.5)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    print("Загружаю все бессрочные контракты Bybit...")
    tickers, instruments = fetch_all()
    rows = analyse(tickers, instruments, args.capital, args.leverage,
                   args.maker_bps, args.taker_bps)
    print(f"Подходят по ликвидности и размеру лота: {len(rows)} из {len(tickers)}\n")

    rt = 2 * args.maker_bps
    # ---------------------------------------------------------- маркет-мейкинг
    mm = sorted([r for r in rows if r["mm_edge_bps"] > 0],
                key=lambda r: r["mm_edge_bps"], reverse=True)[:args.top]
    print("=" * 78)
    print(f"  МАРКЕТ-МЕЙКИНГ: спред минус круг по комиссии ({rt:.1f} bp)")
    print("=" * 78)
    if not mm:
        print("  Ни одного инструмента со спредом шире комиссии.")
        print("  При ставке мейкера %.2f%% маркет-мейкинг недоступен." % (args.maker_bps / 100))
    else:
        print(f"  {'символ':<16} {'спред':>9} {'запас':>9} {'оборот 24ч':>12} {'мин.лот':>10}")
        print("  " + "-" * 74)
        for r in mm:
            print(f"  {r['symbol']:<16} {r['spread_bps']:>8.2f}b {r['mm_edge_bps']:>8.2f}b "
                  f"{r['turnover_m']:>10.0f}M$ {r['min_notional']:>9.2f}$")

    # --------------------------------------------------------------- фандинг
    print("\n" + "=" * 78)
    print("  ФАНДИНГ: доход за удержание позиции, без предсказания направления")
    print("=" * 78)
    fund = sorted(rows, key=lambda r: abs(r["funding_apr"]), reverse=True)[:args.top]
    print(f"  {'символ':<14} {'годовых':>9} {'интв':>5} {'кто платит':>13} "
          f"{'$/мес':>8} {'спред':>8} {'оборот':>9}")
    print("  " + "-" * 74)
    for r in fund:
        who = "лонги->шортам" if r["funding_apr"] > 0 else "шорты->лонгам"
        flag = "  !" if abs(r["funding_apr"]) > 200 else ""
        print(f"  {r['symbol']:<14} {r['funding_apr']:>8.0f}% {r['funding_interval_h']:>4.0f}ч "
              f"{who:>13} {r['month_usdt']:>7.2f}$ {r['spread_bps']:>7.2f}b "
              f"{r['turnover_m']:>7.0f}M${flag}")

    extreme = [r for r in fund if abs(r["funding_apr"]) > 200]
    if extreme:
        print(f"\n  ! {len(extreme)} инструментов со ставкой выше 200% годовых.")
        print("    Такая ставка — это не возможность, а сигнал бедствия: рынок")
        print("    перекошен настолько, что одна сторона платит любую цену за выход.")
        print("    Обычно это делистинг, обвал или потеря привязки. Цена уходит")
        print("    против вас быстрее, чем фандинг успевает заплатить.")

    print("\n" + "=" * 78)
    print("  КАК ЧИТАТЬ")
    print("=" * 78)
    print("  Широкий спред почти всегда означает низкую ликвидность: выйти из")
    print("  позиции будет дороже, чем показывает спред, а цену легче двигают.")
    print("  Прежде чем брать инструмент из верхней таблицы, проверьте глубину")
    print("  книги и прогоните на нём tools/timeframe_sweep.py.")
    print()
    print("  Столбец $/мес — сколько принесёт фандинг на ВАШ депозит за месяц,")
    print("  если держать позицию на весь бюджет. Годовые проценты на малом")
    print("  счёте вводят в заблуждение: 100%% годовых на 40 USDT это 3 доллара")
    print("  в месяц, и это ДО убытка от движения цены.")
    print()
    print("  Высокий фандинг означает перекошенный рынок. Ставка может")
    print("  развернуться, а цена уйти против вас сильнее, чем фандинг заплатит.")
    print("  Забирать фандинг безопасно только дельта-нейтрально: шорт в бессрочном")
    print("  контракте против покупки того же актива на споте. Одним лишь")
    print("  бессрочным контрактом это направленная ставка, а не арбитраж.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
