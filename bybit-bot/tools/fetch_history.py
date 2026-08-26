#!/usr/bin/env python3
"""Выгрузка исторических свечей в CSV — для офлайн-бэктеста.

    python tools/fetch_history.py --symbol ETHUSDT --interval 30 --bars 20000
    python tools/backtest.py --csv ETHUSDT_30.csv --bars 20000
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Скачать свечи Bybit в CSV")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="30")
    ap.add_argument("--bars", type=int, default=20_000)
    ap.add_argument("--out")
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)          # публичные данные, ключи не нужны
    rows: list[list[str]] = []
    end = None
    while len(rows) < args.bars:
        params = dict(category="linear", symbol=args.symbol,
                      interval=args.interval, limit=1000)
        if end is not None:
            params["end"] = end
        chunk = http.get_kline(**params)["result"]["list"]
        if not chunk:
            break
        rows.extend(chunk)
        end = int(chunk[-1][0]) - 1
        print(f"\r  загружено {len(rows)}", end="", flush=True)
        if len(chunk) < 1000:
            break

    rows = sorted(rows, key=lambda r: int(r[0]))[-args.bars:]
    out = Path(args.out or f"{args.symbol}_{args.interval}.csv")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        w.writerows(rows)
    print(f"\n{len(rows)} свечей -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
