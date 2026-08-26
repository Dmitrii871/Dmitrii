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
