#!/usr/bin/env python3
"""Проверка правила «покупать низ Боллинджера, продавать середину».

Идея пользователя, дословно: покупка когда %B у нуля (цена на нижней
полосе) и/или RSI около 30; продажа у %B 0.4-0.5 или RSI 50, не дожидаясь
верхней полосы. Без стопа, без шортов — как на споте.

Скрипт отвечает цифрами на три вопроса:
1. Как часто отскок доезжает до середины и сколько это даёт валовыми.
2. Что остаётся после комиссий — спотовых (10 bp/сторона) и фьючерсных.
3. Какой ценой: без стопа просадка живёт внутри позиции — считаем её.

Запуск:  ./.venv/bin/python3 tools/dip.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import bollinger_pct_b, rsi  # noqa: E402
from tools.backtest import fetch_klines  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
# число свечей можно передать аргументом: tools/dip.py 12000 (~1.4 года часовиков)
BARS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2000
NOTIONAL = 25.0
RSI_P, BB_P = 14, 20


def run_rule(closes, lows, entry_bb, entry_rsi, exit_bb, exit_rsi, need_both):
    r = rsi(closes, RSI_P)
    b = bollinger_pct_b(closes, BB_P)
    off_r = len(closes) - len(r)
    off_b = len(closes) - len(b)
    trades = []          # (вход, выход, баров в позиции, худшая цена внутри)
    pos_entry = None
    entry_i = 0
    worst = 0.0
    for i in range(max(off_r + 1, off_b + 1), len(closes)):
        ri, bi = r[i - off_r], b[i - off_b]
        price = closes[i]
        if pos_entry is None:
            hit_bb = bi <= entry_bb
            hit_rsi = ri <= entry_rsi
            trigger = (hit_bb and hit_rsi) if need_both else (hit_bb or hit_rsi)
            if trigger:
                pos_entry, entry_i, worst = price, i, price
        else:
            worst = min(worst, lows[i])
            if bi >= exit_bb or ri >= exit_rsi:
                trades.append((pos_entry, price, i - entry_i, worst))
                pos_entry = None
    return trades


def run_flip_series(vals, closes, lows, highs, low_lvl, high_lvl,
                    stop_pct=0.0, rearm=None):
    """Перевёртыш по произвольному ряду (RSI, %B): лонг у нижнего края,
    шорт у верхнего, позиция всегда одна. Логика идентична run_flip."""
    off = len(closes) - len(vals)
    if rearm is None:
        rearm = (high_lvl - low_lvl) * 0.07
    trades = []
    side, entry, entry_i, worst = None, 0.0, 0, 0.0
    long_armed = short_armed = True
    for i in range(off + 1, len(closes)):
        v = vals[i - off]
        price = closes[i]
        if v > low_lvl + rearm:
            long_armed = True
        if v < high_lvl - rearm:
            short_armed = True
        if side == "long":
            worst = min(worst, lows[i])
            if stop_pct and lows[i] <= entry * (1 - stop_pct / 100):
                trades.append((entry, entry * (1 - stop_pct / 100), i - entry_i, worst, "long"))
                side, long_armed = None, False
                continue
        elif side == "short":
            worst = max(worst, highs[i])
            if stop_pct and highs[i] >= entry * (1 + stop_pct / 100):
                trades.append((entry, entry * (1 + stop_pct / 100), i - entry_i, worst, "short"))
                side, short_armed = None, False
                continue
        want = "long" if v <= low_lvl else ("short" if v >= high_lvl else None)
        if want == "long" and not long_armed:
            want = None
        if want == "short" and not short_armed:
            want = None
        if want and want != side:
            if side is not None:
                trades.append((entry, price, i - entry_i, worst, side))
            side, entry, entry_i, worst = want, price, i, price
    return trades


def run_flip(closes, lows, highs, low_lvl=30.0, high_lvl=70.0, stop_pct=0.0):
    """Перевёртыш: лонг при RSI<=30, шорт при RSI>=70, позиция всегда одна.

    stop_pct > 0 — стоп-лосс от входа; после стопа повторный вход в ту же
    сторону разрешается только после того, как RSI вышел из крайней зоны,
    иначе правило немедленно откупает тот же нож.
    Сделка: (вход, выход, баров, худшая цена против позиции, сторона).
    """
    r = rsi(closes, RSI_P)
    off = len(closes) - len(r)
    trades = []
    side = None
    entry = 0.0
    entry_i = 0
    worst = 0.0
    long_armed = short_armed = True
    for i in range(off + 1, len(closes)):
        ri = r[i - off]
        price = closes[i]

        if ri > low_lvl + 5:
            long_armed = True
        if ri < high_lvl - 5:
            short_armed = True

        if side == "long":
            worst = min(worst, lows[i])
            if stop_pct and lows[i] <= entry * (1 - stop_pct / 100):
                stop_price = entry * (1 - stop_pct / 100)
                trades.append((entry, stop_price, i - entry_i, worst, "long"))
                side, long_armed = None, False
                continue
        elif side == "short":
            worst = max(worst, highs[i])
            if stop_pct and highs[i] >= entry * (1 + stop_pct / 100):
                stop_price = entry * (1 + stop_pct / 100)
                trades.append((entry, stop_price, i - entry_i, worst, "short"))
                side, short_armed = None, False
                continue

        want = "long" if ri <= low_lvl else ("short" if ri >= high_lvl else None)
        if want == "long" and not long_armed:
            want = None
        if want == "short" and not short_armed:
            want = None
        if want and want != side:
            if side is not None:
                trades.append((entry, price, i - entry_i, worst, side))
            side, entry, entry_i, worst = want, price, i, price
    return trades


def report_flip(trades, fee_bps, funding_bps_day=3.0):
    fee = fee_bps / 10_000
    fund = funding_bps_day / 10_000
    nets = []
    dips = []
    for e, x, h, w, side in trades:
        gross = (x - e) / e if side == "long" else (e - x) / e
        nets.append((gross - 2 * fee - (h / 24) * fund) * NOTIONAL)
        dips.append((e - w) / e if side == "long" else (w - e) / e)
    wins = [n for n in nets if n > 0]
    return {
        "n": len(nets), "net": sum(nets),
        "wr": len(wins) / len(nets) * 100 if nets else 0,
        "avg_hold": sum(h for _, _, h, _, _ in trades) / len(trades) if trades else 0,
        "worst_dip": max(dips, default=0.0) * 100,
    }


def report(trades, fee_bps):
    fee = fee_bps / 10_000
    nets = [((x - e) / e - 2 * fee) * NOTIONAL for e, x, _, _ in trades]
    wins = [n for n in nets if n > 0]
    dd_inside = max(((e - w) / e for e, _, _, w in trades), default=0.0)
    return {
        "n": len(nets), "net": sum(nets),
        "wr": len(wins) / len(nets) * 100 if nets else 0,
        "avg_hold": sum(h for _, _, h, _ in trades) / len(trades) if trades else 0,
        "worst_dip": dd_inside * 100,
    }


def main() -> int:
    variants = [
        ("%B<=0.05 ИЛИ RSI<=30 -> %B>=0.45 или RSI>=50", 0.05, 30, 0.45, 50, False),
        ("%B<=0.05 И RSI<=30 (строже)                 ", 0.05, 30, 0.45, 50, True),
        # чистое правило по одному RSI: купить у 30, продать у 70
        ("RSI<=30 -> RSI>=70 (без Боллинджера)        ", -1.0, 30, 9.9, 70, False),
    ]
    print(f"Часовые свечи, {BARS} шт на символ, позиция {NOTIONAL:.0f} USDT, без стопа\n")
    for name, ebb, ersi, xbb, xrsi, both in variants:
        total_spot = total_perp = total_n = 0.0
        print(f"ПРАВИЛО: {name}")
        print(f"  {'символ':<10}{'сделок':>7}{'винрейт':>9}{'держали':>9}"
              f"{'валовыми':>10}{'спот -10bp':>11}{'перп -5.5bp':>12}{'макс.пров.':>11}")
        for sym in SYMBOLS:
            try:
                rows = fetch_klines(sym, "60", BARS, testnet=False)
            except Exception as exc:  # noqa: BLE001
                print(f"  {sym}: нет данных ({exc})")
                continue
            closes = [float(x[4]) for x in rows]
            lows = [float(x[3]) for x in rows]
            trades = run_rule(closes, lows, ebb, ersi, xbb, xrsi, both)
            g = report(trades, 0.0)
            s = report(trades, 10.0)
            p = report(trades, 5.5)
            total_spot += s["net"]; total_perp += p["net"]; total_n += g["n"]
            print(f"  {sym:<10}{g['n']:>7}{g['wr']:>8.0f}%{g['avg_hold']:>8.1f}ч"
                  f"{g['net']:>10.2f}{s['net']:>11.2f}{p['net']:>12.2f}"
                  f"{g['worst_dip']:>10.1f}%")
        print(f"  {'ИТОГО':<10}{total_n:>7.0f}{'':>9}{'':>9}{'':>10}"
              f"{total_spot:>11.2f}{total_perp:>12.2f}\n")
    print("ПРАВИЛО-ПЕРЕВЁРТЫШ: лонг при RSI<=30, шорт при RSI>=70 (фьючерсы,")
    print("комиссия 5.5 bp/сторона + фандинг 3 bp/день удержания)")
    data = {}
    for sym in SYMBOLS:
        try:
            rows = fetch_klines(sym, "60", BARS, testnet=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: нет данных ({exc})")
            continue
        data[sym] = ([float(x[4]) for x in rows], [float(x[3]) for x in rows],
                     [float(x[2]) for x in rows])
    print("  === по RSI (лонг<=30 / шорт>=70) ===")
    for stop, label in ((0.0, "без стопа"), (2.0, "стоп 2%"), (5.0, "стоп 5%")):
        print(f"  --- {label} ---")
        print(f"  {'символ':<10}{'сделок':>7}{'винрейт':>9}{'держали':>9}"
              f"{'итог USDT':>11}{'макс.против':>12}")
        t_net = t_n = 0.0
        for sym, (closes, lows, highs) in data.items():
            fr = report_flip(run_flip(closes, lows, highs, stop_pct=stop), 5.5)
            t_net += fr["net"]; t_n += fr["n"]
            print(f"  {sym:<10}{fr['n']:>7}{fr['wr']:>8.0f}%{fr['avg_hold']:>8.1f}ч"
                  f"{fr['net']:>11.2f}{fr['worst_dip']:>11.1f}%")
        print(f"  {'ИТОГО':<10}{t_n:>7.0f}{'':>9}{'':>9}{t_net:>11.2f}\n")

    print("  === по Боллинджеру (лонг при %B<=0 / шорт при %B>=1) ===")
    for stop, label in ((0.0, "без стопа"), (2.0, "стоп 2%")):
        print(f"  --- {label} ---")
        print(f"  {'символ':<10}{'сделок':>7}{'винрейт':>9}{'держали':>9}"
              f"{'итог USDT':>11}{'макс.против':>12}")
        t_net = t_n = 0.0
        for sym, (closes, lows, highs) in data.items():
            b = bollinger_pct_b(closes, BB_P)
            tr = run_flip_series(b, closes, lows, highs, 0.0, 1.0,
                                 stop_pct=stop, rearm=0.15)
            fr = report_flip(tr, 5.5)
            t_net += fr["net"]; t_n += fr["n"]
            print(f"  {sym:<10}{fr['n']:>7}{fr['wr']:>8.0f}%{fr['avg_hold']:>8.1f}ч"
                  f"{fr['net']:>11.2f}{fr['worst_dip']:>11.1f}%")
        print(f"  {'ИТОГО':<10}{t_n:>7.0f}{'':>9}{'':>9}{t_net:>11.2f}\n")

    print("Читать так: «валовыми» — если бы комиссий не было; следующие две")
    print("колонки — что остаётся на споте и на перпетуалах. «Макс.пров.» —")
    print("насколько глубоко цена уходила ПОД вход, пока ждали отскока:")
    print("без стопа это и есть настоящий риск правила.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
