#!/usr/bin/env python3
"""Наблюдение за живой книгой заявок: устойчив ли спред и чем он оплачен.

Сканер даёт спред одним снимком. Этого мало: спред скачет, а маркет-мейкинг
живёт на его СРЕДНЕМ значении. Хуже того, широкий спред — плата за
неблагоприятный отбор: бид исполняется, когда цена падает, аск — когда
растёт. Этот монитор измеряет и то, и другое на реальных данных.

Ключей не требует, ничем не торгует, только смотрит:
    python tools/book_monitor.py --symbol AGIUSDT --minutes 15
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def walk_book(levels: list, mid: float, size_usdt: float) -> float | None:
    """Во сколько базисных пунктов обойдётся рыночный выход на size_usdt.

    Идём по уровням стакана, пока не наберём нужный объём, и считаем
    средневзвешенную цену исполнения против середины рынка. Это и есть
    настоящая стоимость закрытия позиции на тонкой книге.
    """
    need, cost, filled = size_usdt, 0.0, 0.0
    for price_s, qty_s in levels:
        price, qty = float(price_s), float(qty_s)
        avail = price * qty
        take = min(need, avail)
        cost += take * price
        filled += take
        need -= take
        if need <= 0:
            break
    if need > 0 or filled <= 0:
        return None                     # книги не хватило на такой объём
    avg = cost / filled
    return abs(avg - mid) / mid * 10_000


def sample(http, symbol: str, exit_size: float) -> dict | None:
    try:
        ob = http.get_orderbook(category="linear", symbol=symbol, limit=50)["result"]
        bids, asks = ob.get("b", []), ob.get("a", [])
        if not bids or not asks:
            return None
        bid, bid_sz = float(bids[0][0]), float(bids[0][1])
        ask, ask_sz = float(asks[0][0]), float(asks[0][1])
        if ask <= bid:
            return None
        mid = (bid + ask) / 2
        return {
            "t": time.time(), "mid": mid,
            "spread_bps": (ask - bid) / mid * 10_000,
            "depth_usdt": min(bid * bid_sz, ask * ask_sz),
            "exit_bps": walk_book(bids, mid, exit_size),
        }
    except Exception:  # noqa: BLE001 — пропуск одного замера не должен рушить наблюдение
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Устойчивость спреда и риск маркет-мейкинга")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--every", type=float, default=3.0, help="период опроса, секунды")
    ap.add_argument("--maker-bps", type=float, default=2.0)
    ap.add_argument("--notional", type=float, default=25.0)
    ap.add_argument("--save", help="сохранить сырые замеры в CSV, чтобы анализ")
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    deadline = time.time() + args.minutes * 60
    rows: list[dict] = []
    print(f"Наблюдаю {args.symbol} {args.minutes:g} мин, опрос раз в {args.every:g} с. "
          f"Прервать — Ctrl+C\n")
    try:
        while time.time() < deadline:
            s = sample(http, args.symbol, args.notional)
            if s:
                rows.append(s)
                if len(rows) % 10 == 0:
                    print(f"\r  замеров {len(rows)}, спред сейчас {s['spread_bps']:.2f} bp",
                          end="", flush=True)
            time.sleep(args.every)
    except KeyboardInterrupt:
        print("\n  прервано")

    print()
    if len(rows) < 20:
        print("Слишком мало замеров для выводов. Увеличьте --minutes.")
        return 1

    spreads = [r["spread_bps"] for r in rows]
    depths = [r["depth_usdt"] for r in rows]
    mids = [r["mid"] for r in rows]
    rt = 2 * args.maker_bps

    half_spread = statistics.median(spreads) / 2

    if args.save:
        import csv as _csv
        with open(args.save, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["t", "mid", "spread_bps", "depth_usdt"])
            for r in rows:
                w.writerow([r["t"], r["mid"], r["spread_bps"], r["depth_usdt"]])
        print(f"  сырые замеры сохранены в {args.save}\n")

    q = statistics.quantiles(spreads, n=10)
    print("=" * 66)
    print(f"  {args.symbol} — {len(rows)} замеров за {args.minutes:g} мин")
    print("=" * 66)
    print(f"  Спред медиана        {statistics.median(spreads):>8.2f} bp")
    print(f"  Спред 10-й перцентиль{q[0]:>8.2f} bp   (узкие моменты)")
    print(f"  Спред 90-й перцентиль{q[-1]:>8.2f} bp   (широкие моменты)")
    wide = sum(1 for s in spreads if s > rt) / len(spreads)
    print(f"  Время, когда спред шире издержек: {wide:>5.1%}")
    print("  " + "-" * 62)
    dq = statistics.quantiles(depths, n=4) if len(depths) > 4 else [0, 0, 0]
    print(f"  Глубина у края: медиана {statistics.median(depths):>7.1f} USDT, "
          f"нижняя четверть {dq[0]:>6.1f} USDT")
    thin = sum(1 for d in depths if d < args.notional) / len(depths)
    print(f"  Время, когда на краю меньше вашего ордера ({args.notional:g}$): {thin:>5.1%}")
    print(f"  РЕКОМЕНДУЕМЫЙ РАЗМЕР ОРДЕРА: {dq[0]:>6.1f} USDT")
    print("    (нижняя четверть глубины — ваша заявка не будет доминировать в книге)")

    exits = [r["exit_bps"] for r in rows if r.get("exit_bps") is not None]
    if exits:
        med_exit = statistics.median(exits)
        print(f"  Проскальзывание рыночного выхода на {args.notional:g}$: "
              f"{med_exit:>6.2f} bp (медиана)")
        print(f"    против вашего заработка {half_spread:.2f} bp с одной стороны")
    else:
        med_exit = None
        print(f"  ! Книги не хватает, чтобы закрыть {args.notional:g}$ рыночным ордером")
    print("  " + "-" * 62)
    print(f"  Ваш заработок с одной стороны (половина спреда): {half_spread:>6.2f} bp")
    print()
    print("  СНОС ЦЕНЫ ПО ГОРИЗОНТАМ УДЕРЖАНИЯ")
    print("  Инвентарь держится не секунды, а минуты. Смотрим, насколько")
    print("  типично уходит цена за это время против вашей половины спреда.")
    print(f"  {'горизонт':>10} {'медиана':>9} {'90-й проц':>11} {'хуже вашего края':>18}")

    # Сколько замеров укладывается в горизонт при текущем периоде опроса
    adverse_by_h: dict[int, float] = {}
    for horizon_s in (args.every, 30, 60, 300):
        lag = max(1, int(round(horizon_s / args.every)))
        if lag >= len(mids):
            continue
        moves = [abs(mids[i + lag] - mids[i]) / mids[i] * 10_000
                 for i in range(len(mids) - lag)]
        if not moves:
            continue
        worse = sum(1 for m in moves if m > half_spread) / len(moves)
        adverse_by_h[int(horizon_s)] = worse
        p90 = statistics.quantiles(moves, n=10)[-1] if len(moves) > 10 else max(moves)
        print(f"  {horizon_s:>8.0f} с {statistics.median(moves):>8.2f}b "
              f"{p90:>10.2f}b {worse:>17.0%}")
    # Для вывода берём горизонт в минуту: типичное время удержания инвентаря
    adverse = adverse_by_h.get(60, max(adverse_by_h.values()) if adverse_by_h else 0.0)
    print("=" * 66)

    print("\n  ВЫВОД")
    ok = True
    if wide < 0.8:
        print(f"  ! Спред шире издержек лишь {wide:.0%} времени — котировать выгодно")
        print("    далеко не всегда, реальный запас меньше, чем показал сканер.")
        ok = False
    if thin > 0.3:
        print(f"  ! В {thin:.0%} случаев на краю книги меньше вашего ордера ({args.notional:g}$).")
        print("    Проблема не во входе — первым в очереди стоять как раз хорошо.")
        print("    Проблема в выходе: закрывать позицию придётся через несколько")
        print("    уровней стакана, и против такой крупной заявки торгуют избирательно.")
        print(f"    Уменьшите размер до {dq[0]:.1f} USDT и перезапустите монитор.")
        ok = False
    if exits and med_exit > half_spread:
        print(f"  ! Выход стоит {med_exit:.2f} bp при заработке {half_spread:.2f} bp с одной")
        print("    стороны. Одна вынужденная ликвидация съест несколько удачных кругов.")
        ok = False
    if adverse > 0.5:
        print(f"  ! За минуту удержания цена уходит дальше вашего края в {adverse:.0%} случаев.")
        print("    Это неблагоприятный отбор: бид исполняется на падении, аск на росте,")
        print("    и движение против вас съедает заработок со спреда.")
        ok = False
    if ok:
        print("  Спред устойчив, глубина достаточна, движение умереннее половины спреда.")
        print("  Инструмент пригоден для проверки маркет-мейкинга НА TESTNET.")
    print("\n  Это наблюдение, а не доказательство прибыльности. Оно показывает")
    print("  только то, что условия не исключают маркет-мейкинг сразу.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
