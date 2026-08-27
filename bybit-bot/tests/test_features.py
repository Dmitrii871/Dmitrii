"""Тесты меры предсказательной силы: она не должна видеть сигнал в шуме."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.feature_scan import align_right, spearman


def test_perfect_and_inverse_relations():
    rng = random.Random(5)
    x = [rng.gauss(0, 1) for _ in range(300)]
    assert abs(spearman(x, x) - 1.0) < 1e-9
    assert abs(spearman(x, [-v for v in x]) + 1.0) < 1e-9


def test_independent_series_stay_near_noise_floor():
    """Независимые ряды дают |IC| порядка 1/sqrt(n) — это и есть шум."""
    rng = random.Random(7)
    n = 500
    floor = 1 / math.sqrt(n)
    vals = []
    for _ in range(20):
        x = [rng.gauss(0, 1) for _ in range(n)]
        y = [rng.gauss(0, 1) for _ in range(n)]
        vals.append(abs(spearman(x, y)))
    assert sum(vals) / len(vals) < floor * 1.5, "шум не должен систематически превышать порог"
    assert max(vals) > floor * 0.5, "отдельные прогоны обязаны доходить до уровня шума"


def test_noise_can_exceed_fixed_textbook_threshold():
    """Ровно та ловушка, из-за которой порог должен зависеть от выборки.

    При 500 наблюдениях случайные ряды регулярно дают |IC| выше 0.05 —
    числа, которое в учебниках зовут «сильным признаком».
    """
    rng = random.Random(3)
    n = 500
    hits = sum(1 for _ in range(50)
               if abs(spearman([rng.gauss(0, 1) for _ in range(n)],
                               [rng.gauss(0, 1) for _ in range(n)])) > 0.05)
    assert hits > 5, "шум должен пробивать фиксированный порог заметно часто"


def test_significance_threshold_shrinks_with_sample():
    assert 2 / math.sqrt(1000) < 2 / math.sqrt(500)
    assert abs(2 / math.sqrt(10_000) - 0.02) < 0.001


def test_weak_signal_is_detected_above_noise():
    rng = random.Random(11)
    n = 2000
    x = [rng.gauss(0, 1) for _ in range(n)]
    y = [v * 0.15 + rng.gauss(0, 1) for v in x]      # слабая, но реальная связь
    ic = abs(spearman(x, y))
    assert ic > 2 / math.sqrt(n), "настоящая связь обязана пробивать порог"


def test_too_short_series_returns_zero():
    assert spearman([1.0, 2.0], [2.0, 1.0]) == 0.0


def test_constant_series_gives_zero_not_error():
    assert spearman([1.0] * 100, [float(i) for i in range(100)]) == 0.0


def test_align_right_pads_left_with_none():
    """Индикаторы короче истории: выравнивание обязано быть по правому краю."""
    padded = align_right([1.0, 2.0, 3.0], 5)
    assert padded == [None, None, 1.0, 2.0, 3.0]
    assert padded[-1] == 3.0, "последнее значение должно относиться к последнему бару"


def test_align_right_handles_empty_series():
    assert align_right([], 3) == [None, None, None]


# ---------------------------------------- выход по времени вместо стопа
def test_timed_exit_closes_position():
    """Позиция обязана закрыться по сроку, даже если TP и SL не задеты."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.backtest import backtest, synthetic_klines

    rows = synthetic_klines(2000, vol_bps=20)
    cfg = {"take_profit_pct": 50.0, "stop_loss_pct": 0.0,   # уровни недостижимы
           "min_confluence": 2, "order_notional_usdt": 25, "hold_bars": 6}
    r = backtest(rows, cfg, 3.75, 25.0)
    assert r["trades"] > 0, "без выхода по времени сделок бы не было вовсе"
    assert r["timed_exit_share"] > 0.95, "почти все выходы должны быть по сроку"


def test_no_stop_means_stop_never_triggers():
    from tools.backtest import backtest, synthetic_klines
    rows = synthetic_klines(1500, vol_bps=60)
    common = {"take_profit_pct": 1.3, "min_confluence": 2,
              "order_notional_usdt": 25, "hold_bars": 12}
    with_stop = backtest(rows, {**common, "stop_loss_pct": 0.5}, 3.75, 25.0)
    no_stop = backtest(rows, {**common, "stop_loss_pct": 0.0}, 3.75, 25.0)
    assert no_stop["timed_exit_share"] > with_stop["timed_exit_share"], \
        "без стопа больше выходов должно приходиться на срок"


def test_removing_stop_is_not_free_on_random_walk():
    """Контроль: без настоящего возврата к среднему снятие стопа не помогает.

    На данных со встроенным возвратом снятие стопа резко улучшает результат,
    и на этом легко обмануться. На случайном блуждании выигрыша нет,
    а просадка растёт — это и есть цена приёма.
    """
    from tools.backtest import backtest, synthetic_klines
    rows = synthetic_klines(4000, vol_bps=59)
    common = {"take_profit_pct": 1.3, "min_confluence": 2,
              "order_notional_usdt": 25, "mode": "reversion"}
    with_stop = backtest(rows, {**common, "stop_loss_pct": 0.9, "hold_bars": 0}, 3.75, 25.0)
    no_stop = backtest(rows, {**common, "stop_loss_pct": 0.0, "hold_bars": 24}, 3.75, 25.0)
    assert no_stop["max_drawdown"] > with_stop["max_drawdown"], \
        "снятие стопа обязано увеличивать просадку на данных без возврата"
    assert no_stop["profit_factor"] < 1.0, "преимущества из ниоткуда не берётся"
