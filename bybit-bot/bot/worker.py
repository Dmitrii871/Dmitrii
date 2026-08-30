"""Один символ: своя биржа, своя стратегия, своя бумажная позиция.

Мультисимвольный режим нужен не ради объёма, а ради СКОРОСТИ проверки.
На одном инструменте вход случается примерно раз в 20 часов, и тридцать
сделок — минимум для оценки винрейта — набираются месяц. На десяти
символах те же тридцать сделок набегают за два-три дня, и это заодно
проверка устойчивости: закономерность обязана работать не на одном рынке.

Риск-менеджер при этом ОДИН на все символы: лимиты считаются по счёту,
а не по инструменту, иначе десять позиций по лимиту дадут десятикратный
риск вместо заявленного.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
import time
from datetime import datetime, timezone
from decimal import Decimal

from .exchange import Exchange
from .models import Action, Position
from .paper import PaperTrader
from .strategies import build
from .strategies.base import Context

log = logging.getLogger(__name__)


@dataclass
class SymbolWorker:
    symbol: str
    exchange: Exchange
    strategy: object
    interval: str
    warmup: int
    paper: PaperTrader | None = None
    simulate: bool = True   # False = боевой режим: журнал пишем, сделки не имитируем
    _pnl_after_ms: int = 0  # закрытия с биржи старше этой метки уже записаны
    last_bar: int = 0
    errors: int = 0
    _instrument: object = field(default=None, repr=False)

    def instrument(self):
        if self._instrument is None:
            self._instrument = self.exchange.instrument()
        return self._instrument

    def position(self) -> Position:
        """Позиция берётся с биржи, а в бумажном режиме — из симуляции."""
        if self.paper and self.exchange.public_only:
            p = self.paper.position
            if p is None:
                return Position(self.symbol, "", Decimal(0), Decimal(0),
                                Decimal(0), Decimal(0))
            return Position(self.symbol, p.side, p.size, p.entry, Decimal(0), p.entry)
        return self.exchange.position()

    def build_context(self, account) -> Context:
        md = self.exchange.market_data(self.interval, self.warmup)
        if not self.simulate:
            self._collect_live_closes()
        if self.paper and self.simulate and md.bar_time != self.last_bar and md.highs and md.lows:
            self.last_bar = md.bar_time
            self.paper.on_price(Decimal(str(md.highs[-1])), Decimal(str(md.lows[-1])))
        return Context(
            md=md,
            position=self.position(),
            account=account,
            instrument=self.instrument(),
            open_orders=self.exchange.open_orders(),
        )

    def _collect_live_closes(self) -> None:
        """Закрытые боевые сделки — с биржи в файл сделок.

        В live симуляции нет, и без этого статус вечно показывал
        «СДЕЛОК: 0» при реально закрытых позициях. Биржа — единственный
        честный источник: её цены включают проскальзывание, closedPnl —
        комиссии.
        """
        if self.paper is None or not self.paper.trades_path:
            return
        if self._pnl_after_ms == 0:
            # первая инициализация: древнюю историю не тащим
            self._pnl_after_ms = int(time.time() * 1000) - 60_000
        try:
            rows = self.exchange.closed_pnl(limit=10)
        except Exception as exc:  # noqa: BLE001 — статистика не должна ронять торговлю
            log.warning("%s: не удалось получить закрытые сделки: %s", self.symbol, exc)
            return
        fresh = sorted((r for r in rows
                        if int(r.get("updatedTime") or r.get("createdTime") or 0)
                        > self._pnl_after_ms),
                       key=lambda r: int(r.get("updatedTime") or r.get("createdTime") or 0))
        for r in fresh:
            ts = int(r.get("updatedTime") or r.get("createdTime") or 0)
            self._pnl_after_ms = max(self._pnl_after_ms, ts)
            closing_side = r.get("side", "")
            pos_side = "Buy" if closing_side == "Sell" else "Sell"
            trade = {
                "side": pos_side,
                "entry": r.get("avgEntryPrice", ""),
                "exit": r.get("avgExitPrice", ""),
                "size": r.get("qty", ""),
                "gross": "",
                "fees": "",
                "net": r.get("closedPnl", ""),
                "reason": "закрытие на бирже",
                "opened_at": "",
                "maker_exit": False,
                "closed_at": datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                                     .isoformat(timespec="seconds"),
            }
            self.paper._append_trade(trade)  # noqa: SLF001 — общий формат файла сделок
            log.info("[БИРЖА] %s закрыта %s: вход %s выход %s итог %s USDT",
                     self.symbol, pos_side, trade["entry"], trade["exit"], trade["net"])

    def decide(self, ctx: Context) -> list[Action]:
        actions = self.strategy.decide(ctx)
        if self.paper:
            snap = getattr(self.strategy, "last_snapshot", {})
            # В боевом режиме в колонку «позиция» идёт РЕАЛЬНАЯ позиция с биржи:
            # бумажной нет, а пустая колонка прятала бы открытую позицию.
            side = None if self.simulate else ctx.position.side
            self.paper.record(ctx.md.last, snap, actions, position_side=side)
            if self.simulate:
                for a in actions:
                    self.paper.on_action(a, ctx.md.last)
        return actions

    def exposure(self, ctx: Context) -> Decimal:
        return ctx.position.notional()


def warmup_for(strat) -> int:
    """Сколько свечей запрашивать у биржи для этой стратегии.

    Обязательно с запасом над warmup_bars(): последняя свеча ещё не
    закрыта и отбрасывается. Запрос ровно warmup_bars() давал стратегии
    на бар меньше необходимого, и она каждый цикл МОЛЧА выходила по
    «мало данных» — бот неделями выглядел работающим, не посчитав
    ни одного сигнала.
    """
    return max(strat.warmup_bars() + 5, 60)


def make_workers(cfg: dict, api_key: str, api_secret: str, dry_run: bool,
                 plan=None) -> list[SymbolWorker]:
    """Список символов из конфига; одиночный symbol тоже поддерживается."""
    symbols = cfg.get("symbols") or [cfg["symbol"]]
    if isinstance(symbols, str):
        symbols = [symbols]
    strat_cfg = cfg["strategy"]
    name = strat_cfg["name"]
    scfg = strat_cfg.get(name, {})
    interval = scfg.get("interval", "60")
    fees = cfg.get("fees", {})

    overrides = scfg.get("notional_overrides") or {}
    workers: list[SymbolWorker] = []
    for sym in symbols:
        sym_scfg = scfg
        if sym in overrides:
            # у BTC/ETH минимальный лот больше стандартной позиции —
            # размер задаётся на символ, стратегия своя на каждый символ
            sym_scfg = {**scfg, "order_notional_usdt": overrides[sym]}
        ex = Exchange({**cfg, "symbol": sym}, api_key, api_secret, dry_run,
                      paper_equity=float(cfg.get("paper_equity", 500)))
        # план подключается только к своему символу
        sym_plan = plan if (plan is not None and plan.symbol == sym) else None
        strat = build(name, sym_scfg, plan=sym_plan) if name == "signal" else build(name, sym_scfg)
        strat.validate(fees)
        paper = PaperTrader(
            maker_bps=float(fees.get("maker_bps", 2.0)),
            taker_bps=float(fees.get("taker_bps", 5.5)),
            # Имена файлов с умолчаниями: параметры появились в примере
            # конфига позже, чем у пользователей появились свои копии.
            # Без умолчания сделки честно совершались, но не писались на
            # диск — тест выглядел пустым при работающей стратегии.
            journal_path=f"{sym}_{cfg.get('journal_file', 'journal.csv')}",
            # Файл сделок — только про симуляцию; в боевом режиме сделки
            # живут на бирже, а журнал решений ведётся в обоих режимах:
            # раньше paper создавался лишь при dry_run, и боевой бот летел
            # вслепую — status.sh показывал вечно устаревшие файлы.
            trades_path=f"{sym}_{cfg.get('trades_file', 'trades.csv')}",
        )
        workers.append(SymbolWorker(
            symbol=sym, exchange=ex, strategy=strat, interval=interval,
            warmup=warmup_for(strat), paper=paper, simulate=dry_run,
        ))
    log.info("Символов в работе: %d — %s", len(workers), ", ".join(symbols))
    return workers


def aggregate_summary(workers: list[SymbolWorker]) -> dict:
    """Сводка бумажной торговли по всем символам сразу."""
    trades, net, fees, wins = 0, 0.0, 0.0, 0
    per_symbol = []
    for w in workers:
        if not w.paper:
            continue
        s = w.paper.summary()
        trades += s["trades"]
        net += s["net_usdt"]
        fees += s["fees_usdt"]
        wins += round(s["win_rate"] * s["trades"])
        if s["trades"]:
            per_symbol.append((w.symbol, s["trades"], s["net_usdt"]))
    return {
        "trades": trades,
        "net_usdt": net,
        "fees_usdt": fees,
        "win_rate": wins / trades if trades else 0.0,
        "per_symbol": sorted(per_symbol, key=lambda x: -x[2]),
    }
