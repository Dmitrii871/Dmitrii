#!/usr/bin/env python3
"""Все закрытые сделки счёта с биржи: и бота, и ручные.

Тянет closed-pnl по деривативам за последние N дней (по умолчанию 7),
печатает каждую сделку и сводку: винрейт, итог, разбивка по символам.
Биржа — единственный честный источник: цены с проскальзыванием,
итог с комиссиями.

Запуск:  ./.venv/bin/python3 tools/mytrades.py [дней]
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7
    load_dotenv()
    key, sec = os.getenv("BYBIT_API_KEY", ""), os.getenv("BYBIT_API_SECRET", "")
    if not key:
        print("Нет ключей в .env — сначала ./setkeys.sh")
        return 1

    from pybit.unified_trading import HTTP
    http = HTTP(testnet=False, api_key=key, api_secret=sec, recv_window=10_000)

    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows: list[dict] = []
    cursor = ""
    while True:
        kw = dict(category="linear", startTime=start, endTime=end, limit=100)
        if cursor:
            kw["cursor"] = cursor
        res = http.get_closed_pnl(**kw)
        if res.get("retCode") != 0:
            print("Ошибка биржи:", res.get("retMsg"))
            return 1
        result = res.get("result", {})
        rows += result.get("list") or []
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break

    if not rows:
        print(f"За последние {days} дн. закрытых сделок на деривативах нет.")
        return 0

    rows.sort(key=lambda r: int(r.get("updatedTime") or 0))
    print(f"ЗАКРЫТЫЕ СДЕЛКИ ЗА {days} ДН. (деривативы, все: бот + ручные)\n")
    print(f"{'когда (UTC)':<17}{'символ':<10}{'поз.':<6}{'вход':>10}{'выход':>10}{'итог USDT':>11}")
    total = 0.0
    wins = 0
    by_sym: Counter = Counter()
    pnl_sym: dict[str, float] = {}
    for r in rows:
        ts = datetime.fromtimestamp(int(r.get("updatedTime") or 0) / 1000,
                                    tz=timezone.utc).strftime("%d.%m %H:%M")
        sym = r.get("symbol", "?")
        side = "Long" if r.get("side") == "Sell" else "Short"
        pnl = float(r.get("closedPnl") or 0)
        total += pnl
        wins += pnl > 0
        by_sym[sym] += 1
        pnl_sym[sym] = pnl_sym.get(sym, 0.0) + pnl
        print(f"{ts:<17}{sym:<10}{side:<6}{float(r.get('avgEntryPrice') or 0):>10g}"
              f"{float(r.get('avgExitPrice') or 0):>10g}{pnl:>+11.4f}")

    n = len(rows)
    print("\n" + "=" * 64)
    print(f"ИТОГО: сделок {n} | прибыльных {wins} ({wins / n * 100:.0f}%) | "
          f"итог {total:+.4f} USDT")
    print("по символам: " + ", ".join(
        f"{s}: {by_sym[s]} шт, {pnl_sym[s]:+.3f}" for s in by_sym))
    return 0


if __name__ == "__main__":
    sys.exit(main())
