#!/usr/bin/env python3
"""Есть ли в признаке предсказательная сила — до того, как строить стратегию.

Строить стратегию под каждую идею и гонять бэктест долго и обманчиво:
перебор параметров рано или поздно найдёт красивую кривую даже в шуме.
Быстрее и честнее спросить иначе: коррелирует ли признак с БУДУЩЕЙ
доходностью вообще.

Мера — информационный коэффициент (IC), корреляция между значением
признака сейчас и доходностью через k баров.

Порог значимости НЕ фиксирован: уровень шума равен 1/sqrt(n), поэтому
при 500 наблюдениях случайные ряды дают |IC| около 0.045 сами по себе.
Сравнивать надо с этим уровнем, а не с числом из учебника, иначе шум
будет объявлен признаком.

Данные публичные, ключи не нужны:
    python tools/feature_scan.py --symbol ETHUSDT --interval 60 --bars 1000
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import adx, bollinger_pct_b, macd, rsi  # noqa: E402


def spearman(x: list[float], y: list[float]) -> float:
    """Ранговая корреляция: устойчива к выбросам, которых в рынке хватает."""
    n = len(x)
    if n < 10:
        return 0.0

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def align_right(series: list[float], n: int) -> list[float | None]:
    """Дополнить серию слева None до длины n — индикаторы короче истории."""
    return [None] * (n - len(series)) + list(series)


def fetch_extras(http, symbol: str, interval: str, bars: int, times: list[int]) -> dict:
    """Данные, которых бот пока не использует, выровненные по времени свечей."""
    out: dict[str, list[float | None]] = {}
    idx = {t: i for i, t in enumerate(times)}
    n = len(times)

    def put(name: str, rows, key: str, ts_key: str = "timestamp"):
        series: list[float | None] = [None] * n
        hit = 0
        for r in rows:
            try:
                t = int(r[ts_key])
                v = float(r[key])
            except (KeyError, TypeError, ValueError):
                continue
            # привязываем к ближайшей свече не позже отметки
            slot = idx.get(t - t % (int(interval) * 60_000)) if interval.isdigit() else idx.get(t)
            if slot is not None:
                series[slot] = v
                hit += 1
        if hit >= n // 4:
            out[name] = series

    itv = f"{interval}min" if interval.isdigit() and int(interval) < 60 else \
          ("1h" if interval == "60" else "4h" if interval == "240" else "1d")
    try:
        r = http.get_open_interest(category="linear", symbol=symbol,
                                   intervalTime=itv, limit=200)["result"]["list"]
        put("открытый интерес", r, "openInterest")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! открытый интерес недоступен: {exc}")
    try:
        r = http.get_long_short_ratio(category="linear", symbol=symbol,
                                      period=itv, limit=500)["result"]["list"]
        put("доля лонгов", r, "buyRatio")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! соотношение лонг/шорт недоступно: {exc}")
    try:
        r = http.get_premium_index_price_kline(
            category="linear", symbol=symbol, interval=interval, limit=1000)["result"]["list"]
        prem = {int(x[0]): float(x[4]) for x in r}
        series = [prem.get(t) for t in times]
        if sum(1 for v in series if v is not None) >= n // 4:
            out["премия к индексу"] = series
    except Exception as exc:  # noqa: BLE001
        print(f"  ! премия к индексу недоступна: {exc}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Есть ли предсказательная сила в признаках")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="60")
    ap.add_argument("--bars", type=int, default=1000)
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 4, 12])
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    rows = http.get_kline(category="linear", symbol=args.symbol,
                          interval=args.interval, limit=min(args.bars, 1000))["result"]["list"]
    rows = sorted(rows, key=lambda r: int(r[0]))[:-1]        # незакрытую свечу отбрасываем
    if len(rows) < 200:
        print("Слишком мало свечей.")
        return 1
    times = [int(r[0]) for r in rows]
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    vols = [float(r[5]) for r in rows]
    n = len(closes)
    print(f"\nЗагружено {n} свечей {args.symbol} {args.interval}m")

    feats: dict[str, list[float | None]] = {
        "RSI 14": align_right(rsi(closes, 14), n),
        "MACD гистограмма": align_right(macd(closes)[2], n),
        "Bollinger %B": align_right(bollinger_pct_b(closes), n),
        "ADX 14": align_right(adx(highs, lows, closes)[0], n),
        "доходность прошлого бара": [None] + [(closes[i] / closes[i - 1] - 1) for i in range(1, n)],
        "объём к среднему": [None] * 20 + [
            vols[i] / (statistics.mean(vols[i - 20:i]) or 1) for i in range(20, n)],
        "размах бара": [(highs[i] - lows[i]) / closes[i] for i in range(n)],
    }
    print("Тяну данные, которых бот пока не использует...")
    feats.update(fetch_extras(http, args.symbol, args.interval, args.bars, times))

    print("\n" + "=" * 72)
    print("  ИНФОРМАЦИОННЫЙ КОЭФФИЦИЕНТ: связь признака с будущей доходностью")
    print("=" * 72)
    head = "  " + f"{'признак':<26}" + "".join(f"{f'через {h}':>11}" for h in args.horizons)
    print(head)
    print("  " + "-" * (len(head) - 2))

    best: list[tuple[float, str, int]] = []
    for name, series in feats.items():
        cells = []
        for h in args.horizons:
            xs, ys = [], []
            for i in range(n - h):
                v = series[i]
                if v is None:
                    continue
                xs.append(v)
                ys.append(closes[i + h] / closes[i] - 1)
            m = len(xs)
            ic = spearman(xs, ys) if m >= 50 else 0.0
            # значимость: два стандартных отклонения от нуля при данном m
            threshold = 2.0 / math.sqrt(m) if m else 1.0
            best.append((abs(ic), name, h, threshold, m))
            mark = "*" if abs(ic) >= threshold * 1.5 else (
                "~" if abs(ic) >= threshold else " ")
            cells.append(f"{ic:>10.3f}{mark}")
        print(f"  {name:<26}" + "".join(cells))
    print("=" * 72)
    example_n = max((b[4] for b in best), default=0)
    if example_n:
        print(f"  Порог значимости при {example_n} наблюдениях: "
              f"|IC| > {2/math.sqrt(example_n):.3f}")
    print("  ~ выше порога значимости    * в полтора раза выше порога")
    print("  Всё остальное неотличимо от шума.\n")

    strong = [b for b in best if b[0] >= b[3]]
    if strong:
        strong.sort(reverse=True)
        print("  ЕСТЬ ЗА ЧТО ЗАЦЕПИТЬСЯ")
        for ic, name, h, thr, m in strong[:5]:
            print(f"    {name} на горизонте {h} баров: |IC| = {ic:.3f} "
                  f"при пороге {thr:.3f} ({m} набл.)")
        print("\n  Это ещё не стратегия: IC показывает связь, а не прибыль")
        print("  после комиссий. Но строить имеет смысл именно вокруг них.\n")
    else:
        print("  НИ ОДИН признак не превысил порога значимости.")
        print("  Это объясняет все прошлые результаты: дело не в настройках")
        print("  и не в выборе режима — в этих данных на этом горизонте")
        print("  предсказательной информации попросту нет.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
