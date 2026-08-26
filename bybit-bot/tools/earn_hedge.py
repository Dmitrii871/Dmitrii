#!/usr/bin/env python3
"""Можно ли забрать высокую ставку Earn, захеджировав ценовой риск.

Соблазн выглядит так: монета платит 100% годовых в Earn, купим её,
зашортим в бессрочном контракте — цена станет безразлична, и останется
чистая ставка.

Ловушка в том, что обе ставки порождены ОДНИМ дефицитом: спросом занять
монету для продажи в шорт. Биржа платит вам за монету потому, что кто-то
платит ей за право эту монету шортить. Этот инструмент считает, что
останется после вычитания фандинга.

Ставки Earn берутся с экрана биржи, публичного API для них нет:
    python tools/earn_hedge.py --coins BICO=106.83 BMT=80.40 MOVE=75.38
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_coins(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"Формат: МОНЕТА=СТАВКА, например BICO=106.83 (получено '{it}')")
        coin, rate = it.split("=", 1)
        coin = coin.strip().upper()
        if not coin:
            raise SystemExit(f"Пустое имя монеты в '{it}'")
        try:
            out[coin] = float(rate)
        except ValueError:
            raise SystemExit(f"Не число: '{rate}'")
    return out


def funding_apr(http, symbol: str, periods: int) -> tuple[float, float, float, int] | None:
    """Возвращает (фактические годовые, медианные годовые, доля положительных, выплат).

    Решение принимается по ФАКТИЧЕСКИ накопленному, а не по медиане.
    У фандинга бывают редкие огромные всплески: медиана их не видит,
    а списывается каждая выплата. На BMTUSDT медиана давала -6% годовых
    при фактических -104% — расхождение в 17 раз, и по медиане позиция
    выглядела бы прибыльной, будучи убыточной.
    """
    inst = http.get_instruments_info(category="linear", symbol=symbol)["result"]["list"]
    if not inst:
        return None
    interval_h = int(inst[0].get("fundingInterval") or 480) / 60
    hist = http.get_funding_rate_history(
        category="linear", symbol=symbol, limit=min(periods, 200))["result"]["list"]
    rates = [float(r["fundingRate"]) for r in hist]
    if not rates:
        return None
    per_year = 8760 / interval_h
    positive = sum(1 for r in rates if r > 0) / len(rates)
    realized = statistics.mean(rates) * per_year * 100     # то, что реально начислится
    median = statistics.median(rates) * per_year * 100     # для сравнения: ровный ли поток
    return realized, median, positive, len(rates)


def main() -> int:
    ap = argparse.ArgumentParser(description="Хедж высокой ставки Earn: что останется")
    ap.add_argument("--coins", nargs="+", required=True,
                    help="пары МОНЕТА=СТАВКА со страницы Earn, напр. BICO=106.83")
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--periods", type=int, default=200)
    args = ap.parse_args()

    from pybit.unified_trading import HTTP

    http = HTTP(testnet=False)
    coins = parse_coins(args.coins)

    print(f"\nКапитал {args.capital:.0f}$. Шорт в бессрочном полностью гасит ценовой риск,")
    print("поэтому остаётся ставка Earn МИНУС фандинг, который платит шорт.\n")
    print("=" * 78)
    print(f"  {'монета':<7} {'Earn':>8} {'фандинг факт':>13} {'медиана':>9} {'полож.':>7} "
          f"{'ИТОГО':>8} {'$/год':>8}")
    print("  " + "-" * 74)

    rows = []
    for coin, earn in sorted(coins.items(), key=lambda kv: -kv[1]):
        sym = f"{coin}USDT"
        try:
            res = funding_apr(http, sym, args.periods)
        except Exception as exc:  # noqa: BLE001
            print(f"  {coin:<8} {earn:>8.2f}%  нет бессрочного контракта ({exc})")
            continue
        if res is None:
            print(f"  {coin:<8} {earn:>8.2f}%  нет данных по фандингу — хедж невозможен")
            continue
        f_apr, f_med, positive, n = res
        # Шорт получает положительный фандинг и платит отрицательный
        net = earn + f_apr
        skew = "  !" if f_med != 0 and abs(f_apr / f_med) > 3 else ""
        rows.append((coin, earn, f_apr, f_med, positive, n, net))
        print(f"  {coin:<7} {earn:>7.2f}% {f_apr:>12.1f}% {f_med:>8.1f}% {positive:>6.0%} "
              f"{net:>7.1f}% {net/100*args.capital:>7.2f}${skew}")

    print("=" * 78)
    if not rows:
        return 1

    skewed = [r for r in rows if r[3] != 0 and abs(r[2] / r[3]) > 3]
    if skewed:
        print(f"\n  ! У {len(skewed)} монет факт расходится с медианой более чем втрое —")
        print("    это редкие огромные выплаты. Решение принимайте по столбцу ФАКТ:")
        print("    медиана таких всплесков не видит, а списывается каждая выплата.")

    good = [r for r in rows if r[6] > 6.82]      # порог: лучшая ставка USDT в Earn
    print("\n  ВЫВОД")
    if good:
        print(f"  {len(good)} монет обгоняют простой вклад USDT (6.82%) даже после фандинга:")
        for coin, earn, f_apr, f_med, positive, n, net in good:
            print(f"    {coin}: {net:.1f}% годовых — но проверьте ликвидность обеих ног,")
            print(f"      риск делистинга и то, что ставка Earn гибкая и меняется в любой момент.")
    else:
        print("  Ни одна монета не обгоняет простой вклад USDT после вычитания фандинга.")
        print("  Высокая ставка Earn и отрицательный фандинг — это один и тот же")
        print("  дефицит, посчитанный дважды. Рынок уже забрал разницу.")
    print("\n  ЧЕГО РАСЧЁТ НЕ УЧИТЫВАЕТ")
    print("  - ставка Earn гибкая: она падает, как только спрос на займ спадает")
    print("  - ёмкость Earn ограничена, вашу сумму могут не принять")
    print("  - фандинг взят медианой прошлого, будущее он не обещает")
    print("  - делистинг и остановка торгов у мелких монет вероятнее, чем кажется\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
