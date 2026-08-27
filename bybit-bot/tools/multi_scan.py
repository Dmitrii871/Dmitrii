#!/usr/bin/env python3
"""Проверка признаков на НЕЗАВИСИМЫХ данных: много символов сразу.

Одну выборку можно спрашивать ограниченное число раз. Мы своё исчерпали:
54 проверки на ETH дали находки на краю значимости, а на дневном
таймфрейме знаки перевернулись. Дальше спрашивать те же данные бессмысленно.

Здесь другой вопрос. Если закономерность настоящая, она встретится на
РАЗНЫХ инструментах с ОДНИМ знаком. Если это шум, знаки распределятся
как подбрасывание монеты.

Проверка строгая: при отсутствии связи знак на каждом символе равновероятен,
поэтому вероятность совпадения k знаков из n считается биномиально. Восемь
одинаковых знаков из десяти — это p = 0.11, то есть ещё не находка.
Десять из десяти — p = 0.002, и вот это уже разговор.

    python tools/multi_scan.py --interval 60 --bars 1000
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import adx, bollinger_pct_b, macd, rsi  # noqa: E402
from tools.feature_scan import align_right, spearman  # noqa: E402

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
]


def binom_two_sided(k: int, n: int) -> float:
    """Вероятность получить k или более одинаковых знаков из n случайно."""
    if n == 0:
        return 1.0
    k = max(k, n - k)                       # берём большинство
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def features_for(closes, highs, lows, vols) -> dict[str, list[float | None]]:
    n = len(closes)
    return {
        "RSI 14": align_right(rsi(closes, 14), n),
        "MACD гистограмма": align_right(macd(closes)[2], n),
        "Bollinger %B": align_right(bollinger_pct_b(closes), n),
        "ADX 14": align_right(adx(highs, lows, closes)[0], n),
        "доходность прошлого бара": [None] + [closes[i] / closes[i - 1] - 1
                                              for i in range(1, n)],
        "объём к среднему": [None] * 20 + [
            vols[i] / (statistics.mean(vols[i - 20:i]) or 1) for i in range(20, n)],
        "размах бара": [(highs[i] - lows[i]) / closes[i] for i in range(n)],
    }


# Признаки, измеряющие ВЕЛИЧИНУ движения, а не его направление.
# У них знаковый IC подозрителен: если за период рынок в среднем рос,
# сила тренда совпадёт с ростом просто потому, что рост и был.
MAGNITUDE_FEATURES = {"ADX 14", "объём к среднему", "размах бара"}


def ic_for_symbol(http, symbol: str, interval: str, bars: int,
                  horizons: list[int]) -> dict[tuple[str, int], float] | None:
    rows = http.get_kline(category="linear", symbol=symbol,
                          interval=interval, limit=min(bars, 1000))["result"]["list"]
    rows = sorted(rows, key=lambda r: int(r[0]))[:-1]
    if len(rows) < 200:
        return None
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    vols = [float(r[5]) for r in rows]
    n = len(closes)

    out: dict[tuple[str, int], float] = {}
    for name, series in features_for(closes, highs, lows, vols).items():
        for h in horizons:
            xs, ys, mags = [], [], []
            for i in range(n - h):
                if series[i] is None:
                    continue
                r = closes[i + h] / closes[i] - 1
                xs.append(series[i])
                ys.append(r)
                mags.append(abs(r))
            out[(name, h)] = spearman(xs, ys) if len(xs) >= 50 else 0.0
            if name in MAGNITUDE_FEATURES and len(xs) >= 50:
                out[(name + " |модуль|", h)] = spearman(xs, mags)
    # снос рынка за период: если он велик, знаковый IC признаков величины
    # отражает его, а не связь
    out[("__снос__", 0)] = (closes[-1] / closes[0] - 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Устойчив ли признак на разных инструментах")
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--interval", default="60")
    ap.add_argument("--bars", type=int, default=1000)
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 4, 12])
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    per_symbol: dict[str, dict[tuple[str, int], float]] = {}
    for sym in args.symbols:
        try:
            res = ic_for_symbol(http, sym, args.interval, args.bars, args.horizons)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {sym}: {exc}")
            continue
        if res is None:
            print(f"  ! {sym}: мало истории")
            continue
        per_symbol[sym] = res
        print(f"  {sym} готов")

    n_sym = len(per_symbol)
    if n_sym < 4:
        print("\nНужно минимум 4 символа для выводов.")
        return 1

    drifts = [d[("__снос__", 0)] for d in per_symbol.values() if ("__снос__", 0) in d]
    mean_drift = statistics.mean(drifts) if drifts else 0.0
    same_way = sum(1 for x in drifts if x > 0)
    print(f"\nСнос рынка за период: в среднем {mean_drift*100:+.1f}%, "
          f"вверх у {same_way} из {len(drifts)} инструментов")
    if abs(mean_drift) > 0.03 and max(same_way, len(drifts) - same_way) >= len(drifts) * 0.8:
        print("! Рынок за период двигался преимущественно в одну сторону.")
        print("  У признаков ВЕЛИЧИНЫ движения (ADX, объём, размах) знаковый IC")
        print("  будет отражать этот снос, а не предсказательную связь.")
        print("  Смотрите на их строки «|модуль|»: если связь с модулем сильнее,")
        print("  признак предсказывает размах, а не направление.")

    keys = sorted({k for d in per_symbol.values() for k in d if k[0] != "__снос__"},
                  key=lambda k: (k[0], k[1]))
    print("\n" + "=" * 78)
    print(f"  УСТОЙЧИВОСТЬ ПРИЗНАКОВ НА {n_sym} ИНСТРУМЕНТАХ, {args.interval}m")
    print("=" * 78)
    print(f"  {'признак':<26} {'гор':>4} {'средний IC':>11} {'знаков':>8} "
          f"{'p':>8} {'вердикт':>12}")
    print("  " + "-" * 74)

    findings = []
    for key in keys:
        name, h = key
        vals = [d[key] for d in per_symbol.values() if key in d]
        if len(vals) < 4:
            continue
        mean_ic = statistics.mean(vals)
        pos = sum(1 for v in vals if v > 0)
        agree = max(pos, len(vals) - pos)
        p = binom_two_sided(agree, len(vals))
        # порог с поправкой на число проверенных пар признак-горизонт
        p_thr = 0.05 / len(keys)
        verdict = "УСТОЙЧИВ" if p < p_thr else ("похоже" if p < 0.05 else "шум")
        findings.append((p, mean_ic, name, h, agree, len(vals), verdict))
        print(f"  {name:<26} {h:>4} {mean_ic:>11.4f} {agree:>4}/{len(vals):<3} "
              f"{p:>8.4f} {verdict:>12}")

    print("=" * 78)
    print(f"  Порог с поправкой на {len(keys)} проверок: p < {0.05/len(keys):.4f}")
    print("  знаков — сколько инструментов сошлись в направлении связи\n")

    # Признак величины считается находкой, только если знаковая связь
    # сильнее связи с модулем — иначе он про размах, а не про направление
    mods = {(n.replace(" |модуль|", ""), h): ic
            for _, ic, n, h, _, _, _ in findings if "|модуль|" in n}
    solid = []
    for f in findings:
        p_, ic, name, h, agree, tot, verdict = f
        if verdict != "УСТОЙЧИВ" or "|модуль|" in name:
            continue
        if name in MAGNITUDE_FEATURES and (name, h) in mods:
            if abs(mods[(name, h)]) >= abs(ic):
                continue          # предсказывает размах, а не направление
        solid.append(f)
    print("  ВЫВОД")
    if solid:
        solid.sort()
        print(f"  {len(solid)} признаков дали один знак на разных инструментах:")
        for p, ic, name, h, agree, tot, _ in solid:
            direction = "рост -> падение" if ic < 0 else "рост -> рост"
            print(f"    {name}, горизонт {h}: средний IC {ic:+.4f}, "
                  f"{agree} из {tot} ({direction})")
        print("\n  Это уже не находка на одной выборке. Следующий шаг — проверить")
        print("  оптимизатором, переживает ли связь комиссии, а не только шум.")
    else:
        print("  Ни один признак не показал устойчивого знака на разных")
        print("  инструментах. Находки на ETH были особенностью одной выборки,")
        print("  а не закономерностью рынка. Это исчерпывающий ответ:")
        print("  публичные индикаторы на этих горизонтах преимущества не дают.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
