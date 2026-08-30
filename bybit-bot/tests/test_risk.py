"""Тесты риск-контура: лимиты должны срабатывать до потери денег."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.models import Account, Action, Position, RiskHalt, RiskReject
from bot.risk import RiskManager

ACCOUNT = Account(Decimal("40.2734"), Decimal("23.8184"))
FLAT = Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))
MID = Decimal("2463")


def rm(**over):
    cfg = {"max_position_usdt": 30, "max_daily_loss_usdt": 2.0,
           "min_free_margin_ratio": 0.25, "max_orders_per_hour": 120,
           "kill_switch_file": "./__no_such_stop__"}
    cfg.update(over)
    return RiskManager(cfg)


def test_preflight_rejects_position_larger_than_margin_allows():
    try:
        rm(max_position_usdt=60).preflight(ACCOUNT, 3)
    except RiskHalt as exc:
        assert "max_position_usdt" in str(exc)
        assert "не больше" in str(exc), "ошибка должна подсказывать верное значение"
    else:
        raise AssertionError("позиция 60 USDT при плече 3x должна быть отклонена")


def test_preflight_accepts_sane_position():
    rm(max_position_usdt=30).preflight(ACCOUNT, 3)


def test_preflight_rejects_empty_account():
    try:
        rm().preflight(Account(Decimal(0), Decimal(0)), 3)
    except RiskHalt as exc:
        assert "капитал" in str(exc)
    else:
        raise AssertionError("нулевой капитал должен отклоняться")


def test_daily_loss_halts_bot():
    r = rm()
    r.check_session(ACCOUNT)                       # первый вызов задаёт точку отсчёта
    try:
        r.check_session(Account(Decimal("38.0"), Decimal("20.0")))   # -2.27 USDT
    except RiskHalt as exc:
        assert "дневной лимит убытка" in str(exc)
    else:
        raise AssertionError("просадка больше лимита должна останавливать бота")


def test_small_loss_does_not_halt():
    r = rm()
    r.check_session(ACCOUNT)
    r.check_session(Account(Decimal("39.5"), Decimal("22.0")))       # -0.77 USDT


def test_daily_counter_resets_on_new_utc_day():
    """Лимит дневной, а не за всё время работы: новый день — новая точка отсчёта."""
    r = rm()
    r.check_session(ACCOUNT)
    r._day = "1999-01-01"                          # имитируем наступление нового дня
    r.check_session(Account(Decimal("30.0"), Decimal("15.0")))       # -10 USDT, но день новый
    assert r._day_equity == Decimal("30.0")


def test_low_free_margin_halts():
    r = rm()
    r.check_session(ACCOUNT)
    try:
        r.check_session(Account(Decimal("40.0"), Decimal("2.0")))    # 5% свободной маржи
    except RiskHalt as exc:
        assert "свободная маржа" in str(exc)
    else:
        raise AssertionError("низкая свободная маржа должна останавливать бота")


def test_kill_switch_file_halts(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("")
    try:
        rm(kill_switch_file=str(stop)).check_session(ACCOUNT)
    except RiskHalt as exc:
        assert "файл-стоп" in str(exc)
    else:
        raise AssertionError("файл STOP должен останавливать бота")


def test_order_exceeding_position_limit_rejected():
    action = Action(kind="market", side="Buy", qty=Decimal("0.05"), price=MID)  # ~123 USDT
    try:
        rm().validate(action, FLAT, ACCOUNT, MID)
    except RiskReject as exc:
        assert "лимите" in str(exc)
    else:
        raise AssertionError("ордер сверх max_position должен отклоняться")


def test_order_within_limit_accepted():
    rm().validate(Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID),
                  FLAT, ACCOUNT, MID)


def test_reduce_only_order_bypasses_position_limit():
    """Закрытие позиции нельзя блокировать лимитом — иначе выйти будет нельзя."""
    big = Position("ETHUSDT", "Buy", Decimal("0.05"), MID, Decimal(0), MID)
    rm().validate(Action(kind="market", side="Sell", qty=Decimal("0.05"),
                         price=MID, reduce_only=True), big, ACCOUNT, MID)


def test_cancel_all_always_allowed():
    rm().validate(Action(kind="cancel_all"), FLAT, ACCOUNT, MID)


def test_hourly_order_cap_enforced():
    r = rm(max_orders_per_hour=3)
    small = Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID)
    for _ in range(3):
        r.validate(small, FLAT, ACCOUNT, MID)
    try:
        r.validate(small, FLAT, ACCOUNT, MID)
    except RiskReject as exc:
        assert "в час" in str(exc)
    else:
        raise AssertionError("лимит ордеров в час должен срабатывать")


# ---------------------------------------------------------------- защита биржевого слоя
def test_stale_data_detected():
    """Поток данных может застыть молча: ошибки нет, а цена часовой давности."""
    import time as _t
    from bot.exchange import Exchange, StaleDataError
    from bot.models import MarketData

    ex = Exchange.__new__(Exchange)          # без сети
    old_bar = int((_t.time() - 3 * 3600) * 1000)
    md = MarketData("ETHUSDT", Decimal("2463"), Decimal("2464"),
                    Decimal("2463"), [1.0], bar_time=old_bar)
    try:
        ex._assert_fresh(md, "60")
    except StaleDataError as exc:
        assert "устарели" in str(exc)
    else:
        raise AssertionError("свеча трёхчасовой давности должна считаться протухшей")


def test_fresh_data_accepted():
    import time as _t
    from bot.exchange import Exchange
    from bot.models import MarketData

    ex = Exchange.__new__(Exchange)
    recent = int((_t.time() - 600) * 1000)   # закрылась 10 минут назад
    ex._assert_fresh(MarketData("ETHUSDT", Decimal("2463"), Decimal("2464"),
                                Decimal("2463"), [1.0], bar_time=recent), "60")


def test_interval_ms_parsing():
    from bot.exchange import Exchange
    assert Exchange._interval_ms("3") == 180_000
    assert Exchange._interval_ms("60") == 3_600_000
    assert Exchange._interval_ms("D") == 86_400_000


def test_quantize_never_rounds_qty_up():
    """Округление количества вверх = ордер больше, чем позволяет депозит."""
    from bot.exchange import quantize
    assert quantize(Decimal("0.02749"), Decimal("0.01")) == Decimal("0.02")
    assert quantize(Decimal("0.00999"), Decimal("0.01")) == Decimal("0.00")


def test_fatal_and_retryable_codes_do_not_overlap():
    """Фатальную ошибку нельзя повторять, временную нельзя считать фатальной."""
    from bot.exchange import FATAL, RETRYABLE
    assert not (set(FATAL) & set(RETRYABLE))
    assert "10006" in RETRYABLE, "лимит запросов повторяется"
    assert "10004" in FATAL, "неверная подпись не лечится повтором"


def test_preflight_counts_open_positions_toward_limit():
    """Перезапуск с открытой позицией: маржа нужна только на ДОБОР.

    Проверка на весь лимит заново отказывалась стартовать и бросала
    живую позицию на бирже без присмотра.
    """
    from decimal import Decimal

    from bot.models import Account, RiskHalt
    from bot.risk import RiskManager

    rm = RiskManager({"max_position_usdt": 25, "max_daily_loss_usdt": 1})
    acc = Account(equity=Decimal("41"), available=Decimal("29"))

    # без учёта открытых 12 USDT — отказ...
    try:
        rm.preflight(acc, 1)
        blocked = False
    except RiskHalt:
        blocked = True
    assert blocked, "полный лимит 25 при свободных 29 обязан не пройти порог 25%"

    # ...а с учётом — добор всего 13 USDT, и это проходит
    rm.preflight(acc, 1, open_exposure=Decimal("12"))
