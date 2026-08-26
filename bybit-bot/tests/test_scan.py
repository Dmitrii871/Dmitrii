"""Тесты сканера инструментов: отбор не должен пропускать мусор."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.scan import MIN_TURNOVER_24H, analyse


def ticker(sym, bid, ask, last, turnover=50e6, funding=0.0001):
    return {"symbol": sym, "bid1Price": str(bid), "ask1Price": str(ask),
            "lastPrice": str(last), "turnover24h": str(turnover),
            "fundingRate": str(funding)}


def inst(sym, min_qty):
    return {"symbol": sym, "lotSizeFilter": {"minOrderQty": str(min_qty)}}


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


def test_funding_annualised_correctly():
    """Фандинг платится трижды в сутки: 0.01% -> 10.95% годовых."""
    rows = analyse([ticker("ETHUSDT", 2463.28, 2463.30, 2463.29, funding=0.0001)],
                   {"ETHUSDT": inst("ETHUSDT", 0.01)}, CAPITAL, LEV, 2.0, 5.5)
    assert abs(rows[0]["funding_apr"] - 10.95) < 0.01


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
