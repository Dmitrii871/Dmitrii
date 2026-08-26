#!/usr/bin/env python3
"""Точка входа. Запуск: python -m bot.main --config config.yaml"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .exchange import Exchange
from .models import Action, RiskHalt, RiskReject
from .risk import RiskManager
from .strategies import build
from .strategies.base import Context

log = logging.getLogger("bot")
_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    log.warning("Получен сигнал %s — останавливаюсь после текущего цикла", signum)
    _stop = True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log")],
    )


def confirm_mainnet(cfg: dict) -> None:
    """Реальные деньги — только через явный флаг и подтверждение с клавиатуры."""
    print("\n" + "=" * 68)
    print("  ВНИМАНИЕ: РЕАЛЬНЫЕ ДЕНЬГИ (MAINNET)")
    print(f"  Символ:            {cfg['symbol']}")
    print(f"  Стратегия:         {cfg['strategy']['name']}")
    print(f"  Плечо:             {cfg.get('leverage')}x")
    print(f"  Макс. позиция:     {cfg['risk']['max_position_usdt']} USDT")
    print(f"  Стоп по убытку:    {cfg['risk']['max_daily_loss_usdt']} USDT")
    print("=" * 68)
    if input("Введите YES заглавными для запуска: ").strip() != "YES":
        print("Отменено.")
        sys.exit(0)


def run(cfg: dict, dry_run: bool) -> int:
    strat_cfg = cfg["strategy"]
    name = strat_cfg["name"]
    strategy = build(name, strat_cfg.get(name, {}))
    strategy.validate(cfg.get("fees", {}))

    ex = Exchange(cfg, os.getenv("BYBIT_API_KEY", ""), os.getenv("BYBIT_API_SECRET", ""), dry_run)
    risk = RiskManager(cfg["risk"])
    inst = ex.instrument()
    if not dry_run:
        ex.set_leverage(int(cfg.get("leverage", 3)))

    interval = strat_cfg.get(name, {}).get("interval", "30")
    warmup = max(strategy.warmup_bars(), 60)
    poll = int(cfg.get("poll_seconds", 10))
    log.info("Стратегия '%s' запущена. Ctrl+C или файл %s для остановки.",
             name, cfg["risk"].get("kill_switch_file", "./STOP"))

    while not _stop:
        try:
            account = ex.account()
            risk.check_session(account)

            ctx = Context(
                md=ex.market_data(interval, warmup),
                position=ex.position(),
                account=account,
                instrument=inst,
                open_orders=ex.open_orders(),
                maker_bps=float(cfg.get("fees", {}).get("maker_bps", 2.0)),
                taker_bps=float(cfg.get("fees", {}).get("taker_bps", 5.5)),
            )

            for action in strategy.decide(ctx):
                try:
                    risk.validate(action, ctx.position, ctx.account, ctx.md.mid)
                except RiskReject as exc:
                    log.warning("Отклонено риск-менеджером: %s | %s", action.describe(), exc)
                    continue
                ex.execute(action)

        except RiskHalt as exc:
            log.error("ОСТАНОВКА ПО РИСКУ: %s", exc)
            if not dry_run:
                log.error("Снимаю ордера и закрываю позицию.")
                ex.execute(Action(kind="cancel_all", reason="аварийная остановка"))
                ex.execute(Action(kind="close", reason="аварийная остановка"))
            return 2
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001 — цикл не должен падать от одной ошибки
            log.exception("Ошибка в цикле: %s", exc)
            time.sleep(min(poll * 3, 60))
            continue

        time.sleep(poll)

    log.info("Остановлен штатно.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Торговый бот Bybit v5")
    ap.add_argument("--config", default="config.yaml", help="путь к YAML-конфигу")
    ap.add_argument("--live", action="store_true",
                    help="разрешить mainnet; без него testnet принудительно")
    ap.add_argument("--strategy", choices=["signal", "maker"], help="переопределить стратегию")
    args = ap.parse_args()

    load_dotenv()
    path = Path(args.config)
    if not path.exists():
        print(f"Нет файла {path}. Скопируйте config.example.yaml в config.yaml", file=sys.stderr)
        return 1
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if args.strategy:
        cfg["strategy"]["name"] = args.strategy

    setup_logging(cfg.get("log_level", "INFO"))

    if not cfg.get("testnet", True) and not args.live:
        log.error("В конфиге testnet: false, но флаг --live не передан. Принудительно testnet.")
        cfg["testnet"] = True

    dry_run = bool(cfg.get("dry_run", True))
    if not cfg.get("testnet", True) and not dry_run:
        confirm_mainnet(cfg)

    if not os.getenv("BYBIT_API_KEY"):
        log.error("Не заданы BYBIT_API_KEY / BYBIT_API_SECRET. Скопируйте .env.example в .env")
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    return run(cfg, dry_run)


if __name__ == "__main__":
    sys.exit(main())
