"""Тесты обхода стакана: цена выхода определяет, жива ли стратегия."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.book_monitor import walk_book

# 10$ по 1.000, 15$ по 0.999, 30$ по 0.998
BOOK = [["1.000", "10"], ["0.999", "15.015"], ["0.998", "30.06"]]
MID = 1.0005


def test_small_exit_pays_only_first_level():
    assert abs(walk_book(BOOK, MID, 5) - 5.0) < 0.01


def test_exit_exactly_at_first_level_size():
    assert abs(walk_book(BOOK, MID, 10) - 5.0) < 0.01


def test_cost_grows_when_walking_deeper():
    """Чем больше позиция, тем дороже выход — иначе расчёт неверен."""
    c5, c25, c50 = (walk_book(BOOK, MID, s) for s in (5, 25, 50))
    assert c5 < c25 < c50


def test_insufficient_book_returns_none():
    """Если книги не хватает, надо честно сказать, а не выдать заниженную цену."""
    assert walk_book(BOOK, MID, 200) is None


def test_empty_book_returns_none():
    assert walk_book([], MID, 10) is None


def test_zero_size_is_not_treated_as_free_exit():
    assert walk_book(BOOK, MID, 0) is None


def test_thin_book_costs_more_than_deep_one():
    thin = [["1.000", "1"], ["0.990", "100"]]
    deep = [["1.000", "100"]]
    assert walk_book(thin, MID, 25) > walk_book(deep, MID, 25)
