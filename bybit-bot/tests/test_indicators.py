"""Тесты индикаторов и логики стратегии. Запуск: python -m pytest tests -q"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import bollinger_pct_b, ema, macd, rsi
from bot.strategies.signal import SignalStrategy


def test_ema_constant_series_equals_constant():
    assert all(math.isclose(v, 7.0) for v in ema([7.0] * 50, 10))


def test_ema_alignment_length():
    assert len(ema(list(range(50)), 10)) == 50 - 10 + 1


def test_rsi_monotonic_up_is_100():
    assert math.isclose(rsi([float(i) for i in range(1, 60)], 14)[-1], 100.0)


def test_rsi_monotonic_down_is_0():
    assert math.isclose(rsi([float(i) for i in range(60, 1, -1)], 14)[-1], 0.0)


def test_rsi_right_aligned():
    closes = [float(i) for i in range(1, 60)]
    assert len(rsi(closes, 14)) == len(closes) - 14


def test_rsi_flat_series_is_neutral():
    assert math.isclose(rsi([100.0] * 40, 14)[-1], 50.0)


def test_macd_histogram_is_line_minus_signal():
    closes = [100 + 10 * math.sin(i / 5) for i in range(200)]
    line, sig, hist = macd(closes)
    offset = len(line) - len(sig)
    assert all(math.isclose(hist[i], line[i + offset] - sig[i]) for i in range(len(sig)))


def test_macd_all_series_end_on_same_bar():
    closes = [100 + 10 * math.sin(i / 5) for i in range(200)]
    line, sig, hist = macd(closes)
    assert len(sig) == len(hist)
    assert len(line) >= len(sig)


def test_pct_b_bounds_on_flat_market():
    assert math.isclose(bollinger_pct_b([50.0] * 40)[-1], 0.5)


def test_pct_b_above_one_when_breaking_upper_band():
    closes = [100.0] * 30 + [130.0]
    assert bollinger_pct_b(closes, 20, 2.0)[-1] > 1.0


def test_short_series_returns_empty():
    assert ema([1.0, 2.0], 10) == []
    assert rsi([1.0, 2.0], 14) == []
    assert bollinger_pct_b([1.0, 2.0], 20) == []


def test_strategy_rejects_take_profit_below_fees():
    strat = SignalStrategy({"take_profit_pct": 0.05, "stop_loss_pct": 0.5})
    try:
        strat.validate({"taker_bps": 5.5, "maker_bps": 2.0})
    except ValueError as exc:
        assert "круг при входе" in str(exc)
    else:
        raise AssertionError("должна была быть ошибка: тейк меньше комиссии")


def test_post_only_entry_has_lower_fee_threshold():
    """Вход мейкером удешевляет круг, значит допускает меньший тейк-профит."""
    fees = {"taker_bps": 5.5, "maker_bps": 2.0}
    # 0.10% = 10 bp: круг тейкером 11 bp -> отказ, круг мейкером 7.5 bp -> проходит
    taker = SignalStrategy({"take_profit_pct": 0.10, "stop_loss_pct": 0.1, "entry_type": "market"})
    try:
        taker.validate(fees)
    except ValueError:
        pass
    else:
        raise AssertionError("market-вход при TP 0.10% должен быть отклонён")

    SignalStrategy({"take_profit_pct": 0.10, "stop_loss_pct": 0.1,
                    "entry_type": "post_only"}).validate(fees)


def test_unknown_entry_type_rejected():
    try:
        SignalStrategy({"entry_type": "magic"}).validate({"taker_bps": 5.5})
    except ValueError as exc:
        assert "entry_type" in str(exc)
    else:
        raise AssertionError("неизвестный entry_type должен отклоняться")


def test_post_only_entry_quotes_at_near_touch():
    """PostOnly-вход в лонг встаёт на биде, а не бьёт по аску."""
    from decimal import Decimal
    from bot.models import Account, Instrument, MarketData, Position
    from bot.strategies.base import Context

    strat = SignalStrategy({"entry_type": "post_only", "order_notional_usdt": 25})
    ctx = Context(
        md=MarketData("ETHUSDT", Decimal("2463.28"), Decimal("2463.30"), Decimal("2463.29")),
        position=Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(40), Decimal(23)),
        instrument=Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.01"),
                              Decimal("0.01"), Decimal(5)),
    )
    action = strat._entry("Buy", ctx, 2, {})
    assert action.kind == "limit" and action.post_only
    assert action.price == ctx.md.bid, "лонг должен вставать на биде"

    action_sell = strat._entry("Sell", ctx, 2, {})
    assert action_sell.price == ctx.md.ask, "шорт должен вставать на аске"


def test_strategy_accepts_sane_take_profit():
    SignalStrategy({"take_profit_pct": 1.2, "stop_loss_pct": 0.8}).validate({"taker_bps": 5.5})


def test_oversold_market_votes_long():
    # резкое падение -> низкий RSI и низкий %B -> голоса в лонг
    closes = [100.0] * 60 + [100 - i * 1.5 for i in range(1, 41)]
    longs, shorts, snap = SignalStrategy({}).votes(closes)
    assert longs >= 2, f"ожидали голоса в лонг, получили {snap}"
    assert shorts == 0


def test_overbought_market_votes_short():
    closes = [100.0] * 60 + [100 + i * 1.5 for i in range(1, 41)]
    longs, shorts, snap = SignalStrategy({}).votes(closes)
    assert shorts >= 2, f"ожидали голоса в шорт, получили {snap}"
    assert longs == 0


def test_maker_rejects_spread_below_fee():
    from bot.strategies.maker import MakerStrategy
    try:
        MakerStrategy({"spread_bps": 1.0}).validate({"maker_bps": 2.0})
    except ValueError as exc:
        assert "комиссию мейкера" in str(exc)
    else:
        raise AssertionError("должна была быть ошибка: спред меньше комиссии")


def _ctx(closes, bar=1, pos=None):
    from decimal import Decimal
    from bot.models import Account, Instrument, MarketData, Position
    from bot.strategies.base import Context
    return Context(
        md=MarketData("ETHUSDT", Decimal("2463.28"), Decimal("2463.30"),
                      Decimal("2463.29"), closes, bar),
        position=pos or Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(40), Decimal(23)),
        instrument=Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.01"),
                              Decimal("0.01"), Decimal(5)),
    )


DOWN = [100.0] * 80 + [100 - i * 1.2 for i in range(1, 45)]
UP = [100.0] * 80 + [100 + i * 1.2 for i in range(1, 45)]


def test_bot_opens_long_on_oversold():
    actions = SignalStrategy({"min_confluence": 2}).decide(_ctx(DOWN))
    assert [a.side for a in actions] == ["Buy"]


def test_bot_opens_short_on_overbought():
    actions = SignalStrategy({"min_confluence": 2}).decide(_ctx(UP))
    assert [a.side for a in actions] == ["Sell"]


def test_long_only_skips_short_signal():
    strat = SignalStrategy({"min_confluence": 2, "direction": "long_only"})
    assert strat.decide(_ctx(UP)) == []
    assert [a.side for a in SignalStrategy(
        {"min_confluence": 2, "direction": "long_only"}).decide(_ctx(DOWN))] == ["Buy"]


def test_short_only_skips_long_signal():
    strat = SignalStrategy({"min_confluence": 2, "direction": "short_only"})
    assert strat.decide(_ctx(DOWN)) == []
    assert [a.side for a in SignalStrategy(
        {"min_confluence": 2, "direction": "short_only"}).decide(_ctx(UP))] == ["Sell"]


def test_bad_direction_rejected():
    try:
        SignalStrategy({"direction": "sideways"}).validate({"taker_bps": 5.5})
    except ValueError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("неизвестный direction должен отклоняться")


def test_reversal_closes_open_position():
    from decimal import Decimal
    from bot.models import Position
    long_pos = Position("ETHUSDT", "Buy", Decimal("0.02"), Decimal(2478), Decimal(0), Decimal(2463))
    actions = SignalStrategy({"min_confluence": 2}).decide(_ctx(UP, pos=long_pos))
    assert [a.kind for a in actions] == ["close"]


def test_cooldown_does_not_block_first_entry():
    """Кулдаун отсчитывается от последнего входа, а не от старта бота."""
    assert SignalStrategy({"min_confluence": 2, "cooldown_bars": 10}).decide(_ctx(DOWN))


def test_maker_quotes_both_sides():
    """Котировки с обеих сторон — но только там, где спред это оправдывает."""
    from decimal import Decimal
    from bot.models import Account, Instrument, MarketData, Position
    from bot.strategies.base import Context
    from bot.strategies.maker import MakerStrategy

    strat = MakerStrategy({"spread_bps": 8, "order_notional_usdt": 25})
    strat.validate({"maker_bps": 2.0})
    ctx = Context(                       # спред 20 bp, круг по комиссии 4 bp
        md=MarketData("ALTUSDT", Decimal("99.90"), Decimal("100.10"), Decimal("100.0")),
        position=Position("ALTUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(40), Decimal(23)),
        instrument=Instrument("ALTUSDT", Decimal("0.01"), Decimal("0.01"),
                              Decimal("0.01"), Decimal(5)),
    )
    actions = strat.decide(ctx)
    sides = [a.side for a in actions if a.kind == "limit"]
    assert sorted(sides) == ["Buy", "Sell"]
    bid = next(a for a in actions if a.side == "Buy")
    ask = next(a for a in actions if a.side == "Sell")
    assert bid.price < ask.price, "бид должен быть ниже аска"


def test_partial_fill_leftover_is_cancelled():
    """Позиция открыта, часть заявки висит — остаток надо снять.

    Иначе позиция тихо вырастет сверх расчёта, а TP/SL были посчитаны
    от исходного размера.
    """
    from decimal import Decimal
    from bot.models import Position
    half = Position("ETHUSDT", "Buy", Decimal("0.01"), Decimal(2463), Decimal(0), Decimal(2463))
    ctx = _ctx(DOWN, pos=half)
    ctx.open_orders = [{"orderId": "1", "reduceOnly": False}]
    actions = SignalStrategy({"entry_type": "post_only"}).decide(ctx)
    assert [a.kind for a in actions] == ["cancel_all"]


def test_reduce_only_orders_are_not_cancelled_as_leftovers():
    """Заявки на закрытие — не остаток входа, их трогать нельзя."""
    from decimal import Decimal
    from bot.models import Position
    pos = Position("ETHUSDT", "Buy", Decimal("0.02"), Decimal(2463), Decimal(0), Decimal(2463))
    ctx = _ctx(DOWN, pos=pos)
    ctx.open_orders = [{"orderId": "1", "reduceOnly": True}]
    assert SignalStrategy({"entry_type": "post_only"}).decide(ctx) == []


def test_votes_series_matches_per_bar():
    """Пакетный расчёт обязан совпадать с побарным — иначе где-то утечка будущего.

    Это главная страховка ускорения бэктеста: если пакетный вариант хоть на
    одном баре отличается, значит индикатор увидел данные, которых на тот
    момент не было.
    """
    import math
    closes = [100 + 12 * math.sin(i / 7) + 4 * math.cos(i / 3) for i in range(400)]
    strat = SignalStrategy({})
    batch = strat.votes_series(closes)
    for i in range(strat.warmup_bars(), len(closes)):
        per_bar = strat.votes(closes[: i + 1])[:2]
        assert batch[i] == per_bar, f"расхождение на баре {i}: {batch[i]} != {per_bar}"


def test_momentum_mode_is_exact_inverse_of_reversion():
    """Трендовый режим обязан быть точным зеркалом разворотного."""
    rev = SignalStrategy({"mode": "reversion"})
    mom = SignalStrategy({"mode": "momentum"})
    for series in (UP, DOWN):
        rl, rs, _ = rev.votes(series)
        ml, ms, _ = mom.votes(series)
        assert (ml, ms) == (rs, rl), "режимы должны быть зеркальны"


def test_momentum_mode_batch_matches_per_bar():
    """Ускоренный расчёт должен зеркалиться так же, иначе бэктест соврёт."""
    import math
    closes = [100 + 9 * math.sin(i / 6) for i in range(300)]
    strat = SignalStrategy({"mode": "momentum"})
    batch = strat.votes_series(closes)
    for i in range(strat.warmup_bars(), len(closes)):
        assert batch[i] == strat.votes(closes[: i + 1])[:2], f"расхождение на баре {i}"


def test_bad_mode_rejected():
    try:
        SignalStrategy({"mode": "гадание"}).validate({"taker_bps": 5.5})
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("неизвестный режим должен отклоняться")


def test_momentum_buys_strength():
    """В трендовом режиме растущий рынок даёт сигнал в лонг, а не в шорт."""
    actions = SignalStrategy({"mode": "momentum", "min_confluence": 2}).decide(_ctx(UP))
    assert [a.side for a in actions] == ["Buy"]


def test_maker_refuses_to_quote_when_spread_below_fees():
    """Спред уже комиссии = гарантированный убыток с каждой сделки."""
    from decimal import Decimal
    from bot.strategies.maker import MakerStrategy
    strat = MakerStrategy({"spread_bps": 8.0, "order_notional_usdt": 25})
    strat.validate({"maker_bps": 2.0})
    # реальная книга ETHUSDT: спред 0.02 USDT на 2463 = 0.081 bp
    ctx = _ctx([])
    actions = strat.decide(ctx)
    assert [a.kind for a in actions] == ["cancel_all"], \
        "при спреде уже комиссии бот обязан снять котировки, а не выставлять"


def test_maker_quotes_when_spread_is_wide_enough():
    """На широком спреде маркет-мейкинг разрешён."""
    from decimal import Decimal
    from bot.models import Account, Instrument, MarketData, Position
    from bot.strategies.base import Context
    from bot.strategies.maker import MakerStrategy

    strat = MakerStrategy({"spread_bps": 8.0, "order_notional_usdt": 25})
    strat.validate({"maker_bps": 2.0})
    # спред 20 bp — вчетверо шире круга по комиссии
    ctx = Context(
        md=MarketData("ALTUSDT", Decimal("99.90"), Decimal("100.10"), Decimal("100.0")),
        position=Position("ALTUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
        account=Account(Decimal(40), Decimal(23)),
        instrument=Instrument("ALTUSDT", Decimal("0.01"), Decimal("0.01"),
                              Decimal("0.01"), Decimal(5)),
    )
    sides = [a.side for a in strat.decide(ctx) if a.kind == "limit"]
    assert sorted(sides) == ["Buy", "Sell"]
