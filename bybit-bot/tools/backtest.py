#!/usr/bin/env python3
"""Бэктест сигнальной стратегии на реальной истории Bybit.

Главный инструмент проекта: отвечает на вопрос "зарабатывает ли эта
настройка вообще", ДО того как вы рискнёте деньгами. Учитывает комиссии,
считает просадку и распределение сделок.

Запуск (ключи API не нужны, данные публичные):
    python tools/backtest.py --symbol ETHUSDT --interval 30 --bars 1000
"""
from __future__ import annotations

import argparse
import statistics
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.strategies.signal import SignalStrategy  # noqa: E402


def synthetic_klines(bars: int, seed: int = 7, start: float = 2460.0,
                     vol_bps: float = 25.0, drift_bps: float = 0.0) -> list[list[str]]:
    """Случайное блуждание для офлайн-проверки движка.

    На данных без тренда и структуры прибыльной стратегии не существует —
    чистый результат должен выйти примерно в минус на величину комиссий.
    Это полезная калибровка: если ваш бэктест на случайных данных
    показывает плюс, значит в нём ошибка (заглядывание в будущее).
    """
    import random
    rng = random.Random(seed)
    price = start
    rows: list[list[str]] = []
    ts = 1_700_000_000_000
    for i in range(bars):
        o = price
        ret = rng.gauss(drift_bps / 10_000, vol_bps / 10_000)
        c = o * (1 + ret)
        wick = abs(rng.gauss(0, vol_bps / 20_000))
        h = max(o, c) * (1 + wick)
        l = min(o, c) * (1 - wick)
        rows.append([str(ts + i * 1_800_000), f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}", "0", "0"])
        price = c
    return rows


def csv_klines(path: str, bars: int) -> list[list[str]]:
    """CSV с колонками: timestamp,open,high,low,close[,volume,turnover]."""
    import csv as _csv
    rows: list[list[str]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = _csv.reader(fh)
        for row in reader:
            if not row or not row[0].strip().replace(".", "").isdigit():
                continue  # пропускаем заголовок
            rows.append([row[0], row[1], row[2], row[3], row[4], "0", "0"])
    return rows[-bars:]


def fetch_klines(symbol: str, interval: str, bars: int, testnet: bool) -> list[list[str]]:
    """Тянет историю постранично: биржа отдаёт максимум 1000 свечей за запрос."""
    from pybit.unified_trading import HTTP

    http = HTTP(testnet=testnet)
    rows: list[list[str]] = []
    end = None
    while len(rows) < bars:
        params = dict(category="linear", symbol=symbol, interval=interval, limit=1000)
        if end is not None:
            params["end"] = end
        chunk = http.get_kline(**params)["result"]["list"]
        if not chunk:
            break
        rows.extend(chunk)
        end = int(chunk[-1][0]) - 1
        if len(chunk) < 1000:
            break
    rows = sorted(rows, key=lambda r: int(r[0]))[-bars:]
    return rows


def backtest(rows, cfg: dict, fee_bps: float, notional: float) -> dict:
    strat = SignalStrategy(cfg)
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    warm = strat.warmup_bars()
    if len(closes) <= warm:
        raise SystemExit(f"Мало данных: нужно больше {warm} свечей, есть {len(closes)}")

    tp_pct, sl_pct = float(strat.tp_pct), float(strat.sl_pct)
    # Выход по времени: связь признака с доходностью живёт на фиксированном
    # горизонте, а стоп-лосс — выход по ПУТИ цены. Если стоп уже обычного
    # колебания на этом горизонте, позицию выбивает шумом до того, как
    # закономерность отработает. 0 — выход по времени выключен.
    hold_bars = int(cfg.get("hold_bars", 0))
    use_stop = sl_pct > 0
    fee = fee_bps / 10_000

    equity = 0.0
    curve = [0.0]
    trades: list[float] = []
    by_side: dict[str, list[float]] = {"Buy": [], "Sell": []}
    ambiguous = 0      # свечи, где задеты И тейк, И стоп: исход выбрало правило
    pos_side: str | None = None
    entry = 0.0
    entry_bar = 0
    last_entry_bar = -10**9
    timed_exits = 0

    # Голоса считаются один раз по всей истории: индикаторы причинные,
    # результат идентичен побарному пересчёту (см. test_votes_series_matches_per_bar),
    # но время падает с O(n^2) до O(n).
    all_votes = strat.votes_series(closes, highs, lows)

    for i in range(warm, len(closes)):
        # 1) сначала проверяем, не выбило ли открытую позицию на этой свече
        if pos_side is not None:
            if pos_side == "Buy":
                tp, sl = entry * (1 + tp_pct), entry * (1 - sl_pct)
                hit_sl = use_stop and lows[i] <= sl
                hit_tp = highs[i] >= tp
            else:
                tp, sl = entry * (1 - tp_pct), entry * (1 + sl_pct)
                hit_sl = use_stop and highs[i] >= sl
                hit_tp = lows[i] <= tp
            # консервативно: если свеча задела оба уровня, считаем стоп.
            # Доля таких случаев отслеживается: если она велика, результат
            # определяется этим правилом, а не рынком, и бэктесту нельзя верить.
            if hit_tp and hit_sl:
                ambiguous += 1
            exit_price = sl if hit_sl else (tp if hit_tp else None)
            # срок вышел — закрываем по цене закрытия бара
            if exit_price is None and hold_bars and i - entry_bar >= hold_bars:
                exit_price = closes[i]
                timed_exits += 1
            if exit_price is not None:
                gross = (exit_price - entry) / entry * (1 if pos_side == "Buy" else -1)
                net = (gross - 2 * fee) * notional
                equity += net
                trades.append(net)
                by_side[pos_side].append(net)
                curve.append(equity)
                pos_side = None

        # 2) затем ищем новый вход
        if pos_side is None and i - last_entry_bar >= strat.cooldown_bars:
            longs, shorts = all_votes[i]
            side = None
            if longs >= strat.min_confluence and longs > shorts:
                side = "Buy"
            elif shorts >= strat.min_confluence and shorts > longs:
                side = "Sell"
            if side:
                pos_side, entry, last_entry_bar, entry_bar = side, closes[i], i, i

    peak, max_dd = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    return {
        "bars": len(closes),
        "trades": len(trades),
        "net_usdt": equity,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf"),
        "max_drawdown": max_dd,
        "fees_paid": len(trades) * 2 * fee * notional,
        "ambiguous_share": (ambiguous / len(trades)) if trades else 0.0,
        "timed_exit_share": (timed_exits / len(trades)) if trades else 0.0,
        "open_at_end": pos_side is not None,
        "by_side": {
            side: {
                "trades": len(v),
                "net_usdt": round(sum(v), 4),
                "win_rate": round(len([x for x in v if x > 0]) / len(v), 4) if v else 0.0,
            }
            for side, v in by_side.items()
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Бэктест сигнальной стратегии")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="30")
    ap.add_argument("--bars", type=int, default=1000)
    ap.add_argument("--mode", choices=["reversion", "momentum", "auto", "both", "all"],
                    default="reversion",
                    help="reversion — покупать перепроданность; momentum — покупать силу")
    ap.add_argument("--notional", type=float, default=25.0, help="размер сделки, USDT")
    ap.add_argument("--fee-bps", type=float, default=5.5, help="комиссия за одну сторону, bp")
    ap.add_argument("--tp", type=float, default=1.2, help="take profit, %%")
    ap.add_argument("--sl", type=float, default=0.8,
                    help="stop loss, %%; 0 — без стопа, выход только по времени и тейку")
    ap.add_argument("--hold", type=int, default=0,
                    help="выход по времени через N баров; 0 — выключен")
    ap.add_argument("--confluence", type=int, default=2)
    ap.add_argument("--testnet", action="store_true")
    ap.add_argument("--csv", help="офлайн-бэктест из CSV: timestamp,open,high,low,close")
    ap.add_argument("--demo", action="store_true",
                    help="офлайн-прогон на случайном блуждании (проверка движка)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    cfg = {
        "take_profit_pct": args.tp,
        "stop_loss_pct": args.sl,
        "min_confluence": args.confluence,
        "order_notional_usdt": args.notional,
        "mode": args.mode if args.mode not in ("both", "all") else "reversion",
        "hold_bars": args.hold,
    }
    if args.demo:
        rows = synthetic_klines(args.bars, seed=args.seed)
        print(f"ДЕМО: {len(rows)} свечей случайного блуждания (данные не рыночные)")
    elif args.csv:
        rows = csv_klines(args.csv, args.bars)
        print(f"CSV: загружено {len(rows)} свечей из {args.csv}")
    else:
        rows = fetch_klines(args.symbol, args.interval, args.bars, args.testnet)
        print(f"Загружено {len(rows)} свечей {args.symbol} {args.interval}m")
    r = backtest(rows, cfg, args.fee_bps, args.notional)

    print("\n" + "=" * 56)
    print(f"  {args.symbol} {args.interval}m | TP {args.tp}% / SL {args.sl}% | "
          f"совпадений {args.confluence}")
    print("=" * 56)
    print(f"  Свечей в тесте     {r['bars']:>12}")
    print(f"  Сделок             {r['trades']:>12}")
    print(f"  Винрейт            {r['win_rate']:>11.1%}")
    print(f"  Средняя прибыль    {r['avg_win']:>12.4f} USDT")
    print(f"  Средний убыток     {r['avg_loss']:>12.4f} USDT")
    print(f"  Профит-фактор      {r['profit_factor']:>12.2f}")
    print(f"  Уплачено комиссий  {r['fees_paid']:>12.4f} USDT")
    print(f"  Макс. просадка     {r['max_drawdown']:>12.4f} USDT")
    print(f"  Спорных исходов    {r['ambiguous_share']:>11.1%}  "
          f"(свеча задела и тейк, и стоп)")
    if r["timed_exit_share"]:
        print(f"  Выходов по времени {r['timed_exit_share']:>11.1%}")
    print(f"  ЧИСТЫЙ РЕЗУЛЬТАТ   {r['net_usdt']:>12.4f} USDT")
    print("-" * 56)
    for side, label in (("Buy", "ЛОНГИ "), ("Sell", "ШОРТЫ")):
        d = r["by_side"][side]
        print(f"  {label}  сделок {d['trades']:>4} | винрейт {d['win_rate']:>6.1%} "
              f"| итог {d['net_usdt']:>9.4f} USDT")
    print("=" * 56)
    if r["ambiguous_share"] > 0.10:
        print(f"  ! {r['ambiguous_share']:.0%} исходов определены правилом бэктеста, а не рынком.")
        print("    Результату верить нельзя — нужны данные меньшего таймфрейма,")
        print("    чтобы понять, что сработало первым: тейк или стоп.")
    if r["trades"] < 30:
        print("  ! Меньше 30 сделок — статистика недостоверна, возьмите больше истории")
    longs, shorts = r["by_side"]["Buy"], r["by_side"]["Sell"]
    if longs["trades"] >= 10 and shorts["trades"] >= 10:
        if longs["net_usdt"] > 0 > shorts["net_usdt"]:
            print("  ! Прибыль только в лонг. Проверьте direction: long_only")
        elif shorts["net_usdt"] > 0 > longs["net_usdt"]:
            print("  ! Прибыль только в шорт. Проверьте direction: short_only")
    if r["net_usdt"] <= 0:
        print("  ! Стратегия убыточна на этой истории. Не запускайте её на реальные деньги.")
    else:
        print("  Положительный результат на истории НЕ гарантирует прибыль в будущем.")
        print("  Проверьте на другом периоде и символе, прежде чем рисковать деньгами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
