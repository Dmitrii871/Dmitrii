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


def sample(http, symbol: str) -> dict | None:
    try:
        ob = http.get_orderbook(category="linear", symbol=symbol, limit=5)["result"]
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
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    deadline = time.time() + args.minutes * 60
    rows: list[dict] = []
    print(f"Наблюдаю {args.symbol} {args.minutes:g} мин, опрос раз в {args.every:g} с. "
          f"Прервать — Ctrl+C\n")
    try:
        while time.time() < deadline:
            s = sample(http, args.symbol)
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

    # Движение середины между замерами — прокси неблагоприятного отбора.
    moves = [abs(mids[i] - mids[i - 1]) / mids[i - 1] * 10_000 for i in range(1, len(mids))]
    half_spread = statistics.median(spreads) / 2

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
    print(f"  Глубина у края книги медиана {statistics.median(depths):>8.0f} USDT")
    thin = sum(1 for d in depths if d < args.notional) / len(depths)
    print(f"  Время, когда на краю меньше вашего ордера ({args.notional:g}$): {thin:>5.1%}")
    print("  " + "-" * 62)
    print(f"  Сдвиг середины между замерами, медиана {statistics.median(moves):>7.2f} bp")
    print(f"  Половина спреда (ваш заработок с одной стороны) {half_spread:>7.2f} bp")
    adverse = sum(1 for m in moves if m > half_spread) / len(moves)
    print(f"  Доля замеров, где цена ушла дальше половины спреда: {adverse:>5.1%}")
    print("=" * 66)

    print("\n  ВЫВОД")
    ok = True
    if wide < 0.8:
        print(f"  ! Спред шире издержек лишь {wide:.0%} времени — котировать выгодно")
        print("    далеко не всегда, реальный запас меньше, чем показал сканер.")
        ok = False
    if thin > 0.3:
        print(f"  ! В {thin:.0%} случаев на краю книги меньше вашего ордера.")
        print("    Вы будете двигать цену собственной заявкой и стоять в очереди.")
        ok = False
    if adverse > 0.5:
        print(f"  ! Цена уходит дальше половины спреда в {adverse:.0%} замеров.")
        print("    Это и есть неблагоприятный отбор: заработок со спреда")
        print("    съедается движением против вас. Маркет-мейкинг здесь опасен.")
        ok = False
    if ok:
        print("  Спред устойчив, глубина достаточна, движение умереннее половины спреда.")
        print("  Инструмент пригоден для проверки маркет-мейкинга НА TESTNET.")
    print("\n  Это наблюдение, а не доказательство прибыльности. Оно показывает")
    print("  только то, что условия не исключают маркет-мейкинг сразу.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
