#!/usr/bin/env python3
"""Точка входа. Запуск: python -m bot.main --config config.yaml"""
from __future__ import annotations

import argparse
import logging
import os
from decimal import Decimal
import signal
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .exchange import FatalExchangeError, StaleDataError
from .lockfile import AlreadyRunning, single_instance
from .models import Action, RiskHalt, RiskReject
from .plan import TradingPlan
from .worker import aggregate_summary, make_workers
from .risk import RiskManager
from .strategies import build
from .strategies.base import Context

log = logging.getLogger("bot")
_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    log.warning("Получен сигнал %s — останавливаюсь после текущего цикла", signum)
    _stop = True


def _sleep(seconds: float) -> None:
    """Сон, прерываемый сигналом остановки.

    time.sleep(30) целиком доживал до конца и после Ctrl+C или pkill:
    бот «ещё жил» до половины минуты, а скрипт перезапуска в это время
    уже поднимал новый экземпляр — и они писали в один журнал вперемешку.
    """
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(1.0, max(0.0, end - time.monotonic())))


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


def run_check(cfg: dict) -> int:
    """Проверка готовности к бирже БЕЗ единого ордера — только чтение.

    Отвечает на вопрос «а сможем ли мы вообще подключиться», ДО того как
    потрачены две недели теста. Проверяется всё, обо что реально
    ломаются запуски: ключи и их права, IP-ограничение, расхождение
    часов, режим позиций, минимальные лоты против размера позиции,
    свежесть данных и достаточность маржи.
    """
    ok = True

    def step(name: str, fn):
        nonlocal ok
        try:
            result = fn()
            print(f"  [ok] {name}{': ' + str(result) if result is not None else ''}")
            return result
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [СБОЙ] {name}:\n         {exc}")
            return None

    print("=" * 64)
    print("  ПРОВЕРКА ПОДКЛЮЧЕНИЯ К БИРЖЕ (ордера НЕ отправляются)")
    print("=" * 64)
    if not os.getenv("BYBIT_API_KEY"):
        print("  [СБОЙ] Нет ключей: скопируйте .env.example в .env и впишите")
        print("         BYBIT_API_KEY / BYBIT_API_SECRET (права: только Trade,")
        print("         вывод средств ВЫКЛЮЧЕН). Ключи с bybit.com, не testnet.")
        return 1
    print(f"  Контур: {'TESTNET' if cfg.get('testnet', True) else 'ОСНОВНАЯ БИРЖА'}"
          " | режим только чтения")

    workers = make_workers(cfg, os.getenv("BYBIT_API_KEY", ""),
                           os.getenv("BYBIT_API_SECRET", ""), dry_run=True, plan=None)
    lead = workers[0].exchange
    step("часы синхронизированы", lambda: f"{lead.check_clock():.0f} мс")
    acc = step("ключи и баланс", lambda: (lambda a: f"капитал {a.equity} USDT, "
               f"свободно {a.available} USDT")(lead.account()))
    for w in workers:
        inst = step(f"{w.symbol}: справочник", lambda w=w: None if w.instrument() else None)
        step(f"{w.symbol}: режим позиций One-Way",
             lambda w=w: w.exchange.check_position_mode())
        step(f"{w.symbol}: свежие свечи",
             lambda w=w: f"{len(w.exchange.market_data(w.interval, w.warmup).closes)} шт")
        notional = getattr(w.strategy, "notional", None)
        if notional is not None:
            i = w.exchange.instrument()
            price = w.exchange.market_price()
            need = max(i.min_qty * price, i.min_notional)
            if notional < need:
                ok = False
                print(f"  [СБОЙ] {w.symbol}: позиция {notional} USDT меньше "
                      f"минимального лота {need:.0f} USDT")
            else:
                print(f"  [ok] {w.symbol}: позиция {notional} USDT >= лота {need:.2f} USDT")
    try:
        RiskManager(cfg["risk"]).preflight(lead.account(), int(cfg.get("leverage", 1)))
        print("  [ok] лимиты риска против маржи")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [СБОЙ] лимиты риска: {exc}")

    print("=" * 64)
    if ok:
        print("  ВСЁ ГОТОВО: подключение рабочее, можно тестировать и запускать.")
    else:
        print("  ЕСТЬ ПРОБЛЕМЫ — исправьте строки [СБОЙ] и повторите проверку.")
    return 0 if ok else 1


def run(cfg: dict, dry_run: bool) -> int:
    strat_cfg = cfg["strategy"]
    name = strat_cfg["name"]

    plan = None
    plan_path = cfg.get("plan_file")
    if plan_path:
        if not Path(plan_path).exists():
            log.error("Файл плана %s не найден", plan_path)
            return 1
        plan = TradingPlan.load(plan_path)
        if plan.note:
            log.info("Сценарий плана: %s", " ".join(plan.note.split()))

    workers = make_workers(cfg, os.getenv("BYBIT_API_KEY", ""),
                           os.getenv("BYBIT_API_SECRET", ""), dry_run, plan)
    if not workers:
        log.error("Не задано ни одного символа")
        return 1

    risk = RiskManager(cfg["risk"])
    leverage = int(cfg.get("leverage", 3))
    lead = workers[0].exchange
    lead.check_clock()
    for w in workers:
        w.exchange.check_position_mode()
        w.instrument()
        if not dry_run:
            w.exchange.set_leverage(leverage)
    risk.preflight(lead.account(), leverage)

    poll = int(cfg.get("poll_seconds", 10))
    heartbeat_every = max(1, int(cfg.get("heartbeat_seconds", 300)) // max(poll, 1))
    tick = 0
    stale_streak = 0
    start_equity: float | None = None
    log.info("Стратегия '%s' запущена. Ctrl+C или файл %s для остановки.",
             name, cfg["risk"].get("kill_switch_file", "./STOP"))

    while not _stop:
        try:
            account = lead.account()
            risk.check_session(account)
            tick += 1
            if start_equity is None:
                start_equity = float(account.equity)

            contexts: dict[str, object] = {}
            gap = float(cfg.get("request_gap_seconds", 0.3))
            for i, w in enumerate(workers):
                if i and gap:
                    time.sleep(gap)     # разносим запросы, иначе хвост списка
                try:                     # получает пустые ответы от биржи
                    contexts[w.symbol] = w.build_context(account)
                    w.errors = 0
                except StaleDataError as exc:
                    log.warning("%s: %s", w.symbol, exc)
                except Exception as exc:  # noqa: BLE001
                    w.errors += 1
                    log.warning("%s: ошибка данных (%d подряд): %s", w.symbol, w.errors, exc)

            if not contexts:
                stale_streak += 1
                if stale_streak >= 10:
                    log.error("Данные не приходят %d циклов подряд.", stale_streak)
                    stale_streak = 0
                _sleep(poll)
                continue
            stale_streak = 0

            total_exposure = sum(
                (w.exposure(contexts[w.symbol]) for w in workers if w.symbol in contexts),
                Decimal(0))

            for w in workers:
                ctx = contexts.get(w.symbol)
                if ctx is None:
                    continue
                others = total_exposure - w.exposure(ctx)
                for action in w.decide(ctx):
                    try:
                        risk.validate(action, ctx.position, account, ctx.md.mid, others)
                    except RiskReject as exc:
                        log.warning("%s отклонено: %s | %s",
                                    w.symbol, action.describe(), exc)
                        continue
                    w.exchange.execute(action)

            if tick % heartbeat_every == 1:
                delta = float(account.equity) - start_equity
                open_pos = [f"{w.symbol} {contexts[w.symbol].position.side}"
                            for w in workers
                            if w.symbol in contexts and not contexts[w.symbol].position.is_flat]
                log.info("СТАТУС | капитал %.4f USDT (%+.4f за сессию) | "
                         "экспозиция %.2f USDT | позиций %d%s",
                         float(account.equity), delta, float(total_exposure),
                         len(open_pos), f" ({', '.join(open_pos)})" if open_pos else "")
                if dry_run:
                    _log_paper(workers)

        except StaleDataError as exc:
            log.warning("Пропуск цикла: %s", exc)
            _sleep(poll)
            continue
        except FatalExchangeError as exc:
            log.error("НЕИСПРАВИМАЯ ОШИБКА БИРЖИ: %s", exc)
            return 3
        except RiskHalt as exc:
            log.error("ОСТАНОВКА ПО РИСКУ: %s", exc)
            if not dry_run:
                log.error("Снимаю ордера и закрываю позиции по всем символам.")
                for w in workers:
                    try:
                        w.exchange.execute(Action(kind="cancel_all", reason="аварийная остановка"))
                        w.exchange.execute(Action(kind="close", reason="аварийная остановка"))
                    except Exception as exc2:  # noqa: BLE001
                        log.error("%s: не удалось закрыть: %s", w.symbol, exc2)
            return 2
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001 — цикл не должен падать от одной ошибки
            log.exception("Ошибка в цикле: %s", exc)
            _sleep(min(poll * 3, 60))
            continue

        _sleep(poll)

    if dry_run:
        log.info("=" * 60)
        log.info("ИТОГ БУМАЖНОЙ ТОРГОВЛИ ЗА СЕССИЮ")
        _log_paper(workers, final=True)
        log.info("=" * 60)
    log.info("Остановлен штатно.")
    return 0


def _log_paper(workers, final: bool = False) -> None:
    s = aggregate_summary(workers)
    if not s["trades"]:
        log.info("[БУМАГА] сделок пока нет")
        return
    log.info("[БУМАГА] всего сделок %d | винрейт %.0f%% | комиссии %.4f | ИТОГ %+.4f USDT",
             s["trades"], s["win_rate"] * 100, s["fees_usdt"], s["net_usdt"])
    if final:
        for sym, n, net in s["per_symbol"]:
            log.info("           %-12s сделок %3d  итог %+8.4f USDT", sym, n, net)


def main() -> int:
    ap = argparse.ArgumentParser(description="Торговый бот Bybit v5")
    ap.add_argument("--config", default="config.yaml", help="путь к YAML-конфигу")
    ap.add_argument("--live", action="store_true",
                    help="разрешить mainnet; без него testnet принудительно")
    ap.add_argument("--strategy", choices=["signal", "maker", "trend"], help="переопределить стратегию")
    ap.add_argument("--check", action="store_true",
                    help="проверить подключение к бирже (ключи, лоты, часы) и выйти; "
                         "ордера не отправляются")
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

    if args.check:
        # Проверяем именно ту биржу, где пойдёт торговля — основную.
        # Ордеров нет, поэтому подтверждение --live здесь не требуется.
        cfg["testnet"] = False
        return run_check(cfg)

    if not cfg.get("testnet", True) and not args.live:
        log.error("В конфиге testnet: false, но флаг --live не передан. Принудительно testnet.")
        cfg["testnet"] = True

    dry_run = bool(cfg.get("dry_run", True))
    if not cfg.get("testnet", True) and not dry_run:
        confirm_mainnet(cfg)

    if not os.getenv("BYBIT_API_KEY"):
        if not dry_run:
            log.error("Не заданы BYBIT_API_KEY / BYBIT_API_SECRET. "
                      "Скопируйте .env.example в .env")
            return 1
        log.warning("Ключей нет — сухой прогон на публичных данных с условным балансом.")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Второй экземпляр перемешал бы журнал и считал бы риск по половине позиций
    try:
        with single_instance(cfg.get("lock_file", "./bot.lock")):
            return run(cfg, dry_run)
    except AlreadyRunning as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
