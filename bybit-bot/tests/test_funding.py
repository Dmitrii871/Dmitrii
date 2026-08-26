"""Тесты оценки дельта-нейтрального сбора фандинга."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.funding_scan import PERP_TAKER, SPOT_TAKER, evaluate

LEG = 250.0
FEES = LEG * (SPOT_TAKER + PERP_TAKER) * 2      # 0.78 USDT на полный цикл


def test_fees_are_charged_on_all_four_trades():
    """Вход и выход обеих ног — четыре сделки, а не две."""
    r = evaluate([0.0] * 10, 8.0, LEG)
    assert abs(r["fees_usdt"] - FEES) < 0.001
    assert r["net_usdt"] < 0, "при нулевой ставке позиция убыточна на комиссию"


def test_positive_funding_pays_the_short():
    r = evaluate([0.0001] * 100, 8.0, LEG)
    assert r["gross_usdt"] > 0
    assert abs(r["gross_usdt"] - 0.0001 * 100 * LEG) < 0.001


def test_negative_funding_costs_money():
    r = evaluate([-0.0001] * 100, 8.0, LEG)
    assert r["gross_usdt"] < 0
    assert r["net_usdt"] < r["gross_usdt"], "комиссия добавляется к убытку"


def test_apr_depends_on_funding_interval():
    """Та же ставка при часовом интервале даёт восьмикратные годовые."""
    rates = [0.0001] * 50
    apr_8h = evaluate(rates, 8.0, LEG)["median_apr"]
    apr_1h = evaluate(rates, 1.0, LEG)["median_apr"]
    assert abs(apr_8h - 10.95) < 0.01
    assert abs(apr_1h / apr_8h - 8) < 0.01


def test_flip_rate_detects_unstable_funding():
    """Постоянная смена знака — признак непредсказуемого дохода."""
    stable = evaluate([0.0001] * 100, 8.0, LEG)
    flipping = evaluate([0.0001, -0.0001] * 50, 8.0, LEG)
    assert stable["flip_rate"] == 0.0
    assert flipping["flip_rate"] > 0.95


def test_share_positive_counts_periods_in_your_favour():
    r = evaluate([0.0001] * 80 + [-0.0001] * 20, 8.0, LEG)
    assert abs(r["share_positive"] - 0.8) < 0.001


def test_worst_period_is_reported_in_bps():
    r = evaluate([0.0001] * 50 + [-0.002], 8.0, LEG)
    assert abs(r["worst_period_bps"] - (-20.0)) < 0.01


def test_monthly_projection_scales_by_observed_days():
    """200 выплат по 8 часов — это 66.7 дней, а не месяц."""
    r = evaluate([0.0001] * 200, 8.0, LEG)
    assert abs(r["days"] - 200 * 8 / 24) < 0.01
    expected = r["net_usdt"] / r["days"] * 30
    assert abs(r["net_per_month"] - expected) < 0.01


def test_empty_history_returns_nothing():
    assert evaluate([], 8.0, LEG) == {}


# ------------------------------------------------- концентрация дохода
def test_even_flow_has_low_concentration():
    """Ровный поток: верхние 10% выплат дают примерно 10% дохода."""
    from tools.funding_detail import concentration
    c = concentration([0.0001] * 200)
    assert abs(c["top10"] - 0.10) < 0.02
    assert abs(c["top25"] - 0.25) < 0.02


def test_spiky_income_is_detected():
    """Несколько всплесков среди мелочи должны дать концентрацию под 90%."""
    from tools.funding_detail import concentration
    c = concentration([0.00001] * 190 + [0.002] * 10)
    assert c["top10"] > 0.85, "доход из редких событий обязан быть виден"


def test_negative_rates_excluded_from_concentration():
    """Концентрация считается по доходу, а не по убыткам."""
    from tools.funding_detail import concentration
    assert concentration([-0.001] * 50)["top10"] == 0.0


def test_empty_history_concentration_is_zero():
    from tools.funding_detail import concentration
    assert concentration([])["top10"] == 0.0


def test_median_and_mean_diverge_on_spiky_data():
    """Расхождение медианы и среднего — первый признак всплесков.

    Именно оно бросилось в глаза в таблице: у TACUSDT и ZROUSDT одинаковая
    медиана 11%, а чистый доход отличался в 5.5 раза.
    """
    import statistics
    spiky = [0.00001] * 190 + [0.002] * 10
    assert statistics.mean(spiky) > statistics.median(spiky) * 5
