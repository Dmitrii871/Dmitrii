"""Бумажная торговля для режима dry_run: что было бы, если бы бот торговал.

Без этого сухой прогон отвечает только на вопрос «не падает ли бот».
С этим он отвечает на вопрос «зарабатывал бы он», причём на живых ценах,
но без единого реального ордера.

Ведётся журнал каждого решения в CSV: индикаторы, режим рынка, действие
и его причина. По журналу видно не только результат, но и почему бот
поступил именно так.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import Action

log = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    side: str
    size: Decimal
    entry: Decimal
    take_profit: Decimal | None
    stop_loss: Decimal | None
    opened_at: str
    exit_limit: Decimal | None = None   # выставленная reduce-only лимитка


@dataclass
class PaperTrader:
    """Симуляция позиции на живых ценах.

    Вход считается по цене, которую бот запросил; выход — по take-profit
    или stop-loss, как только цена их задела. Комиссия списывается с обеих
    сторон, иначе результат будет приукрашен.
    """
    maker_bps: float = 2.0
    taker_bps: float = 5.5
    journal_path: str | None = None
    trades_path: str | None = None      # сделки переживают перезапуск бота

    position: PaperPosition | None = None
    trades: list[dict] = field(default_factory=list)
    realized: Decimal = Decimal(0)

    # ------------------------------------------------------------- сделки
    def _fee(self, notional: Decimal, maker: bool) -> Decimal:
        bps = self.maker_bps if maker else self.taker_bps
        return notional * Decimal(str(bps)) / Decimal(10_000)

    def on_action(self, action: Action, price: Decimal) -> None:
        """Бот принял решение — отражаем его в бумажной позиции."""
        if action.kind == "close" and self.position is not None:
            self._close(price, "решение стратегии", maker=False)
            return
        # Выход лимиткой (exit_type: maker_chase). Без этого бумажная позиция
        # закрывалась только по стопу или по таймауту — то есть тейкером,
        # и проверка «доля мейкерских выходов» была невозможна в принципе.
        if action.reduce_only and action.kind == "limit" and self.position is not None:
            self.position.exit_limit = action.price
            return
        if action.kind == "cancel_all":
            if self.position is not None:
                self.position.exit_limit = None
            return
        if action.kind not in ("limit", "market") or action.reduce_only:
            return
        if self.position is not None:
            return                                  # позиция уже открыта
        entry = action.price or price
        self.position = PaperPosition(
            side=action.side or "Buy",
            size=action.qty or Decimal(0),
            entry=entry,
            take_profit=action.take_profit,
            stop_loss=action.stop_loss,
            opened_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        log.info("[БУМАГА] вход %s %s @ %s | TP %s | SL %s",
                 self.position.side, self.position.size, entry,
                 action.take_profit, action.stop_loss)

    def on_price(self, high: Decimal, low: Decimal) -> None:
        """Новая свеча: не выбило ли бумажную позицию по TP или SL."""
        p = self.position
        if p is None:
            return
        if p.side == "Buy":
            hit_sl = p.stop_loss is not None and low <= p.stop_loss
            hit_tp = p.take_profit is not None and high >= p.take_profit
        else:
            hit_sl = p.stop_loss is not None and high >= p.stop_loss
            hit_tp = p.take_profit is not None and low <= p.take_profit
        if p.exit_limit is not None:
            hit_limit = high >= p.exit_limit if p.side == "Buy" else low <= p.exit_limit
        else:
            hit_limit = False
        # консервативно: если свеча задела оба уровня, считаем стоп
        if hit_sl:
            self._close(p.stop_loss, "стоп-лосс", maker=False)
        elif hit_limit:
            self._close(p.exit_limit, "лимитный выход", maker=True)
        elif hit_tp:
            self._close(p.take_profit, "тейк-профит", maker=False)

    def _close(self, exit_price: Decimal, reason: str, maker: bool) -> None:
        p = self.position
        if p is None:
            return
        direction = Decimal(1) if p.side == "Buy" else Decimal(-1)
        gross = (exit_price - p.entry) * direction * p.size
        fees = self._fee(p.entry * p.size, maker) + self._fee(exit_price * p.size, maker)
        net = gross - fees
        self.realized += net
        self.trades.append({
            "side": p.side, "entry": float(p.entry), "exit": float(exit_price),
            "size": float(p.size), "gross": float(gross), "fees": float(fees),
            "net": float(net), "reason": reason, "opened_at": p.opened_at,
            "maker_exit": maker,
            "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        self._append_trade(self.trades[-1])
        log.info("[БУМАГА] выход %s @ %s (%s) | сделка %+.4f USDT | всего %+.4f USDT",
                 p.side, exit_price, reason, float(net), float(self.realized))
        self.position = None

    # ------------------------------------------------------------- отчёт
    def summary(self) -> dict:
        wins = [t for t in self.trades if t["net"] > 0]
        losses = [t for t in self.trades if t["net"] <= 0]
        gross_win = sum(t["net"] for t in wins)
        gross_loss = abs(sum(t["net"] for t in losses))
        return {
            "trades": len(self.trades),
            "net_usdt": float(self.realized),
            "win_rate": len(wins) / len(self.trades) if self.trades else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss else None,
            "fees_usdt": sum(t["fees"] for t in self.trades),
            "open_position": self.position.side if self.position else None,
            "maker_exits": sum(1 for t in self.trades if t.get("maker_exit")),
            "maker_share": (sum(1 for t in self.trades if t.get("maker_exit"))
                            / len(self.trades)) if self.trades else 0.0,
        }

    def log_summary(self) -> None:
        s = self.summary()
        if not s["trades"]:
            log.info("[БУМАГА] сделок пока нет%s",
                     f", позиция открыта: {s['open_position']}" if s["open_position"] else "")
            return
        pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "—"
        log.info("[БУМАГА] сделок %d | винрейт %.0f%% | мейкер-выходы %.0f%% | "
                 "профит-фактор %s | комиссии %.4f | ИТОГ %+.4f USDT",
                 s["trades"], s["win_rate"] * 100, s["maker_share"] * 100,
                 pf, s["fees_usdt"], s["net_usdt"])

    def _append_trade(self, trade: dict) -> None:
        """Сделка на диск сразу после закрытия.

        Список self.trades живёт в памяти: перезапуск бота обнулял бы
        многодневный тест. Файл переживает и перезапуск, и падение.
        """
        if not self.trades_path:
            return
        path = Path(self.trades_path)
        write_header = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(trade))
            if write_header:
                w.writeheader()
            w.writerow(trade)

    # ------------------------------------------------------------ журнал
    def record(self, price: Decimal, snapshot: dict, actions: list[Action]) -> None:
        """Строка журнала на каждый цикл: что бот видел и что решил."""
        if not self.journal_path:
            return
        path = Path(self.journal_path)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "price": float(price),
            "adx": snapshot.get("adx", ""),
            "режим": snapshot.get("режим", ""),
            "rsi": snapshot.get("rsi", ""),
            "macd_hist": snapshot.get("macd_hist", ""),
            "pct_b": snapshot.get("pct_b", ""),
            "голоса_лонг": snapshot.get("голоса_лонг", ""),
            "голоса_шорт": snapshot.get("голоса_шорт", ""),
            "действие": "; ".join(a.describe() for a in actions) or "нет",
            "причина": "; ".join(a.reason for a in actions if a.reason),
            "позиция": self.position.side if self.position else "",
            "итог_usdt": float(self.realized),
        }
        # Заголовок — по состоянию ФАЙЛА, а не по памяти процесса. Флаг
        # «уже писал» переживал удаление файла: бот продолжал дописывать
        # строки без заголовка, и такой CSV не читался ни отчётом, ни Excel.
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            if write_header:
                w.writeheader()
            w.writerow(row)
