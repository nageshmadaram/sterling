from app.engines.gamma_move.strategy import SessionState
from app.engines.gamma_move.models import PositionState, InstrumentRef
from app.engines.gamma_move.exit import weekday_sessions_held


def _pos(day: str) -> PositionState:
    inst = InstrumentRef(instrument_id="1", tradingsymbol="X26SEP1300CE",
                         option_type="CE", strike=1300, expiry="2026-09-29",
                         lot_size=500, tick_size=0.05)
    return PositionState(signal_id="s", instrument=inst, entry=20.0, stop=14.0,
                         quantity=500, lots=1, entered_ms=0, entry_day=day)


def test_roll_does_not_inflate_hold_across_a_weekend():
    st = SessionState(day="2026-09-04")  # Friday
    st.positions["X"] = _pos("2026-09-04")
    st.roll("2026-09-07")  # Monday
    assert st.positions["X"].sessions_held == 0
    assert weekday_sessions_held("2026-09-04", "2026-09-07") == 1
