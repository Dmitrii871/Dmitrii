"""Риск-контур. Проверяет каждое действие до отправки на биржу.

Разделение намеренное: стратегия думает о прибыли, риск-менеджер —
о том, чтобы депозит пережил серию неудачных решений стратегии.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from pathlib import Path

from .models import Account, Action, Position, RiskHalt, RiskReject

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, cfg: dict):
        self.max_position = Decimal(str(cfg.get("max_position_usdt", 60)))
        self.max_daily_loss = Decimal(str(cfg.get("max_daily_loss_usdt", 5)))
        self.min_free_margin = float(cfg.get("min_free_margin_ratio", 0.25))
        self.max_orders_per_hour = int(cfg.get("max_orders_per_hour", 120))
        self.kill_file = Path(cfg.get("kill_switch_file", "./STOP"))
        self._session_equity: Decimal | None = None
        self._order_times: list[float] = []

    def start_session(self, account: Account) -> None:
        self._session_equity = account.equity
        log.info(
            "Стартовый капитал сессии: %.4f USDT | стоп по убытку: -%.2f USDT",
            float(account.equity), float(self.max_daily_loss),
        )

    # ------------------------------------------------------- проверки цикла
    def check_session(self, account: Account) -> None:
        """Вызывается каждый тик. Бросает RiskHalt — бот обязан остановиться."""
        if self.kill_file.exists():
            raise RiskHalt(f"обнаружен файл-стоп {self.kill_file}")

        if self._session_equity is None:
            self.start_session(account)
            return

        drawdown = self._session_equity - account.equity
        if drawdown >= self.max_daily_loss:
            raise RiskHalt(
                f"достигнут дневной лимит убытка: -{float(drawdown):.4f} USDT "
                f"(лимит {float(self.max_daily_loss)})"
            )
        if account.free_margin_ratio < self.min_free_margin:
            raise RiskHalt(
                f"свободная маржа {account.free_margin_ratio:.1%} ниже порога "
                f"{self.min_free_margin:.0%} — риск ликвидации"
            )

    def validate(self, action: Action, position: Position, account: Account, mid: Decimal) -> None:
        """Бросает RiskReject — отклоняется одно действие, бот продолжает работу."""
        if action.kind == "cancel_all":
            return

        if action.kind in ("limit", "market") and not action.reduce_only:
            self._check_rate_limit()
            add = (action.qty or Decimal(0)) * (action.price or mid)
            # для лимитки в ту же сторону складываем с текущей позицией
            same_side = position.side == action.side
            projected = position.notional() + add if same_side or position.is_flat else abs(position.notional() - add)
            if projected > self.max_position:
                raise RiskReject(
                    f"позиция выросла бы до {float(projected):.2f} USDT при лимите "
                    f"{float(self.max_position)} USDT"
                )
            if account.available <= 0:
                raise RiskReject("нулевой доступный баланс")

    def _check_rate_limit(self) -> None:
        now = time.time()
        self._order_times = [t for t in self._order_times if now - t < 3600]
        if len(self._order_times) >= self.max_orders_per_hour:
            raise RiskReject(
                f"лимит {self.max_orders_per_hour} ордеров в час исчерпан "
                "(защита от разгона комиссий)"
            )
        self._order_times.append(now)
