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
        if self.paper and md.bar_time != self.last_bar and md.highs and md.lows:
            self.last_bar = md.bar_time
            self.paper.on_price(Decimal(str(md.highs[-1])), Decimal(str(md.lows[-1])))
        return Context(
            md=md,
            position=self.position(),
            account=account,
            instrument=self.instrument(),
            open_orders=self.exchange.open_orders(),
        )

    def decide(self, ctx: Context) -> list[Action]:
        actions = self.strategy.decide(ctx)
        if self.paper:
            snap = getattr(self.strategy, "last_snapshot", {})
            self.paper.record(ctx.md.last, snap, actions)
            for a in actions:
                self.paper.on_action(a, ctx.md.last)
        return actions

    def exposure(self, ctx: Context) -> Decimal:
        return ctx.position.notional()


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

    workers: list[SymbolWorker] = []
    for sym in symbols:
        ex = Exchange({**cfg, "symbol": sym}, api_key, api_secret, dry_run,
                      paper_equity=float(cfg.get("paper_equity", 500)))
        # план подключается только к своему символу
        sym_plan = plan if (plan is not None and plan.symbol == sym) else None
        strat = build(name, scfg, plan=sym_plan) if name == "signal" else build(name, scfg)
        strat.validate(fees)
        paper = PaperTrader(
            maker_bps=float(fees.get("maker_bps", 2.0)),
            taker_bps=float(fees.get("taker_bps", 5.5)),
            journal_path=(f"{sym}_{cfg['journal_file']}" if cfg.get("journal_file") else None),
            trades_path=(f"{sym}_{cfg['trades_file']}" if cfg.get("trades_file") else None),
        ) if dry_run else None
        workers.append(SymbolWorker(
            symbol=sym, exchange=ex, strategy=strat, interval=interval,
            warmup=max(strat.warmup_bars(), 60), paper=paper,
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
