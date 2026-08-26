#!/usr/bin/env python3
"""Диагностика счёта: собирает всё, что нужно для настройки бота.

Запускается ЛОКАЛЬНО на вашей машине. Ключи API читаются из .env,
никуда не отправляются и не попадают в отчёт. Идентификаторы счёта
вырезаются. Готовый diagnostics.json можно безопасно показать кому угодно.

    python tools/diagnose.py --symbol ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REDACT = {"uid", "userID", "accountId", "memberId", "apiKey", "id", "orderLinkId"}


def scrub(obj):
    """Рекурсивно вырезает идентификаторы, чтобы отчёт можно было показывать."""
    if isinstance(obj, dict):
        return {k: ("<скрыто>" if k in REDACT else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def safe(fn, label: str):
    """Один недоступный эндпоинт не должен рушить весь отчёт."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {label}: {exc}")
        return {"error": str(exc)}


def analyse_closed_pnl(rows: list[dict]) -> dict:
    """Статистика по вашим закрытым сделкам — база для настройки TP/SL."""
    if not rows:
        return {"trades": 0, "note": "нет закрытых сделок за период"}
    pnls = [float(r.get("closedPnl", 0)) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    holds = []
    for r in rows:
        try:
            holds.append((int(r["updatedTime"]) - int(r["createdTime"])) / 60_000)
        except (KeyError, ValueError, TypeError):
            pass
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "trades": len(pnls),
        "net_pnl_usdt": round(sum(pnls), 4),
        "win_rate": round(len(wins) / len(pnls), 4),
        "avg_win_usdt": round(statistics.mean(wins), 4) if wins else 0.0,
        "avg_loss_usdt": round(statistics.mean(losses), 4) if losses else 0.0,
        "biggest_win_usdt": round(max(pnls), 4),
        "biggest_loss_usdt": round(min(pnls), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "expectancy_per_trade_usdt": round(sum(pnls) / len(pnls), 4),
        "median_hold_minutes": round(statistics.median(holds), 1) if holds else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Диагностика счёта Bybit для настройки бота")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--testnet", action="store_true")
    ap.add_argument("--out", default="diagnostics.json")
    args = ap.parse_args()

    from dotenv import load_dotenv
    from pybit.unified_trading import HTTP

    load_dotenv()
    key, secret = os.getenv("BYBIT_API_KEY"), os.getenv("BYBIT_API_SECRET")
    if not key or not secret:
        print("Нет BYBIT_API_KEY / BYBIT_API_SECRET в .env", file=sys.stderr)
        return 1

    http = HTTP(testnet=args.testnet, api_key=key, api_secret=secret, recv_window=10_000)
    sym, cat = args.symbol, "linear"
    print(f"Собираю данные по {sym}...")

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": sym,
        "environment": "testnet" if args.testnet else "mainnet",
    }

    # 1. Комиссии — важнейший вход для любой стратегии
    fee = safe(lambda: http.get_fee_rates(category=cat, symbol=sym)["result"]["list"][0], "комиссии")
    if "error" not in fee:
        maker, taker = float(fee.get("makerFeeRate", 0)), float(fee.get("takerFeeRate", 0))
        report["fees"] = {
            "maker_pct": maker * 100, "taker_pct": taker * 100,
            "maker_bps": round(maker * 10_000, 3), "taker_bps": round(taker * 10_000, 3),
            "round_trip_taker_bps": round(taker * 2 * 10_000, 3),
            "min_profitable_move_pct": round(taker * 2 * 100, 4),
        }

    # 2. Конфигурация счёта: режим маржи и режим позиций
    report["account_config"] = scrub(safe(lambda: http.get_account_info()["result"], "конфиг счёта"))

    bal = safe(lambda: http.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0], "баланс")
    if "error" not in bal:
        report["balance"] = {
            "total_equity_usdt": bal.get("totalEquity"),
            "available_usdt": bal.get("totalAvailableBalance"),
            "used_margin_usdt": bal.get("totalInitialMargin"),
            "maintenance_margin_usdt": bal.get("totalMaintenanceMargin"),
            "unrealised_pnl_usdt": bal.get("totalPerpUPL"),
        }

    # 3. Торговые фильтры символа — определяют минимальный размер сделки
    inst = safe(lambda: http.get_instruments_info(category=cat, symbol=sym)["result"]["list"][0], "фильтры")
    if "error" not in inst:
        lot, price = inst["lotSizeFilter"], inst["priceFilter"]
        report["instrument"] = {
            "tick_size": price["tickSize"],
            "qty_step": lot["qtyStep"],
            "min_order_qty": lot["minOrderQty"],
            "max_leverage": inst["leverageFilter"]["maxLeverage"],
        }

    # 4. Текущие позиции: плечо, режим, расстояние до ликвидации
    pos = safe(lambda: http.get_positions(category=cat, symbol=sym)["result"]["list"], "позиции")
    if isinstance(pos, list):
        report["positions"] = [{
            "side": p.get("side"), "size": p.get("size"),
            "avg_price": p.get("avgPrice"), "mark_price": p.get("markPrice"),
            "leverage": p.get("leverage"),
            "position_mode": "hedge" if p.get("positionIdx") in (1, 2) else "one-way",
            "trade_mode": "isolated" if p.get("tradeMode") == 1 else "cross",
            "liq_price": p.get("liqPrice"),
            "unrealised_pnl": p.get("unrealisedPnl"),
            "take_profit": p.get("takeProfit") or None,
            "stop_loss": p.get("stopLoss") or None,
        } for p in pos]

    # 5. История закрытых сделок — по ней видно, что реально работает
    pnl = safe(lambda: http.get_closed_pnl(category=cat, symbol=sym, limit=100)["result"]["list"],
               "закрытые P&L")
    if isinstance(pnl, list):
        report["closed_pnl_stats"] = analyse_closed_pnl(pnl)

    # 6. Фандинг: для позиций дольше 8 часов это отдельная статья расходов
    fr = safe(lambda: http.get_funding_rate_history(category=cat, symbol=sym, limit=90)["result"]["list"],
              "фандинг")
    if isinstance(fr, list) and fr:
        rates = [float(r["fundingRate"]) for r in fr]
        report["funding"] = {
            "samples": len(rates),
            "avg_rate_pct": round(statistics.mean(rates) * 100, 6),
            "annualised_cost_pct_for_long": round(statistics.mean(rates) * 3 * 365 * 100, 3),
            "share_positive": round(sum(1 for r in rates if r > 0) / len(rates), 3),
        }

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nОтчёт записан в {args.out} (ключи и идентификаторы в него не попали)\n")
    if "fees" in report:
        f = report["fees"]
        print(f"  Комиссия мейкер/тейкер : {f['maker_pct']:.4f}% / {f['taker_pct']:.4f}%")
        print(f"  Круг тейкером          : {f['round_trip_taker_bps']:.1f} bp "
              f"-> сделка меньше {f['min_profitable_move_pct']:.3f}% убыточна")
    if "closed_pnl_stats" in report and report["closed_pnl_stats"].get("trades"):
        s = report["closed_pnl_stats"]
        print(f"  Сделок в истории       : {s['trades']}")
        print(f"  Винрейт                : {s['win_rate']:.1%}")
        print(f"  Матожидание на сделку  : {s['expectancy_per_trade_usdt']:+.4f} USDT")
        print(f"  Профит-фактор          : {s['profit_factor']}")
    if "funding" in report:
        print(f"  Фандинг годовых (лонг) : {report['funding']['annualised_cost_pct_for_long']:+.2f}%")
    print("\nЭтот файл можно показать — в нём нет ключей и номера счёта.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
