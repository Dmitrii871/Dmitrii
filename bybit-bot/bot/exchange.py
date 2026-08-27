"""Обёртка над Bybit v5 (pybit.unified_trading).

Отвечает за три вещи, на которых чаще всего ломаются самописные боты:
округление до tick_size / qty_step, повторы при сетевых сбоях
и режим dry_run, в котором ордера логируются, но не отправляются.
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from .models import Account, Action, Instrument, MarketData, Position

log = logging.getLogger(__name__)

# Временные сбои: имеет смысл повторить с задержкой.
RETRYABLE = (
    "timeout", "timed out", "connection", "temporarily", "too many visits",
    "system busy", "service unavailable", "502", "503", "504",
    "10006",            # превышен лимит запросов
    "10016",            # внутренняя ошибка биржи
)

# Повторять бессмысленно и опасно: проблема в ключах или в конфигурации.
# Бот должен остановиться, а не долбить биржу неверной подписью.
FATAL = (
    "10003",  # неверный API-ключ
    "10004",  # неверная подпись
    "10005",  # нет прав у ключа
    "10010",  # IP не в белом списке
    "33004",  # срок действия ключа истёк
    "invalid api key", "api key is invalid", "api_key expire",
    "unmatched ip", "permission denied",
)

# Расхождение часов больше этого — подпись начнёт отвергаться биржей
MAX_CLOCK_DRIFT_MS = 2_000
# Данные старше стольких секунд считаем протухшими и не торгуем по ним
MAX_DATA_AGE_SECONDS = 120


class FatalExchangeError(RuntimeError):
    """Ошибка, которую нельзя исправить повтором: ключи, права, IP."""


class StaleDataError(RuntimeError):
    """Биржа отдала устаревшие данные — торговать по ним нельзя."""


def _d(value: Any, default: str = "0") -> Decimal:
    """Bybit отдаёт числа строками, пустая строка = отсутствие значения."""
    if value in (None, "", "null"):
        return Decimal(default)
    return Decimal(str(value))


def first_or_fail(res: dict, what: str, symbol: str):
    """Первый элемент списка ответа или понятная ошибка.

    Биржа при перегрузе возвращает retCode 0 с ПУСТЫМ списком. Обращение
    к [0] давало 'list index out of range' — сообщение, по которому
    невозможно понять, что произошло.
    """
    items = res.get("list") or []
    if not items:
        raise StaleDataError(
            f"{symbol}: биржа вернула пустой ответ на {what}. "
            "Обычно это лимит запросов — увеличьте poll_seconds "
            "или request_gap_seconds."
        )
    return items[0]


def quantize(value: Decimal, step: Decimal, mode=ROUND_DOWN) -> Decimal:
    """Приводит значение к сетке биржи. Цены — HALF_UP, количества — DOWN."""
    if step <= 0:
        return value
    return (value / step).quantize(Decimal(1), rounding=mode) * step


class Exchange:
    # Значения по умолчанию на уровне класса: объект иногда собирают
    # в обход __init__ (тесты с заглушкой HTTP), и обращение к ним
    # не должно падать.
    public_only: bool = False
    dry_run: bool = False
    public = None

    def __init__(self, cfg: dict, api_key: str, api_secret: str, dry_run: bool,
                 paper_equity: float = 0.0):
        self.symbol: str = cfg["symbol"]
        self.category: str = cfg.get("category", "linear")
        self.testnet: bool = bool(cfg.get("testnet", True))
        self.dry_run = dry_run
        self._instrument: Instrument | None = None
        # Сухой прогон без ключей: свечи и стакан публичные, ордера не уходят,
        # а баланс берётся условный. Так логику можно проверить сразу.
        self.public_only = dry_run and not api_key
        self._paper_equity = Decimal(str(paper_equity or 100))

        from pybit.unified_trading import HTTP  # импорт здесь: тесты не требуют pybit

        self.http = HTTP(
            testnet=self.testnet,
            api_key=api_key,
            api_secret=api_secret,
            recv_window=10_000,
        )
        # Рыночные данные ВСЕГДА с основной сети. На testnet у менее
        # популярных пар котировок нет вовсе, а там где есть — цены
        # синтетические, и проверка стратегии на них ничего не значит.
        # Ордера при этом уходят туда, куда указано в конфиге.
        self.public = HTTP(testnet=False, recv_window=10_000) if self.testnet else self.http
        log.info(
            "Подключение к Bybit %s | символ %s | dry_run=%s%s",
            "TESTNET" if self.testnet else "MAINNET", self.symbol, dry_run,
            " | БЕЗ КЛЮЧЕЙ: только публичные данные, условный баланс "
            f"{float(self._paper_equity):.0f} USDT" if self.public_only else "",
        )
        if self.testnet:
            log.info("%s: котировки и свечи берутся с ОСНОВНОЙ сети — "
                     "на testnet цены синтетические", self.symbol)

    # ---------------------------------------------------------------- вызовы
    def _call_public(self, method: str, **kwargs) -> dict:
        """Публичные данные с основной сети, с той же обработкой ошибок."""
        if self.public is None:
            return self._call(method, **kwargs)
        saved, self.http = self.http, self.public
        try:
            return self._call(method, **kwargs)
        finally:
            self.http = saved

    def _call(self, method: str, **kwargs) -> dict:
        """Вызов API с классификацией ошибок и повтором временных сбоев.

        Три исхода вместо одного: фатальные ошибки (ключи, права, IP)
        поднимаются сразу и останавливают бота; временные повторяются
        с экспоненциальной задержкой и случайным разбросом; остальные
        поднимаются как есть.

        Разброс задержки обязателен: без него несколько ботов или несколько
        повторов синхронизируются и бьются в лимит запросов одновременно.
        """
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                resp = getattr(self.http, method)(**kwargs)
                code = resp.get("retCode")
                if code != 0:
                    raise RuntimeError(f"{method}: {resp.get('retMsg')} (retCode={code})")
                return resp.get("result", {})
            except Exception as exc:  # noqa: BLE001 — классифицируем по тексту и коду
                last_err = exc
                text = str(exc).lower()
                if any(m in text for m in FATAL):
                    raise FatalExchangeError(
                        f"{method}: {exc}\n"
                        "Проверьте: ключ от того же контура (testnet/mainnet), "
                        "права Trade включены, ваш IP в белом списке ключа."
                    ) from exc
                if not any(m in text for m in RETRYABLE) or attempt == 3:
                    raise
                delay = 2 ** attempt + random.uniform(0, 1)
                log.warning("%s: %s — повтор через %.1fs", method, exc, delay)
                time.sleep(delay)
        raise RuntimeError(f"{method} не удался") from last_err

    def check_clock(self) -> float:
        if self.public_only:
            return 0.0
        """Расхождение локальных часов с биржей.

        Bybit отвергает запросы, чей timestamp вне recv_window. Часы VPS
        уходят на секунды в сутки, и ошибка приходит как "invalid signature",
        которая про часы не говорит ни слова — поэтому проверяем явно.
        """
        local_before = time.time() * 1000
        res = self._call_public("get_server_time")
        local_after = time.time() * 1000
        server_ms = float(res.get("timeNano", 0)) / 1e6 or float(res.get("timeSecond", 0)) * 1000
        drift = server_ms - (local_before + local_after) / 2
        if abs(drift) > MAX_CLOCK_DRIFT_MS:
            raise FatalExchangeError(
                f"Часы разошлись с биржей на {drift:.0f} мс (порог {MAX_CLOCK_DRIFT_MS}). "
                "Биржа начнёт отвергать подписи. Синхронизируйте время: "
                "sudo timedatectl set-ntp true"
            )
        log.info("Расхождение часов с биржей: %.0f мс — в норме", drift)
        return drift

    # ------------------------------------------------------------ справочник
    def instrument(self) -> Instrument:
        if self._instrument is not None:
            return self._instrument
        res = self._call_public("get_instruments_info", category=self.category, symbol=self.symbol)
        item = first_or_fail(res, "справочник инструментов", self.symbol)
        lot, price = item["lotSizeFilter"], item["priceFilter"]
        self._instrument = Instrument(
            symbol=self.symbol,
            tick_size=_d(price["tickSize"]),
            qty_step=_d(lot["qtyStep"]),
            min_qty=_d(lot["minOrderQty"]),
            min_notional=_d(lot.get("minNotionalValue"), "5"),
        )
        log.info(
            "Фильтры %s: tick=%s, шаг=%s, мин.кол-во=%s, мин.нотионал=%s USDT",
            self.symbol, self._instrument.tick_size, self._instrument.qty_step,
            self._instrument.min_qty, self._instrument.min_notional,
        )
        return self._instrument

    def check_position_mode(self) -> str:
        if self.public_only:
            return "one-way"
        """Бот рассчитан на односторонний режим (One-Way).

        В хедж-режиме Bybit требует positionIdx у каждого ордера, иначе
        заявки отклоняются. Молча торговать в таком режиме нельзя, поэтому
        несовпадение — это ошибка запуска, а не предупреждение.
        """
        items = self._call("get_positions", category=self.category, symbol=self.symbol).get("list", [])
        idx = {int(p.get("positionIdx", 0)) for p in items}
        if idx - {0}:
            raise RuntimeError(
                f"Символ {self.symbol} в хедж-режиме (positionIdx={sorted(idx)}). "
                "Бот работает в одностороннем режиме: переключите позиционный режим "
                "на Bybit в 'Односторонний' (One-Way) для этого символа."
            )
        log.info("Режим позиций: односторонний (лонг и шорт по очереди, не одновременно)")
        return "one-way"

    def set_leverage(self, leverage: int) -> None:
        try:
            self._call(
                "set_leverage", category=self.category, symbol=self.symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage),
            )
            log.info("Плечо установлено: %sx", leverage)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # "not modified" — значение уже такое, это не ошибка.
            # С открытой позицией Bybit менять плечо не даёт — тоже не повод падать:
            # работаем с тем, что есть, но говорим об этом громко.
            if "not modified" in msg or "110043" in msg:
                log.info("Плечо уже %sx", leverage)
            elif "position" in msg or "110026" in msg:
                log.warning(
                    "Плечо не изменено на %sx: есть открытая позиция. "
                    "Бот продолжит с текущим плечом — закройте позицию и "
                    "перезапустите, если нужно именно %sx.", leverage, leverage,
                )
            else:
                raise

    # ------------------------------------------------------------- состояние
    def market_data(self, interval: str, bars: int = 200) -> MarketData:
        tick = first_or_fail(
            self._call_public("get_tickers", category=self.category, symbol=self.symbol),
            "котировки", self.symbol)
        kl = self._call(
            "get_kline", category=self.category, symbol=self.symbol,
            interval=interval, limit=min(bars, 1000),
        )["list"]
        # Bybit отдаёт свечи от новых к старым; последняя ещё не закрыта — отбрасываем
        rows = list(reversed(kl))[:-1]
        if not rows:
            raise StaleDataError("биржа не вернула ни одной закрытой свечи")

        md = MarketData(
            symbol=self.symbol,
            bid=_d(tick["bid1Price"]),
            ask=_d(tick["ask1Price"]),
            last=_d(tick["lastPrice"]),
            closes=[float(r[4]) for r in rows],
            highs=[float(r[2]) for r in rows],
            lows=[float(r[3]) for r in rows],
            bar_time=int(rows[-1][0]),
        )
        self._assert_fresh(md, interval)
        if md.bid <= 0 or md.ask <= 0 or md.ask <= md.bid:
            raise StaleDataError(f"некорректная книга: бид {md.bid}, аск {md.ask}")
        return md

    @staticmethod
    def _interval_ms(interval: str) -> int:
        table = {"D": 86_400_000, "W": 604_800_000, "M": 2_592_000_000}
        return table.get(interval.upper(), 0) or int(interval) * 60_000

    def _assert_fresh(self, md: MarketData, interval: str) -> None:
        """Поток данных может тихо застыть: ошибки нет, а цена часовой давности.

        Проверяем возраст последней закрытой свечи. Нормальный возраст —
        меньше одного интервала (мы внутри текущей формирующейся свечи).
        """
        step = self._interval_ms(interval)
        close_time = md.bar_time + step
        age_s = (time.time() * 1000 - close_time) / 1000
        limit_s = step / 1000 + MAX_DATA_AGE_SECONDS
        if age_s > limit_s:
            raise StaleDataError(
                f"данные устарели: последняя закрытая свеча старше "
                f"{age_s / 60:.1f} мин при допуске {limit_s / 60:.1f} мин. "
                "Торговля приостановлена до восстановления потока."
            )

    def position(self) -> Position:
        if self.public_only:
            return Position(self.symbol, "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        res = self._call("get_positions", category=self.category, symbol=self.symbol)
        items = res.get("list", [])
        if not items:
            return Position(self.symbol, "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        p = items[0]
        size = _d(p.get("size"))
        return Position(
            symbol=self.symbol,
            side=p.get("side", "") if size > 0 else "",
            size=size,
            entry_price=_d(p.get("avgPrice")),
            unrealised_pnl=_d(p.get("unrealisedPnl")),
            mark_price=_d(p.get("markPrice")),
        )

    def account(self) -> Account:
        if self.public_only:
            return Account(equity=self._paper_equity, available=self._paper_equity)
        res = self._call("get_wallet_balance", accountType="UNIFIED")
        acc = first_or_fail(res, "баланс счёта", self.symbol)
        return Account(
            equity=_d(acc.get("totalEquity")),
            available=_d(acc.get("totalAvailableBalance")),
        )

    def open_orders(self) -> list[dict]:
        if self.public_only:
            return []
        return self._call("get_open_orders", category=self.category, symbol=self.symbol).get("list", [])

    # --------------------------------------------------------------- ордера
    def execute(self, action: Action) -> None:
        if action.kind == "cancel_all":
            self._cancel_all(action)
        elif action.kind == "close":
            self._close(action)
        else:
            self._place(action)

    def _cancel_all(self, action: Action) -> None:
        if self.dry_run:
            log.info("[DRY] снять все ордера — %s", action.reason)
            return
        self._call("cancel_all_orders", category=self.category, symbol=self.symbol)
        log.info("Все ордера сняты — %s", action.reason)

    def _close(self, action: Action) -> None:
        pos = self.position()
        if pos.is_flat:
            return
        side = "Sell" if pos.side == "Buy" else "Buy"
        self._place(Action(
            kind="market", side=side, qty=pos.size,
            reduce_only=True, reason=action.reason or "закрытие позиции",
        ))

    def _order_exists(self, link_id: str) -> bool:
        """Долетел ли ордер до биржи. Спрашиваем по своему идентификатору."""
        for method in ("get_open_orders", "get_order_history"):
            try:
                res = self._call(method, category=self.category,
                                 symbol=self.symbol, orderLinkId=link_id)
                if res.get("list"):
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Сверка через %s не удалась: %s", method, exc)
        return False

    def _place(self, action: Action) -> None:
        inst = self.instrument()
        raw = action.qty or Decimal(0)

        if action.reduce_only:
            # Закрытие позиции НЕ фильтруется минимальным лотом и минимальной
            # суммой: биржа разрешает закрыть остаток любого размера, а вот
            # округление вниз превратило бы 0.005 в ноль и заперло бы позицию.
            qty = quantize(raw, inst.qty_step, ROUND_DOWN)
            if qty <= 0:
                qty = raw           # остаток меньше шага — отдаём как есть
            if qty <= 0:
                log.warning("Нечего закрывать: количество %s", raw)
                return
        else:
            qty = quantize(raw, inst.qty_step, ROUND_DOWN)
            if qty < inst.min_qty:
                log.warning(
                    "Ордер пропущен: количество %s меньше минимума %s", qty, inst.min_qty
                )
                return
            # Второй фильтр биржи помимо минимального количества: минимальная
            # сумма. Без проверки ордер уйдёт и вернётся отказом.
            notional = qty * (action.price or self.market_price())
            if notional < inst.min_notional:
                log.warning(
                    "Ордер пропущен: сумма %.2f USDT меньше минимума %.2f USDT",
                    float(notional), float(inst.min_notional),
                )
                return

        params: dict[str, Any] = {
            "category": self.category,
            "symbol": self.symbol,
            "side": action.side,
            "orderType": "Limit" if action.kind == "limit" else "Market",
            "qty": str(qty),
            "reduceOnly": action.reduce_only,
        }
        if action.kind == "limit":
            price = quantize(action.price, inst.tick_size, ROUND_HALF_UP)
            params["price"] = str(price)
            params["timeInForce"] = "PostOnly" if action.post_only else "GTC"
        if action.take_profit is not None:
            params["takeProfit"] = str(quantize(action.take_profit, inst.tick_size, ROUND_HALF_UP))
        if action.stop_loss is not None:
            params["stopLoss"] = str(quantize(action.stop_loss, inst.tick_size, ROUND_HALF_UP))

        # Свой идентификатор ордера — защита от дублей. Если ответ биржи
        # потерялся в сети, повторная отправка с тем же orderLinkId будет
        # отклонена как дубликат, а не создаст вторую позицию.
        link_id = f"bot-{uuid.uuid4().hex[:24]}"
        params["orderLinkId"] = link_id

        if self.dry_run:
            log.info("[DRY] %s | %s", action.describe(), action.reason)
            return

        try:
            res = self._place_once(params)
            log.info("Ордер %s: %s | %s", res.get("orderId", "?"),
                     action.describe(), action.reason)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # PostOnly, который сразу стал бы тейкером, биржа отклоняет — это норма
            if "post only" in msg or "would immediately match" in msg:
                log.debug("PostOnly отклонён (стал бы тейкером): %s", action.describe())
                return
            if "duplicate" in msg or "orderlinkid" in msg:
                log.info("Ордер уже был принят биржей (дубликат %s) — повтор не нужен", link_id)
                return
            raise

    def _place_once(self, params: dict) -> dict:
        """Отправка ордера без слепого повтора.

        Обычный ретрай здесь опасен: на таймауте ордер мог уже исполниться,
        и повтор открыл бы вторую позицию. Поэтому при неясном сбое сначала
        спрашиваем биржу, долетел ли ордер, и только потом решаем.
        """
        link_id = params["orderLinkId"]
        try:
            resp = self.http.place_order(**params)
            if resp.get("retCode") != 0:
                raise RuntimeError(
                    f"place_order: {resp.get('retMsg')} (retCode={resp.get('retCode')})")
            return resp.get("result", {})
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if any(m in text for m in FATAL):
                raise FatalExchangeError(f"place_order: {exc}") from exc
            if not any(m in text for m in RETRYABLE):
                raise
            log.warning("Ордер отправлен, ответ не получен (%s). Сверяюсь с биржей...", exc)
            time.sleep(2)
            if self._order_exists(link_id):
                log.info("Ордер %s всё-таки принят биржей — повтор не нужен", link_id)
                return {"orderId": "reconciled", "orderLinkId": link_id}
            log.info("Ордер %s до биржи не долетел — отправляю повторно", link_id)
            resp = self.http.place_order(**params)
            if resp.get("retCode") != 0:
                raise RuntimeError(
                    f"place_order (повтор): {resp.get('retMsg')} "
                    f"(retCode={resp.get('retCode')})") from exc
            return resp.get("result", {})

    def market_price(self) -> Decimal:
        tick = first_or_fail(
            self._call_public("get_tickers", category=self.category, symbol=self.symbol),
            "котировки", self.symbol)
        return _d(tick["lastPrice"])
