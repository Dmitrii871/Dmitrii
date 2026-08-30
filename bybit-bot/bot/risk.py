"""Риск-контур. Проверяет каждое действие до отправки на биржу.

Разделение намеренное: стратегия думает о прибыли, риск-менеджер —
о том, чтобы депозит пережил серию неудачных решений стратегии.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
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
        self._day_equity: Decimal | None = None
        self._day: str = ""
        self._order_times: list[float] = []
        # Серия убытков подряд — признак того, что режим рынка сменился
        # и стратегия перестала работать. 0 отключает предохранитель.
        self.max_loss_streak = int(cfg.get("max_loss_streak", 5))
        self._loss_streak = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def start_day(self, account: Account) -> None:
        """Отсчёт дневного убытка начинается заново в 00:00 UTC.

        Иначе лимит превращается в лимит "за всё время работы" и бот
        останавливается через неделю на накопленной сумме, а не на дневной.
        """
        self._day = self._today()
        self._day_equity = account.equity
        log.info(
            "Торговый день %s | капитал на начало: %.4f USDT | дневной стоп: -%.2f USDT",
            self._day, float(account.equity), float(self.max_daily_loss),
        )

    def preflight(self, account: Account, max_leverage: int,
                  open_exposure: Decimal = Decimal(0)) -> None:
        """Проверка согласованности лимитов с реальным балансом — до первой сделки.

        Частая ошибка: max_position_usdt задан больше, чем позволяет депозит,
        и бот открывает позицию, после чего немедленно падает по марже.

        open_exposure — нотионал УЖЕ открытых позиций. Их маржа уже списана
        из available, и лимит они уже занимают: перезапуск с открытой
        позицией требовал маржу на весь лимит заново и отказывался
        стартовать, бросая живую позицию без присмотра.
        """
        if account.equity <= 0:
            raise RiskHalt("нулевой капитал на счёте")
        remaining = max(self.max_position - open_exposure, Decimal(0))
        need = remaining / max(Decimal(max_leverage), Decimal(1))
        after = account.available - need
        ratio = float(after / account.equity)
        if ratio < self.min_free_margin:
            safe = (account.available - account.equity * Decimal(str(self.min_free_margin))) \
                * Decimal(max_leverage)
            raise RiskHalt(
                f"max_position_usdt={float(self.max_position):.0f} при плече {max_leverage}x "
                f"требует {float(need):.2f} USDT маржи. После открытия свободной маржи "
                f"осталось бы {ratio:.1%} при пороге {self.min_free_margin:.0%} — "
                f"бот остановился бы сразу после входа. "
                f"Поставьте max_position_usdt не больше {max(float(safe), 0):.0f}."
            )
        log.info(
            "Проверка лимитов пройдена: лимит %.0f USDT (открыто %.0f), "
            "добор требует %.2f USDT маржи, свободной останется %.1f%%",
            float(self.max_position), float(open_exposure), float(need), ratio * 100,
        )

    # ------------------------------------------------------- проверки цикла
    def check_session(self, account: Account) -> None:
        """Вызывается каждый тик. Бросает RiskHalt — бот обязан остановиться."""
        if self.kill_file.exists():
            raise RiskHalt(f"обнаружен файл-стоп {self.kill_file}")

        if self._day_equity is None or self._day != self._today():
            self.start_day(account)
            return

        drawdown = self._day_equity - account.equity
        if drawdown >= self.max_daily_loss:
            raise RiskHalt(
                f"достигнут дневной лимит убытка за {self._day}: "
                f"-{float(drawdown):.4f} USDT (лимит {float(self.max_daily_loss)})"
            )
        if account.free_margin_ratio < self.min_free_margin:
            raise RiskHalt(
                f"свободная маржа {account.free_margin_ratio:.1%} ниже порога "
                f"{self.min_free_margin:.0%} — риск ликвидации"
            )

    def validate(self, action: Action, position: Position, account: Account,
                 mid: Decimal, other_exposure: Decimal = Decimal(0)) -> None:
        """Бросает RiskReject — отклоняется одно действие, бот продолжает работу.

        other_exposure — нотионал позиций по ОСТАЛЬНЫМ символам. Лимит
        max_position_usdt относится к счёту целиком: без этого десять
        символов по лимиту дали бы десятикратный риск от заявленного.
        """
        if action.kind == "cancel_all":
            return

        if action.kind in ("limit", "market") and not action.reduce_only:
            # Сначала все проверки, и только потом расход лимита: отклонённый
            # ордер не должен съедать квоту на реально отправленные.
            self._check_rate_limit(record=False)
            add = (action.qty or Decimal(0)) * (action.price or mid)
            # для лимитки в ту же сторону складываем с текущей позицией
            same_side = position.side == action.side
            own = (position.notional() + add if same_side or position.is_flat
                   else abs(position.notional() - add))
            projected = own + other_exposure
            if projected > self.max_position:
                extra = (f" (в том числе {float(other_exposure):.2f} по другим символам)"
                         if other_exposure > 0 else "")
                raise RiskReject(
                    f"суммарная позиция выросла бы до {float(projected):.2f} USDT "
                    f"при лимите {float(self.max_position)} USDT{extra}"
                )
            if account.available <= 0:
                raise RiskReject("нулевой доступный баланс")
            self._check_rate_limit(record=True)

    def _check_rate_limit(self, record: bool) -> None:
        now = time.time()
        self._order_times = [t for t in self._order_times if now - t < 3600]
        if len(self._order_times) >= self.max_orders_per_hour:
            raise RiskReject(
                f"лимит {self.max_orders_per_hour} ордеров в час исчерпан "
                "(защита от разгона комиссий)"
            )
        if record:
            self._order_times.append(now)

    # ------------------------------------------------- серия убыточных сделок
    def register_trade(self, pnl: Decimal) -> None:
        """Учёт закрытой сделки для предохранителя по серии убытков.

        ВНИМАНИЕ: пока не подключён к торговому циклу — main.py не отслеживает
        закрытые сделки и этот метод не вызывает. Предохранитель заработает
        только после того, как в цикл добавят чтение closed-pnl.
        """
        if pnl < 0:
            self._loss_streak += 1
        else:
            self._loss_streak = 0
        if self.max_loss_streak and self._loss_streak >= self.max_loss_streak:
            raise RiskHalt(
                f"{self._loss_streak} убыточных сделок подряд при лимите "
                f"{self.max_loss_streak}. Рынок не соответствует стратегии — "
                "нужен разбор, а не следующая сделка."
            )
