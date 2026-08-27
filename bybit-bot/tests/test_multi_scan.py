"""Тесты проверки на независимых инструментах."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.multi_scan import binom_two_sided, features_for


def test_coin_flip_is_not_a_finding():
    """Половина знаков — это ровно то, что даёт монета."""
    assert binom_two_sided(5, 10) == 1.0


def test_eight_of_ten_is_still_not_significant():
    """Ключевая калибровка: восемь совпадений из десяти легко даёт шум."""
    p = binom_two_sided(8, 10)
    assert 0.05 < p < 0.2, f"8/10 не должно проходить порог, получено p={p}"


def test_nine_and_ten_of_ten_are_significant():
    assert binom_two_sided(9, 10) < 0.05
    assert binom_two_sided(10, 10) < 0.005


def test_more_symbols_make_the_test_stronger():
    """Та же доля совпадений на большей выборке значимее."""
    assert binom_two_sided(16, 20) < binom_two_sided(8, 10)


def test_minority_counted_as_majority():
    """Знак может быть отрицательным — важна согласованность, а не направление."""
    assert binom_two_sided(2, 10) == binom_two_sided(8, 10)


def test_empty_sample_is_not_significant():
    assert binom_two_sided(0, 0) == 1.0


def test_random_signs_rarely_pass_threshold():
    """Прогон шума через тест не должен давать находок систематически."""
    rng = random.Random(17)
    passes = 0
    for _ in range(200):
        signs = sum(1 for _ in range(10) if rng.random() > 0.5)
        if binom_two_sided(signs, 10) < 0.05:
            passes += 1
    assert passes / 200 < 0.05, "доля ложных находок обязана держаться ниже уровня"


def test_features_are_right_aligned_to_last_bar():
    """Признак на баре i не должен использовать данные после него."""
    closes = [100 + i * 0.1 for i in range(200)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    vols = [1000.0] * 200
    feats = features_for(closes, highs, lows, vols)
    for name, series in feats.items():
        assert len(series) == len(closes), f"{name}: длина обязана совпадать с историей"
        assert series[-1] is not None, f"{name}: последний бар должен быть заполнен"


def test_first_bars_are_none_not_zero():
    """Пока индикатор не прогрелся, значение отсутствует, а не равно нулю.

    Ноль был бы принят за настоящее наблюдение и исказил бы корреляцию.
    """
    closes = [100 + i * 0.1 for i in range(200)]
    feats = features_for(closes, [c + 0.5 for c in closes],
                         [c - 0.5 for c in closes], [1000.0] * 200)
    assert feats["RSI 14"][0] is None
    assert feats["доходность прошлого бара"][0] is None
