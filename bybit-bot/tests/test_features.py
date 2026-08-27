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
