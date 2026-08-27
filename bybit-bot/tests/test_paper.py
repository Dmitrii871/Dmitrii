"""Тесты бумажной торговли: тестовый режим должен считать честно."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.models import Action
from bot.paper import PaperTrader


def entry(side="Buy", tp="2500", sl="2440"):
    return Action(kind="market", side=side, qty=Decimal("0.02"),
                  take_profit=Decimal(tp), stop_loss=Decimal(sl))


def test_take_profit_closes_with_gain():
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2501"), Decimal("2460"))
    assert pt.position is None
    assert pt.realized > 0
    assert pt.trades[0]["reason"] == "тейк-профит"


def test_stop_loss_closes_with_loss():
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2470"), Decimal("2439"))
    assert pt.realized < 0
    assert pt.trades[0]["reason"] == "стоп-лосс"


def test_stop_wins_when_candle_touches_both():
    """Консервативно: свеча задела оба уровня — считаем стоп, как в бэктесте."""
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2501"), Decimal("2439"))
    assert pt.trades[0]["reason"] == "стоп-лосс"


def test_short_direction_is_inverted():
    pt = PaperTrader()
    pt.on_action(entry(side="Sell", tp="2400", sl="2490"), Decimal("2463"))
    pt.on_price(Decimal("2470"), Decimal("2399"))       # цена упала — шорт в плюсе
    assert pt.realized > 0
    assert pt.trades[0]["reason"] == "тейк-профит"


def test_fees_charged_on_both_sides():
    """Без комиссии бумажный результат приукрашен и вводит в заблуждение."""
    pt = PaperTrader(taker_bps=5.5)
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2501"), Decimal("2460"))
    t = pt.trades[0]
    expected = (2463 + 2500) * 0.02 * 5.5 / 10_000
    assert abs(t["fees"] - expected) < 1e-6
    assert t["net"] < t["gross"], "комиссия обязана уменьшать результат"


def test_second_entry_ignored_while_position_open():
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_action(entry(), Decimal("2470"))
    assert pt.position.entry == Decimal("2463"), "вторая позиция открываться не должна"


def test_close_action_exits_position():
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_action(Action(kind="close", reason="разворот"), Decimal("2470"))
    assert pt.position is None
    assert pt.trades[0]["reason"] == "решение стратегии"


def test_reduce_only_action_does_not_open_position():
    pt = PaperTrader()
    pt.on_action(Action(kind="market", side="Sell", qty=Decimal("0.02"),
                        reduce_only=True), Decimal("2463"))
    assert pt.position is None


def test_summary_counts_correctly():
    pt = PaperTrader()
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2501"), Decimal("2460"))        # +
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2470"), Decimal("2439"))        # -
    s = pt.summary()
    assert s["trades"] == 2
    assert abs(s["win_rate"] - 0.5) < 1e-9
    assert s["profit_factor"] is not None


def test_summary_on_empty_history():
    s = PaperTrader().summary()
    assert s["trades"] == 0 and s["profit_factor"] is None


def test_journal_written_with_header(tmp_path):
    """Журнал должен объяснять не только что бот сделал, но и почему."""
    path = tmp_path / "j.csv"
    pt = PaperTrader(journal_path=str(path))
    pt.record(Decimal("2463"), {"adx": 31.2, "режим": "momentum", "rsi": 71.0},
              [entry()])
    text = path.read_text(encoding="utf-8")
    assert "режим" in text and "причина" in text, "заголовок обязателен"
    assert "momentum" in text and "31.2" in text


def test_journal_appends_without_duplicating_header(tmp_path):
    path = tmp_path / "j.csv"
    pt = PaperTrader(journal_path=str(path))
    for _ in range(3):
        pt.record(Decimal("2463"), {}, [])
    assert path.read_text(encoding="utf-8").count("итог_usdt") == 1


def test_no_journal_path_is_silent(tmp_path):
    PaperTrader().record(Decimal("1"), {}, [])      # не должно бросить


def test_reduce_only_limit_fills_as_maker():
    """Без этого выход в режиме maker_chase не считался мейкерским.

    В maker_chase стратегия не ставит биржевой тейк-профит, а держит
    reduce-only лимитку. Раньше бумажная позиция закрывалась только по
    стопу или по таймауту, то есть всегда тейкером — и главный критерий
    теста, доля мейкерских выходов, был неизмерим в принципе.
    """
    pt = PaperTrader()
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("0.02")), Decimal("2463"))
    pt.on_action(Action(kind="limit", side="Sell", qty=Decimal("0.02"),
                        price=Decimal("2470"), reduce_only=True, post_only=True),
                 Decimal("2463"))
    pt.on_price(Decimal("2471"), Decimal("2462"))       # свеча прошила лимитку
    assert pt.position is None
    assert pt.trades[0]["maker_exit"] is True
    assert pt.trades[0]["reason"] == "лимитный выход"
    # комиссия обеих сторон по мейкерской ставке
    expected = Decimal("2463") * Decimal("0.02") * Decimal("2") / Decimal(10_000) \
        + Decimal("2470") * Decimal("0.02") * Decimal("2") / Decimal(10_000)
    assert abs(Decimal(str(pt.trades[0]["fees"])) - expected) < Decimal("0.0001")


def test_maker_exit_cheaper_than_taker_exit():
    """Ради этой разницы всё и затевалось: 4.0 bp против 7.5 bp."""
    maker, taker = PaperTrader(), PaperTrader()
    for pt, exit_maker in ((maker, True), (taker, False)):
        pt.on_action(Action(kind="market", side="Buy", qty=Decimal("1")), Decimal("100"))
        if exit_maker:
            pt.on_action(Action(kind="limit", side="Sell", qty=Decimal("1"),
                                price=Decimal("101"), reduce_only=True), Decimal("100"))
            pt.on_price(Decimal("101"), Decimal("100"))
        else:
            pt._close(Decimal("101"), "решение стратегии", maker=False)
    assert maker.realized > taker.realized


def test_cancel_all_removes_pending_exit():
    """Перестановка лимитки: старая цена не должна исполниться задним числом."""
    pt = PaperTrader()
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("1")), Decimal("100"))
    pt.on_action(Action(kind="limit", side="Sell", qty=Decimal("1"),
                        price=Decimal("101"), reduce_only=True), Decimal("100"))
    pt.on_action(Action(kind="cancel_all"), Decimal("100"))
    pt.on_price(Decimal("102"), Decimal("99"))
    assert pt.position is not None, "снятая заявка не может исполниться"


def test_trades_survive_restart(tmp_path):
    """Многодневный тест не должен обнуляться перезапуском бота."""
    path = tmp_path / "BTCUSDT_trades.csv"
    pt = PaperTrader(trades_path=str(path))
    pt.on_action(entry(), Decimal("2463"))
    pt.on_price(Decimal("2501"), Decimal("2460"))

    import csv
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["reason"] == "тейк-профит"

    # второй запуск дописывает, а не затирает
    pt2 = PaperTrader(trades_path=str(path))
    pt2.on_action(entry(), Decimal("2463"))
    pt2.on_price(Decimal("2501"), Decimal("2460"))
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2


def test_maker_share_in_summary():
    pt = PaperTrader()
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("1")), Decimal("100"))
    pt.on_action(Action(kind="limit", side="Sell", qty=Decimal("1"),
                        price=Decimal("101"), reduce_only=True), Decimal("100"))
    pt.on_price(Decimal("101"), Decimal("100"))
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("1")), Decimal("100"))
    pt._close(Decimal("99"), "стоп-лосс", maker=False)
    s = pt.summary()
    assert s["trades"] == 2 and s["maker_exits"] == 1
    assert abs(s["maker_share"] - 0.5) < 1e-9
