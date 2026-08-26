"""Тесты сканера инструментов: отбор не должен пропускать мусор."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.scan import MIN_TURNOVER_24H, analyse


def ticker(sym, bid, ask, last, turnover=50e6, funding=0.0001):
    return {"symbol": sym, "bid1Price": str(bid), "ask1Price": str(ask),
            "lastPrice": str(last), "turnover24h": str(turnover),
            "fundingRate": str(funding)}


def inst(sym, min_qty, interval=480):
    return {"symbol": sym, "lotSizeFilter": {"minOrderQty": str(min_qty)},
            "fundingInterval": interval}


CAPITAL, LEV = 40.0, 3          # бюджет = 40 * 3 * 0.6 = 72 USDT


def test_tight_spread_gives_negative_market_making_edge():
    """ETHUSDT: спред 0.081 bp против круга 4 bp — запас должен быть минусовым."""
    rows = analyse([ticker("ETHUSDT", 2463.28, 2463.30, 2463.29)],
                   {"ETHUSDT": inst("ETHUSDT", 0.01)}, CAPITAL, LEV, 2.0, 5.5)
    assert len(rows) == 1
    assert rows[0]["mm_edge_bps"] < 0, "узкий спред не может давать преимущество мейкеру"


def test_wide_spread_gives_positive_edge():
    rows = analyse([ticker("ALTUSDT", 99.90, 100.10, 100.0)],
                   {"ALTUSDT": inst("ALTUSDT", 0.1)}, CAPITAL, LEV, 2.0, 5.5)
    assert rows[0]["mm_edge_bps"] > 15, "спред 20 bp минус круг 4 bp"


def test_illiquid_instrument_is_filtered_out():
    """Широкий спред на неликвиде — ловушка, а не возможность."""
    rows = analyse([ticker("JUNKUSDT", 1.0, 1.5, 1.25, turnover=MIN_TURNOVER_24H / 10)],
                   {"JUNKUSDT": inst("JUNKUSDT", 1)}, CAPITAL, LEV, 2.0, 5.5)
    assert rows == []


def test_instrument_too_expensive_for_capital_is_filtered():
    """Минимальный лот BTCUSDT не влезет в депозит 40 USDT."""
    rows = analyse([ticker("BTCUSDT", 78000, 78001, 78000)],
                   {"BTCUSDT": inst("BTCUSDT", 0.01)},   # 0.01 BTC = 780 USDT
                   CAPITAL, LEV, 2.0, 5.5)
    assert rows == []


def test_affordable_instrument_passes():
    rows = analyse([ticker("BTCUSDT", 78000, 78001, 78000)],
                   {"BTCUSDT": inst("BTCUSDT", 0.001)},  # 78 USDT — впритык
                   CAPITAL, LEV, 2.0, 5.5)
    assert rows == []          # 78 > бюджет 72
    rows = analyse([ticker("BTCUSDT", 78000, 78001, 78000)],
                   {"BTCUSDT": inst("BTCUSDT", 0.0001)}, CAPITAL, LEV, 2.0, 5.5)
    assert len(rows) == 1


def test_funding_annualised_by_contract_interval():
    """Годовые обязаны считаться по интервалу КОНТРАКТА, а не по общему 8ч.

    У Bybit интервал разный: 480, 240, 60 минут. Ошибка здесь даёт
    расхождение в разы и может подтолкнуть к сделке на ложных цифрах.
    """
    t = ticker("XUSDT", 10.0, 10.01, 10.0, funding=0.0001)
    apr_8h = analyse([t], {"XUSDT": inst("XUSDT", 1, interval=480)},
                     CAPITAL, LEV, 2.0, 5.5)[0]["funding_apr"]
    apr_1h = analyse([t], {"XUSDT": inst("XUSDT", 1, interval=60)},
                     CAPITAL, LEV, 2.0, 5.5)[0]["funding_apr"]
    assert abs(apr_8h - 10.95) < 0.01, "8ч: 0.01% x 1095 выплат = 10.95%"
    assert abs(apr_1h - 87.6) < 0.1, "1ч: тот же процент, но 8760 выплат"
    assert abs(apr_1h / apr_8h - 8) < 0.01, "разница ровно в 8 раз"


def test_missing_funding_interval_defaults_to_8h():
    """Если биржа не отдала интервал — берём 8 часов, но не падаем."""
    rows = analyse([ticker("XUSDT", 10.0, 10.01, 10.0, funding=0.0001)],
                   {"XUSDT": {"symbol": "XUSDT",
                              "lotSizeFilter": {"minOrderQty": "1"}}},
                   CAPITAL, LEV, 2.0, 5.5)
    assert abs(rows[0]["funding_apr"] - 10.95) < 0.01


def test_monthly_income_is_grounded_in_capital():
    """Столбец $/мес должен считаться от бюджета, а не быть абстрактным процентом."""
    rows = analyse([ticker("XUSDT", 10.0, 10.01, 10.0, funding=0.0001)],
                   {"XUSDT": inst("XUSDT", 1, interval=480)}, CAPITAL, LEV, 2.0, 5.5)
    budget = CAPITAL * LEV * 0.6          # 72 USDT
    expected = budget * 0.0001 * (1095 / 12)
    assert abs(rows[0]["month_usdt"] - expected) < 0.01
    assert rows[0]["month_usdt"] < 1.0, "на таком депозите это меньше доллара в месяц"


def test_negative_funding_means_shorts_pay():
    rows = analyse([ticker("XUSDT", 10.0, 10.01, 10.0, funding=-0.0005)],
                   {"XUSDT": inst("XUSDT", 1)}, CAPITAL, LEV, 2.0, 5.5)
    assert rows[0]["funding_apr"] < 0


def test_broken_ticker_does_not_crash_scan():
    """Биржа иногда отдаёт пустые поля — сканер не должен падать."""
    bad = {"symbol": "BADUSDT", "bid1Price": "", "ask1Price": "0",
           "lastPrice": "0", "turnover24h": "abc", "fundingRate": None}
    assert analyse([bad], {"BADUSDT": inst("BADUSDT", 1)}, CAPITAL, LEV, 2.0, 5.5) == []


def test_inverted_book_is_rejected():
    """Аск ниже бида — битые данные, торговать по ним нельзя."""
    rows = analyse([ticker("XUSDT", 10.05, 10.0, 10.0)],
                   {"XUSDT": inst("XUSDT", 1)}, CAPITAL, LEV, 2.0, 5.5)
    assert rows == []
