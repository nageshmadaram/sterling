"""A forming 15-minute bar must not be the bar the trigger judges."""
from app.engines.gamma_move import GammaMoveConfig, evaluate
from tests.engines.gamma_move.conftest import bar, quiet_session

CFG = GammaMoveConfig()


def test_live_clock_inside_the_last_bar_drops_it():
    series = quiet_session() + [bar(0, 24, oi=96_000, volume=5_000, close=53.0)]
    last = series[-1].ts_ms
    m = evaluate(series, CFG, now_ms=last + 3 * 60_000)
    assert m is None or not m.triggered


def test_replay_clock_equal_to_bar_ts_keeps_it():
    series = quiet_session() + [bar(0, 24, oi=96_000, volume=5_000, close=53.0)]
    m = evaluate(series, CFG, now_ms=series[-1].ts_ms)
    assert m is not None and m.triggered
