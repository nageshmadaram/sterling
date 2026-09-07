"""The simulator.

Two kinds of test here. The safety ones are the point: a simulation must never
reach a broker, never be driven by live ticks, and never run on top of a live
armed session. The rest check that the replay actually exercises the strategy.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.engines.atm_premium_imbalance import (
    ATMPremiumImbalanceConfig, InstrumentRef, OptionPairRef,
)
from app.engines.atm_premium_imbalance.replay import Bar
import app.services.atm_premium_imbalance_runner as R
import app.services.atm_premium_imbalance_sim as S

IST = timezone(timedelta(hours=5, minutes=30))


def _latest_completed_weekday() -> date:
    day = datetime.now(IST).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


DAY = _latest_completed_weekday()


def _pair(lot=20):
    def leg(ot, token):
        return InstrumentRef(instrument_id=token, tradingsymbol=f"SENSEX77700{ot}",
                             option_type=ot, strike=77700.0, expiry="2026-08-27",
                             lot_size=lot, tick_size=0.05, upper_circuit=3000.0)
    return OptionPairRef(underlying="SENSEX", expiry="2026-08-27", strike=77700.0,
                         ce=leg("CE", "111"), pe=leg("PE", "222"))


def _bars(prices, *, day=DAY, start_hhmm=(9, 15)):
    """One bar per price, each a flat bar so the path is unambiguous."""
    out = []
    for i, px in enumerate(prices):
        ts = datetime(day.year, day.month, day.day, start_hhmm[0], start_hhmm[1],
                      tzinfo=IST) + timedelta(minutes=i)
        out.append(Bar(ts, px, px, px, px, 0.0))
    return out


@pytest.fixture(autouse=True)
def clean():
    # The terminal buffer is module-level and shared, so a test that counts log
    # lines would otherwise count the previous test's as well.
    from app.services.kite_engine import state
    for reset in (R.clear, S._states.clear, S._tasks.clear, state._activity.clear):
        reset()
    yield
    for reset in (R.clear, S._states.clear, S._tasks.clear, state._activity.clear):
        reset()


@pytest.fixture
def wired(monkeypatch):
    """resolve + bars stubbed; CE dear, PE cheap and then rising."""
    import app.services.atm_premium_imbalance as svc
    import app.services.atm_premium_imbalance_replay as replay

    cfg = ATMPremiumImbalanceConfig(enabled=True, sizing_mode="LOTS", lots=1,
                                    target_points=15.0,
                                    max_premium_at_risk_inr=40_000.0).validate()
    monkeypatch.setattr(svc, "get_config", lambda *a, **k: cfg, raising=False)

    async def resolve(uid, c):
        return _pair()
    monkeypatch.setattr(svc, "resolve_option_pair", resolve, raising=False)

    ce = _bars([500.0, 501.0, 502.0])
    pe = _bars([100.0, 110.0, 120.0])

    async def bars(uid, token, day):
        if day != DAY:
            return []
        return ce if int(token) == 111 else pe
    monkeypatch.setattr(replay, "kite_minute_bars", bars, raising=False)
    # keep the sleeps out of the test
    monkeypatch.setattr(S.asyncio, "sleep", _instant, raising=False)
    return {"cfg": cfg, "ce": ce, "pe": pe}


async def _instant(_seconds):
    return None


# ------------------------------------------------------------------ the safety

@pytest.mark.asyncio
async def test_a_simulation_is_never_driven_by_live_ticks(wired):
    """Today's real ticks and a replayed day are two timelines.

    Letting both into one position is the failure this flag exists to prevent.
    """
    await S.start("u1", speed=600.0)
    session = R.active_session("u1")
    assert session is not None and session.sim is True

    class Boom(R.BrokerPort):
        async def place(self, **kw):
            raise AssertionError("a live tick reached the simulated session")

    assert await R.on_ticks("u1", [{"instrument_token": 111, "last_price": 1.0}],
                            Boom()) == "simulated"


@pytest.mark.asyncio
async def test_a_simulation_refuses_to_run_over_a_live_armed_session(wired):
    """Replaying over real money would show the operator a fiction."""
    live = R.Session(user_id="u1", cfg=wired["cfg"], pair=_pair(),
                     strategy=R.ATMPremiumImbalanceStrategy(
                         cfg=wired["cfg"], pair=_pair(), quantity=20, trade_id="live"),
                     session_date=datetime.now(IST).date(), ce_token=111, pe_token=222)
    R.register(live)
    assert (await S.start("u1"))["status"] == "live_session_active"
    assert R.active_session("u1") is live          # untouched


@pytest.mark.asyncio
async def test_a_finished_live_session_may_be_replayed_over(wired):
    live = R.Session(user_id="u1", cfg=wired["cfg"], pair=_pair(),
                     strategy=R.ATMPremiumImbalanceStrategy(
                         cfg=wired["cfg"], pair=_pair(), quantity=20, trade_id="live"),
                     session_date=datetime.now(IST).date(), ce_token=111, pe_token=222,
                     finished=True)
    R.register(live)
    assert (await S.start("u1"))["status"] == "started"


@pytest.mark.asyncio
async def test_stopping_a_simulation_does_not_release_live_subscriptions(wired, monkeypatch):
    """The simulator subscribed nothing, so it must hand nothing back."""
    released = []

    class TM:
        async def release(self, uid, tokens, owner):
            released.append(tokens)
            return {"ok": True}

    import app.services.exchanges.kite as kite_pkg
    monkeypatch.setattr(kite_pkg, "ticker_manager", TM(), raising=False)
    await S.start("u1", speed=600.0)
    await S.stop("u1")
    assert released == []
    assert R.active_session("u1") is None


@pytest.mark.asyncio
async def test_every_payload_says_it_is_illustrative(wired):
    started = await S.start("u1", speed=600.0)
    assert started["illustrative_only"] is True
    assert S.state("u1")["illustrative_only"] is True


# ------------------------------------------------------------------- the replay

@pytest.mark.asyncio
async def test_nothing_is_traded_before_the_bell(wired):
    """The clock starts at 09:14 with a quote stamped the previous day.

    The observable consequence is what is asserted: the entry cannot have
    happened before the open, because the pre-open quote is refusable and is
    refused. The session clock must also be anchored on the *replayed* day.
    """
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    s = R.active_session("u1")
    open_ms = s.strategy.session_open_ms
    opened = datetime.fromtimestamp(open_ms / 1000, tz=IST)
    assert opened.date() == DAY
    assert opened.strftime("%H:%M") == "09:15"
    assert s.strategy._entry_ts_ms >= open_ms


@pytest.mark.asyncio
async def test_the_replay_buys_the_cheaper_leg_and_reaches_the_target(wired):
    """PE at 100 against CE at 500, then PE runs to 120: target is 115."""
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    s = R.active_session("u1")
    assert s.strategy.trade is not None
    assert s.strategy.trade.option_type == "PE"
    # ask 100.05 (price + half tick) + the 0.50 default entry buffer
    assert s.strategy.trade.entry_price == 100.55
    assert s.strategy.trades_taken == 1
    assert s.finished is True


@pytest.fixture
def long_day(wired, monkeypatch):
    """A rising put, which is what actually produces repeated round trips.

    After an exit the strategy re-arms and buys again at whatever the price is
    now — the exit price — so the next target sits 15 points above *that*. A
    price that comes back down never reaches it; a rising one does. Worth
    knowing about continuous mode generally: it exits on target and immediately
    re-enters, so it holds a position nearly all the time.
    """
    import app.services.atm_premium_imbalance_replay as replay
    ce = _bars([500.0] * 6)
    pe = _bars([100.0, 120.0, 140.0, 160.0, 180.0, 200.0])

    async def bars(uid, token, day):
        if day != DAY:
            return []
        return ce if int(token) == 111 else pe
    monkeypatch.setattr(replay, "kite_minute_bars", bars, raising=False)
    return wired


@pytest.mark.asyncio
async def test_continuous_mode_keeps_trading_after_the_first_close(long_day):
    """The point of continuous: one closed trade is not the end of the session."""
    await S.start("u1", speed=600.0)          # continuous is the default
    await S._tasks["u1"]
    s = R.active_session("u1")
    assert s.strategy.trades_taken >= 2, "it should have re-armed and traded again"
    assert s.finished is False, "the session stays alive while it can still trade"
    assert S.state("u1")["trades"] == s.strategy.trades_taken


@pytest.mark.asyncio
async def test_without_continuous_it_stops_at_the_first_trade(long_day):
    """The same day, the same bars — the difference is only the flag."""
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    s = R.active_session("u1")
    assert s.strategy.trades_taken == 1
    assert s.finished is True


@pytest.mark.asyncio
async def test_a_re_arm_is_announced(long_day):
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    from app.services.kite_engine import state
    assert any(e.kind == "api_rearmed" for e in state.activity("u1"))


@pytest.mark.asyncio
async def test_continuous_mode_says_what_it_relaxed(wired):
    """A replay running a different config from the live one must not be silent."""
    out = await S.start("u1", speed=600.0)
    assert out["continuous"] is True
    assert "trade limit lifted to 50" in out["relaxed"]
    assert "entry window off" in out["relaxed"]
    from app.services.kite_engine import state
    assert any("continuous" in e.message for e in state.activity("u1"))


@pytest.mark.asyncio
async def test_the_session_trade_count_survives_a_re_arm(long_day):
    """Resetting it would make the trade limit and the loss limit unenforceable."""
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    s = R.active_session("u1")
    assert s.strategy.trades_taken >= 2
    assert s.strategy.realised_pnl != 0.0


@pytest.mark.asyncio
async def test_the_size_comes_from_the_config(wired):
    """1 lot of 20, not a hardcoded number."""
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    assert R.active_session("u1").strategy.quantity == 20


@pytest.mark.asyncio
async def test_an_unsized_config_still_simulates_one_lot(wired, monkeypatch):
    """A simulation with no size would show every gate passing and nothing happen."""
    import app.services.atm_premium_imbalance as svc
    cfg = ATMPremiumImbalanceConfig(enabled=True, sizing_mode="LOTS", lots=0,
                                    max_premium_at_risk_inr=40_000.0).validate()
    monkeypatch.setattr(svc, "get_config", lambda *a, **k: cfg, raising=False)
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    assert R.active_session("u1").strategy.quantity == 20


@pytest.mark.asyncio
async def test_progress_is_reported(wired):
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    st = S.state("u1")
    assert st["bars_total"] == 3
    assert st["running"] is False
    assert st["outcome"] is not None


@pytest.mark.asyncio
async def test_a_day_with_no_bars_is_reported_not_guessed(wired, monkeypatch):
    import app.services.atm_premium_imbalance_replay as replay

    async def none(uid, token, day):
        return []
    monkeypatch.setattr(replay, "kite_minute_bars", none, raising=False)
    out = await S.start("u1")
    assert out["status"] == "no_data"


@pytest.mark.asyncio
async def test_the_speed_is_clamped_to_something_sane(wired):
    assert (await S.start("u1", speed=99_999.0))["speed"] == 600.0
    await S.stop("u1")
    # The floor allows slower than real time; zero would be a stopped clock.
    assert (await S.start("u1", speed=0.0))["speed"] == 0.1


@pytest.mark.asyncio
async def test_real_time_is_the_default(wired):
    """One simulated second per real second, so the clock reads like a live one."""
    assert (await S.start("u1"))["speed"] == 1.0


@pytest.mark.asyncio
async def test_the_clock_reads_as_a_market_clock(wired):
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    st = S.state("u1")
    assert st["clock_ist"] is not None
    # 12-hour with AM/PM: an operator reads "09:14:00 AM", not "09:14:00"
    assert st["clock_ist"].endswith("AM") or st["clock_ist"].endswith("PM")


@pytest.mark.asyncio
async def test_the_clock_advances_one_second_at_a_time(wired, monkeypatch):
    """A clock that jumped a minute would not read like a live session."""
    seen: list[int] = []
    real_emit = S._emit

    async def spy(session, broker, legs, **kw):
        seen.append(session.clock_ms)
        return await real_emit(session, broker, legs, **kw)

    monkeypatch.setattr(S, "_emit", spy)
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    gaps = {b - a for a, b in zip(seen, seen[1:])}
    assert gaps and max(gaps) <= S.SECOND_MS, f"clock jumped: {sorted(gaps)}"


@pytest.mark.asyncio
async def test_ticks_walk_the_bar_in_a_documented_order(wired):
    """open, high, low, close: the peak sets the trail before the low tests it.

    A minute bar cannot say which came first, so this ordering is an assumption
    and is the reason a simulation is not a backtest.
    """
    assert S.BAR_PATH == ("open", "high", "low", "close")


@pytest.mark.asyncio
async def test_a_halt_reports_its_reason_not_just_the_word_halted(wired, monkeypatch):
    """A ceiling that stops the trade must say so where the operator is looking."""
    import app.services.atm_premium_imbalance as svc
    # 20 x ~100.55 = Rs2,011, so a Rs1,000 ceiling refuses the entry.
    cfg = ATMPremiumImbalanceConfig(enabled=True, sizing_mode="LOTS", lots=1,
                                    max_premium_at_risk_inr=1_000.0).validate()
    monkeypatch.setattr(svc, "get_config", lambda *a, **k: cfg, raising=False)
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    st = S.state("u1")
    assert st["outcome"] == "halted"
    assert "premium_at_risk_exceeded" in (st["halt_reason"] or "")
    assert "premium_at_risk_exceeded" in st["note"]


@pytest.mark.asyncio
async def test_the_pre_open_tick_lands_just_before_the_bell(wired):
    """A full minute early makes the *other* leg look stale at the open.

    The freshness gate would then report "the feed has gone quiet" for one tick,
    which is true of the clock and false of the market.
    """
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    from app.services.kite_engine import state
    quiet = [e for e in state.activity("u1")
             if e.kind == "api_waiting" and "gone quiet" in e.message]
    assert quiet == []

@pytest.mark.asyncio
async def test_a_skipped_day_is_reported_not_silently_stepped_over(wired, monkeypatch):
    """A failed request on the newest day must not look like a holiday.

    Without this the replay quietly moves back a day and the operator reads
    numbers off a session they did not ask for.
    """
    # Model a rate limit on the requested day and a valid preceding session.
    import app.services.atm_premium_imbalance_replay as replay
    earlier = DAY - timedelta(days=1)
    while earlier.weekday() >= 5:
        earlier -= timedelta(days=1)

    async def flaky(uid, token, day):
        if day >= DAY:
            raise RuntimeError("kite rate limited")
        bars = _bars([100.0], day=day) if int(token) == 222 else _bars([500.0], day=day)
        return bars

    monkeypatch.setattr(replay, "kite_minute_bars", flaky, raising=False)
    out = await S.start("u1", speed=600.0)
    assert out["status"] == "started"
    assert out["session_date"] == earlier.isoformat()
    assert any("kite rate limited" in line for line in out["skipped"]), out["skipped"]

    from app.services.kite_engine import state
    assert any("skipped" in e.message and "rate limited" in e.message
               for e in state.activity("u1"))


@pytest.mark.asyncio
async def test_no_data_anywhere_reports_what_it_tried(wired, monkeypatch):
    import app.services.atm_premium_imbalance_replay as replay

    async def none(uid, token, day):
        return []
    monkeypatch.setattr(replay, "kite_minute_bars", none, raising=False)
    out = await S.start("u1")
    assert out["status"] == "no_data"
    assert out["skipped"], "it should say which days it tried"



@pytest.mark.asyncio
async def test_every_closed_trade_reports_its_result(long_day):
    """A re-arm clears the trade, so the result must be captured before that.

    Without it only the final trade's outcome was ever logged and the rest
    vanished between the exit line and the re-arm line.
    """
    await S.start("u1", speed=600.0)
    await S._tasks["u1"]
    from app.services.kite_engine import state
    events = state.activity("u1")
    done = [e for e in events if e.kind == "api_done"]
    rearmed = [e for e in events if e.kind == "api_rearmed"]
    taken = R.active_session("u1").strategy.trades_taken
    assert len(done) == taken, f"{taken} trades but {len(done)} results reported"
    assert all("pts" in e.message and "P&L" in e.message for e in done)
    assert len(rearmed) >= 1

# ------------------------------------------- the replay must agree with live

@pytest.mark.asyncio
async def test_no_signal_before_the_bell(wired):
    """Live never asks the strategy before 09:15 — the runner's market-hours
    check answers "market_closed" first. The replay must reach the same "no" by
    the same route, or it teaches the operator something untrue about 09:14.
    """
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    s = R.active_session("u1")
    open_ms = s.strategy.session_open_ms
    assert s.strategy._entry_ts_ms >= open_ms

    from app.services.kite_engine import state
    pre = [e for e in state.activity("u1") if e.kind == "api_waiting"]
    assert pre, "it should say why it is doing nothing before the open"
    assert any("exchange is not open" in e.message for e in pre), \
        [e.message for e in pre]


@pytest.mark.asyncio
async def test_the_market_gate_uses_the_virtual_clock_not_the_real_one():
    """Otherwise a replay run at 3am would think the exchange was shut all day."""
    from datetime import datetime as _dt
    bell = int(_dt(2026, 8, 21, 9, 15, tzinfo=IST).timestamp() * 1000)
    assert S._market_open_at(bell - 1_000) is False
    assert S._market_open_at(bell) is True
    assert S._market_open_at(bell + 5 * 3600 * 1000) is True        # 14:15
    assert S._market_open_at(bell + 7 * 3600 * 1000) is False       # 16:15


@pytest.mark.asyncio
async def test_a_date_the_calendar_cannot_answer_still_replays(monkeypatch):
    """A short holiday table must not be able to block a replay outright."""
    from app.services.navigator import calendar as cal

    def boom(_ts):
        raise RuntimeError("year not covered")
    monkeypatch.setattr(cal, "is_market_open_at", boom, raising=False)
    from datetime import datetime as _dt
    bell = int(_dt(2026, 8, 21, 9, 15, tzinfo=IST).timestamp() * 1000)
    assert S._market_open_at(bell) is True
    assert S._market_open_at(bell - 60_000) is False

@pytest.mark.asyncio
async def test_the_clock_starts_a_quarter_minute_before_the_bell(wired, monkeypatch):
    """09:14:45, not 09:14:00. At real speed a full minute of a shut exchange is
    a minute of nothing; fifteen seconds shows the same thing and starts.
    """
    seen: list[int] = []
    real_emit = S._emit

    async def spy(session, broker, legs, **kw):
        seen.append(session.clock_ms)
        return await real_emit(session, broker, legs, **kw)

    monkeypatch.setattr(S, "_emit", spy)
    await S.start("u1", speed=600.0, continuous=False)
    await S._tasks["u1"]
    first = datetime.fromtimestamp(min(seen) / 1000, tz=IST)
    assert first.strftime("%H:%M:%S") == "09:14:59"        # last pre-open second
    assert S.PRE_OPEN_SECONDS == 15
