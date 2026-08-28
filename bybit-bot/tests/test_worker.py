"""Тесты мультисимвольного режима."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.models import Account, Action, Position, RiskReject
from bot.paper import PaperTrader
from bot.risk import RiskManager
from bot.worker import SymbolWorker, aggregate_summary

ACCOUNT = Account(Decimal("500"), Decimal("500"))
MID = Decimal("2463")


def rm(**over):
    cfg = {"max_position_usdt": 30, "max_daily_loss_usdt": 5.0,
           "min_free_margin_ratio": 0.25, "max_orders_per_hour": 500,
           "kill_switch_file": "./__no_stop__"}
    cfg.update(over)
    return RiskManager(cfg)


FLAT = Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def test_limit_applies_to_whole_account_not_per_symbol():
    """Десять символов по лимиту дали бы десятикратный риск — этого быть не должно."""
    action = Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID)  # ~25 USDT
    r = rm(max_position_usdt=30)
    r.validate(action, FLAT, ACCOUNT, MID, other_exposure=Decimal(0))   # проходит
    try:
        r.validate(action, FLAT, ACCOUNT, MID, other_exposure=Decimal(20))
    except RiskReject as exc:
        assert "другим символам" in str(exc)
    else:
        raise AssertionError("экспозиция по другим символам обязана учитываться")


def test_other_exposure_defaults_to_zero():
    """Одиночный режим не должен ломаться от нового параметра."""
    rm().validate(Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID),
                  FLAT, ACCOUNT, MID)


def _worker(sym, trades):
    pt = PaperTrader()
    for net in trades:
        pt.trades.append({"side": "Buy", "entry": 1.0, "exit": 1.0, "size": 1.0,
                          "gross": net, "fees": 0.05, "net": net,
                          "reason": "тест", "opened_at": ""})
        pt.realized += Decimal(str(net))
    return SymbolWorker(symbol=sym, exchange=None, strategy=None,
                        interval="60", warmup=60, paper=pt)


def test_aggregate_sums_across_symbols():
    ws = [_worker("ETHUSDT", [1.0, -0.5]), _worker("BTCUSDT", [2.0])]
    s = aggregate_summary(ws)
    assert s["trades"] == 3
    assert abs(s["net_usdt"] - 2.5) < 1e-9
    assert s["per_symbol"][0][0] == "BTCUSDT", "сортировка по результату"


def test_aggregate_handles_symbols_without_trades():
    s = aggregate_summary([_worker("ETHUSDT", []), _worker("BTCUSDT", [1.0])])
    assert s["trades"] == 1
    assert len(s["per_symbol"]) == 1, "символы без сделок в разбивку не попадают"


def test_aggregate_on_empty_list():
    assert aggregate_summary([])["trades"] == 0


def test_worker_reports_paper_position_when_keyless():
    """Без ключей позиция берётся из симуляции, а не с биржи."""
    class FakeEx:
        public_only = True

    pt = PaperTrader()
    w = SymbolWorker("ETHUSDT", FakeEx(), None, "60", 60, paper=pt)
    assert w.position().is_flat
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("0.02"),
                        take_profit=Decimal("2500"), stop_loss=Decimal("2400")),
                 Decimal("2463"))
    pos = w.position()
    assert pos.side == "Buy" and pos.size == Decimal("0.02")


def test_worker_requests_more_bars_than_warmup():
    """Запас над warmup обязателен: последняя свеча отбрасывается.

    Запрос ровно warmup_bars() давал стратегии на бар меньше нужного,
    и она каждый цикл молча выходила по «мало данных» — бот неделями
    выглядел работающим, не посчитав ни одного сигнала.
    """
    from bot.strategies import build
    from bot.worker import warmup_for

    for name, scfg in (("signal", {}), ("maker", {})):
        strat = build(name, scfg)
        assert warmup_for(strat) > strat.warmup_bars(), (
            f"{name}: после отброса незакрытой свечи стратегии не хватит истории")


def test_signal_reports_short_history():
    """Ранний выход по нехватке данных обязан быть виден в снапшоте."""
    from decimal import Decimal

    from bot.strategies import build
    from bot.strategies.base import Context
    from bot.models import Account, Instrument, MarketData, Position

    strat = build("signal", {})
    n = strat.warmup_bars() - 1
    md = MarketData(symbol="ETHUSDT", bid=Decimal("1"), ask=Decimal("2"),
                    last=Decimal("1.5"), closes=[1.0] * n, highs=[1.0] * n,
                    lows=[1.0] * n, bar_time=0)
    ctx = Context(
        md=md,
        position=Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(100), Decimal(100)),
        instrument=Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.01"),
                              Decimal("0.01"), Decimal("5")),
        open_orders=[],
    )
    assert strat.decide(ctx) == []
    assert "мало данных" in strat.last_snapshot.get("причина", "")


def _entry_ctx(closes, highs, lows):
    from bot.strategies.base import Context
    from bot.models import Account, Instrument, MarketData, Position

    md = MarketData(symbol="ETHUSDT", bid=Decimal("99.9"), ask=Decimal("100.1"),
                    last=Decimal("100"), closes=closes, highs=highs, lows=lows,
                    bar_time=0)
    return Context(
        md=md,
        position=Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(100), Decimal(100)),
        instrument=Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.001"),
                              Decimal("0.001"), Decimal("1")),
        open_orders=[],
    )


def test_atr_stop_widens_with_volatility():
    """Стоп от ATR: в подвижном рынке дальше, чем фиксированный процент.

    Фиксированный стоп 0.8% в тесте выбивало шумом — все выходы подряд
    были стоп-лоссами раньше, чем возврат к среднему успевал отработать.
    """
    from bot.strategies import build

    n = 80
    closes = [100.0] * n
    quiet = build("signal", {"stop_loss_atr_mult": 2.0})
    a_quiet = quiet._entry("Buy", _entry_ctx(closes, [100.1] * n, [99.9] * n), 2, {})

    wild = build("signal", {"stop_loss_atr_mult": 2.0})
    a_wild = wild._entry("Buy", _entry_ctx(closes, [102.0] * n, [98.0] * n), 2, {})

    assert a_wild.stop_loss < a_quiet.stop_loss, (
        "в подвижном рынке стоп обязан быть дальше от входа")

    # выключенный ATR-стоп — прежнее поведение
    off = build("signal", {})
    a_off = off._entry("Buy", _entry_ctx(closes, [102.0] * n, [98.0] * n), 2, {})
    assert abs(float(a_off.stop_loss) - 100 * (1 - 0.008)) < 1e-6


def test_atr_stop_never_tighter_than_half_pct_stop():
    """В штиле ATR-стоп не должен прилипать к цене — не уже половины процентного."""
    from bot.strategies import build

    n = 80
    strat = build("signal", {"stop_loss_atr_mult": 0.1})
    act = strat._entry("Buy", _entry_ctx([100.0] * n, [100.001] * n, [99.999] * n), 2, {})
    floor = 100 * (1 - 0.008 / 2)
    assert float(act.stop_loss) <= floor + 1e-9


def test_live_mode_records_journal_but_never_simulates(tmp_path):
    """Боевой режим: журнал решений пишется, сделки не имитируются.

    Раньше в live журнала не было вовсе — status.sh показывал вечно
    устаревшие файлы, и наблюдать за ботом было нечем.
    """
    from decimal import Decimal as D

    from bot.paper import PaperTrader
    from bot.worker import SymbolWorker

    jp = tmp_path / "X_journal.csv"
    paper = PaperTrader(journal_path=str(jp))
    w = SymbolWorker(symbol="XRPUSDT", exchange=None, strategy=None,
                     interval="60", warmup=25, paper=paper, simulate=False)

    class Strat:
        last_snapshot = {"канал": "1..2"}

        def decide(self, ctx):
            return [Action(kind="market", side="Buy", qty=D("6"))]

    w.strategy = Strat()
    ctx = _ctx_for_worker()
    actions = w.decide(ctx)
    assert actions and paper.position is None, "в live позиция не имитируется"
    text = jp.read_text(encoding="utf-8")
    assert "market Buy" in text, "решение обязано попасть в журнал"
    assert ",Sell," in text or "Sell" in text.split("\n")[1], \
        "колонка позиции должна показывать РЕАЛЬНУЮ позицию с биржи"


def _ctx_for_worker():
    from decimal import Decimal as D

    from bot.models import Account, Instrument, MarketData
    from bot.strategies.base import Context

    md = MarketData(symbol="XRPUSDT", bid=D("1"), ask=D("1.01"), last=D("1"),
                    closes=[1.0] * 30, highs=[1.1] * 30, lows=[0.9] * 30, bar_time=0)
    return Context(
        md=md,
        position=Position("XRPUSDT", "Sell", D("6"), D("1"), D(0), D("1")),
        account=Account(D(40), D(40)),
        instrument=Instrument("XRPUSDT", D("0.0001"), D("0.1"), D("0.1"), D("5")),
        open_orders=[],
    )
