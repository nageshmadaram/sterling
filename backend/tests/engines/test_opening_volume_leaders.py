from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.engines.option_contracts import Bar
from app.engines.opening_volume_leaders import (
    CandleQuality,
    ChaseState,
    EntryPhase,
    LeaderDirection,
    LeaderTier,
    LiquidityState,
    OpeningVolumeConfig,
    ValidationState,
    classify_tier,
    evaluate_leader,
    rank_leaders,
    scan_leaders,
)

IST = timezone(timedelta(hours=5, minutes=30))
SESSION = date(2026, 9, 3)


def _prior_sessions(count: int, *, before: date = SESSION) -> list[date]:
    days: list[date] = []
    cursor = before
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return list(reversed(days))


def _bar(
    session: date,
    at: time,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 100.0,
) -> Bar:
    return Bar(
        datetime.combine(session, at, tzinfo=IST), open_, high, low, close, volume
    )


def _history(
    *,
    prior_count: int = 10,
    prior_open_volume: float = 100.0,
    current_open_volume: float = 500.0,
    down: bool = False,
    with_break: bool = True,
) -> list[Bar]:
    rows: list[Bar] = []
    for session in _prior_sessions(prior_count):
        rows.extend(
            [
                _bar(session, time(9, 15), volume=prior_open_volume),
                _bar(session, time(15, 29), close=100.0, volume=50.0),
            ]
        )
    if down:
        rows.append(
            _bar(
                SESSION,
                time(9, 15),
                open_=110.0,
                high=110.5,
                low=107.5,
                close=108.0,
                volume=current_open_volume,
            )
        )
        if with_break:
            rows.append(
                _bar(
                    SESSION,
                    time(9, 16),
                    open_=108.0,
                    high=108.2,
                    low=106.5,
                    close=107.0,
                    volume=200.0,
                )
            )
    else:
        rows.append(
            _bar(
                SESSION,
                time(9, 15),
                open_=100.0,
                high=102.5,
                low=99.5,
                close=102.0,
                volume=current_open_volume,
            )
        )
        if with_break:
            rows.append(
                _bar(
                    SESSION,
                    time(9, 16),
                    open_=102.0,
                    high=103.5,
                    low=101.8,
                    close=103.0,
                    volume=200.0,
                )
            )
    return rows


@pytest.mark.parametrize(
    ("rvol", "expected"),
    [
        (0.0, LeaderTier.WEAK),
        (1.999, LeaderTier.WEAK),
        (2.0, LeaderTier.WATCH),
        (2.999, LeaderTier.WATCH),
        (3.0, LeaderTier.SPURT),
        (4.999, LeaderTier.SPURT),
        (5.0, LeaderTier.STRONG),
        (9.999, LeaderTier.STRONG),
        (10.0, LeaderTier.EXPLOSIVE),
    ],
)
def test_documented_rvol_boundaries_are_inclusive(rvol, expected):
    assert classify_tier(rvol) is expected


@pytest.mark.parametrize(
    (
        "symbol",
        "opening_volume",
        "displayed_baseline",
        "direction",
        "price",
        "orb_minute",
        "tier",
        "combo",
    ),
    [
        (
            "GODREJCP",
            170_249,
            9_603,
            LeaderDirection.DOWN,
            870.0,
            16,
            LeaderTier.EXPLOSIVE,
            True,
        ),
        (
            "RBLBANK",
            795_733,
            61_187,
            LeaderDirection.UP,
            399.75,
            16,
            LeaderTier.EXPLOSIVE,
            True,
        ),
        (
            "SOLARINDS",
            13_631,
            2_121,
            LeaderDirection.UP,
            20_985.0,
            16,
            LeaderTier.STRONG,
            True,
        ),
        (
            "INOXWIND",
            781_916,
            154_578,
            LeaderDirection.UP,
            71.73,
            16,
            LeaderTier.STRONG,
            False,
        ),
        (
            "PAGEIND",
            1_025,
            245,
            LeaderDirection.UP,
            35_985.0,
            23,
            LeaderTier.SPURT,
            True,
        ),
    ],
)
def test_reference_leader_cards_preserve_tier_direction_and_event_times(
    symbol,
    opening_volume,
    displayed_baseline,
    direction,
    price,
    orb_minute,
    tier,
    combo,
):
    """Lock observable tier/direction inputs and causal event timing.

    Displayed volume baselines are integer-rounded, so the test verifies the
    stable classification boundaries and event times rather than a proprietary
    two-decimal display calculation.  The constructed break is aligned; the
    source card's historical non-COMBO state cannot be reconstructed without
    its raw breakout side.
    """

    rows = [
        _bar(
            day,
            time(9, 15),
            open_=price,
            high=price,
            low=price,
            close=price,
            volume=displayed_baseline,
        )
        for day in _prior_sessions(10)
    ]
    if direction is LeaderDirection.UP:
        opening = _bar(
            SESSION,
            time(9, 15),
            open_=price,
            high=price * 1.02,
            low=price * 0.995,
            close=price * 1.015,
            volume=opening_volume,
        )
    else:
        opening = _bar(
            SESSION,
            time(9, 15),
            open_=price,
            high=price * 1.005,
            low=price * 0.98,
            close=price * 0.985,
            volume=opening_volume,
        )
    rows.append(opening)
    midpoint = (opening.high + opening.low) / 2.0
    for minute in range(16, orb_minute):
        rows.append(
            _bar(
                SESSION,
                time(9, minute),
                open_=midpoint,
                high=opening.high,
                low=opening.low,
                close=midpoint,
                volume=1.0,
            )
        )
    if direction is LeaderDirection.UP:
        break_high = opening.high * 1.001
        rows.append(
            _bar(
                SESSION,
                time(9, orb_minute),
                open_=midpoint,
                high=break_high,
                low=midpoint,
                close=break_high,
                volume=1.0,
            )
        )
    else:
        break_low = opening.low * 0.999
        rows.append(
            _bar(
                SESSION,
                time(9, orb_minute),
                open_=midpoint,
                high=midpoint,
                low=break_low,
                close=break_low,
                volume=1.0,
            )
        )

    observed_at = datetime(2026, 9, 3, 9, orb_minute + 1, tzinfo=IST)
    signal = evaluate_leader(
        symbol,
        rows,
        as_of=observed_at,
        average_turnover_inr=25_000_000.0,
    )

    assert signal.rvol == pytest.approx(opening_volume / displayed_baseline)
    assert signal.direction is direction
    assert signal.tier is tier
    assert signal.signal_time.time() == time(9, 15)
    assert signal.orb_break_time is not None
    assert signal.orb_break_time.time() == time(9, orb_minute)
    assert signal.combo is combo


def test_rvol_uses_only_the_same_minute_from_the_last_ten_prior_sessions():
    rows = _history(prior_count=11, current_open_volume=500.0)
    oldest = _prior_sessions(11)[0]
    rows = [
        Bar(row.timestamp, row.open, row.high, row.low, row.close, 10_000.0)
        if row.timestamp.date() == oldest and row.timestamp.time() == time(9, 15)
        else row
        for row in rows
    ]
    # A huge 09:16 print must not contaminate the 09:15 baseline.
    rows.append(_bar(_prior_sessions(11)[-1], time(9, 16), volume=1_000_000.0))

    signal = evaluate_leader(
        "test",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert signal.average_opening_volume == pytest.approx(100.0)
    assert signal.rvol == pytest.approx(5.0)
    assert signal.tier is LeaderTier.STRONG
    assert signal.baseline_session_count == 10


def test_signal_is_unavailable_until_the_opening_candle_is_complete():
    with pytest.raises(ValueError, match="not complete"):
        evaluate_leader(
            "TEST",
            _history(),
            as_of=datetime(2026, 9, 3, 9, 15, 59, tzinfo=IST),
            average_turnover_inr=20_000_000.0,
        )


def test_future_orb_break_does_not_repaint_the_earlier_snapshot():
    rows = _history()
    before_break = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 16, 30, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )
    after_break = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert before_break.orb_break_time is None
    assert before_break.combo is False
    assert after_break.orb_break_side is LeaderDirection.UP
    assert after_break.orb_break_time.time() == time(9, 16)
    assert after_break.orb_cumulative_volume == pytest.approx(700.0)
    assert after_break.orb_immediate is True
    assert after_break.combo is True


def test_malformed_future_and_premarket_rows_cannot_suppress_a_causal_signal():
    rows = _history()
    rows.extend(
        [
            # Invalid OHLCV is irrelevant before the cash session.
            _bar(SESSION, time(9, 0), open_=-1.0, high=-1.0, low=-1.0, close=-1.0),
            # This in-session candle has not completed at the observation time.
            _bar(SESSION, time(9, 17), open_=-1.0, high=-1.0, low=-1.0, close=-1.0),
        ]
    )

    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    assert signal.rvol == pytest.approx(5.0)
    assert signal.orb_break_time is not None


def test_later_aligned_orb_break_is_combo_without_a_0916_restriction():
    rows = _history(with_break=False)
    rows.extend(
        [
            _bar(SESSION, time(9, 16), open_=102.0, high=102.4, low=101.5, close=102.2),
            _bar(SESSION, time(9, 17), open_=102.2, high=103.5, low=102.0, close=103.0),
        ]
    )
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 18, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    assert signal.orb_aligned is True
    assert signal.orb_immediate is False
    assert signal.combo is True


def test_bearish_opening_candle_tracks_the_first_low_break():
    signal = evaluate_leader(
        "TEST",
        _history(down=True),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    assert signal.direction is LeaderDirection.DOWN
    assert signal.orb_break_side is LeaderDirection.DOWN
    assert signal.orb_aligned is True
    assert signal.combo is True


def test_opposite_side_break_is_visible_but_not_a_combo():
    rows = _history(with_break=False)
    rows.append(
        _bar(
            SESSION,
            time(9, 16),
            open_=102.0,
            high=102.2,
            low=98.5,
            close=99.0,
            volume=200.0,
        )
    )
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    assert signal.direction is LeaderDirection.UP
    assert signal.orb_break_side is LeaderDirection.DOWN
    assert signal.orb_aligned is False
    assert signal.combo is False


def test_two_sided_break_is_ambiguous_in_minute_data():
    rows = _history(with_break=False)
    rows.append(
        _bar(
            SESSION,
            time(9, 16),
            open_=102.0,
            high=103.0,
            low=99.0,
            close=102.5,
            volume=200.0,
        )
    )
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    assert signal.orb_break_side is None
    assert signal.orb_break_time is not None
    assert signal.orb_aligned is False
    assert signal.combo is False


def test_liquidity_floor_is_fail_closed_but_does_not_hide_the_leader():
    rows = _history(current_open_volume=1_000.0)
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=19_999_999.0,
    )

    assert signal.tier is LeaderTier.EXPLOSIVE
    assert signal.is_leader is True
    assert signal.liquidity_state is LiquidityState.FAIL
    assert signal.passes_quality_filters is False
    assert signal.combo is False


def test_missing_complete_turnover_history_is_reported_as_unknown():
    signal = evaluate_leader(
        "TEST",
        _history(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
    )

    assert signal.liquidity_state is LiquidityState.UNKNOWN
    assert signal.turnover_session_count == 0
    assert "fewer than 20" in signal.liquidity_reasons[0]


def test_average_turnover_can_be_reconstructed_from_complete_prior_days():
    cfg = OpeningVolumeConfig(
        baseline_sessions=2,
        turnover_sessions=2,
        min_turnover_bars_per_session=2,
        min_average_turnover_inr=10_000.0,
    )
    rows = _history(prior_count=2, current_open_volume=300.0)
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        config=cfg,
    )

    assert signal.turnover_session_count == 2
    assert signal.average_turnover_inr is not None
    assert signal.average_turnover_inr > 10_000.0
    assert signal.liquidity_state is LiquidityState.PASS


def test_candle_power_metrics_are_transparent_and_configurable():
    signal = evaluate_leader(
        "TEST",
        _history(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert signal.candle_quality is CandleQuality.STRONG
    assert signal.body_fraction == pytest.approx(2.0 / 3.0)
    assert signal.close_location == pytest.approx(2.5 / 3.0)

    stricter = OpeningVolumeConfig(strong_close_location=0.90)
    strict_signal = evaluate_leader(
        "TEST",
        _history(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
        config=stricter,
    )
    assert strict_signal.candle_quality is CandleQuality.MODERATE


def test_entry_phase_exposes_the_documented_clock_without_suppressing_cards():
    signal = evaluate_leader(
        "TEST",
        _history(),
        as_of=datetime(2026, 9, 3, 12, 0, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )
    assert signal.entry_phase is EntryPhase.NO_NEW_ENTRY
    assert signal.is_leader is True


def test_documented_entry_distance_freshness_and_stop_are_explicit():
    signal = evaluate_leader(
        "TEST",
        _history(prior_count=12),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert signal.orb_break_level == pytest.approx(102.5)
    assert signal.orb_age_minutes == 1
    assert signal.orb_fresh is True
    assert signal.orb_distance_pct == pytest.approx((103.0 / 102.5 - 1) * 100)
    assert signal.chase_state is ChaseState.PREFERRED
    assert signal.protective_stop_price == pytest.approx(99.5)
    assert signal.stop_distance_pct == pytest.approx((102.5 - 99.5) / 102.5 * 100)
    assert signal.stop_too_wide is True
    assert signal.hold_5m_status is ValidationState.PENDING
    assert signal.move_1pct_within_60m is None


def test_five_minute_hold_and_one_percent_follow_through_use_orb_reference():
    rows = _history(prior_count=12)
    for minute in range(17, 22):
        rows.append(
            _bar(
                SESSION,
                time(9, minute),
                open_=103.0,
                high=104.0,
                low=102.7,
                close=103.4,
                volume=50.0,
            )
        )
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 22, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert signal.hold_5m_status is ValidationState.PASS
    assert signal.hold_5m_check_time is not None
    assert signal.hold_5m_check_time.time() == time(9, 21)
    assert signal.hold_5m_price == pytest.approx(103.4)
    assert signal.move_1pct_within_60m is True
    assert signal.move_1pct_time is not None
    assert signal.move_1pct_time.time() == time(9, 17)
    assert signal.orb_fresh is False
    assert signal.chase_state is ChaseState.CAUTION


def test_third_consecutive_leader_day_is_causal_and_not_assumed_when_unknown():
    sessions = _prior_sessions(12)
    rows: list[Bar] = []
    for index, session in enumerate(sessions):
        volume = 1_000.0 if index >= 10 else 100.0
        rows.extend(
            [
                _bar(session, time(9, 15), volume=volume),
                _bar(session, time(15, 29), close=100.0),
            ]
        )
    rows.extend(_history(prior_count=0, current_open_volume=1_000.0)[-2:])
    repeated = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )
    unknown = evaluate_leader(
        "TEST",
        _history(prior_count=10),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert repeated.consecutive_leader_days == 3
    assert repeated.third_day_repeat is True
    assert unknown.consecutive_leader_days is None
    assert unknown.third_day_repeat is None


def test_vwap_previous_day_break_and_rsi_are_evidence_not_a_private_score():
    rows = _history(prior_count=12)
    for minute in range(17, 31):
        price = 103.0 + (minute - 16) * 0.1
        rows.append(
            _bar(
                SESSION,
                time(9, minute),
                open_=price - 0.1,
                high=price + 0.2,
                low=price - 0.2,
                close=price,
                volume=100.0,
            )
        )
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 31, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )
    payload = signal.to_dict()

    assert signal.intraday_vwap is not None
    assert signal.vwap_aligned is True
    assert signal.previous_day_high == pytest.approx(101.0)
    assert signal.pdh_pdl_break_aligned is True
    assert signal.rsi_14_1m is not None
    assert "score" not in payload


def test_utc_input_is_normalized_to_the_ist_session():
    rows = [
        Bar(
            row.timestamp.astimezone(timezone.utc),
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume,
        )
        for row in _history()
    ]
    signal = evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 3, 47, tzinfo=timezone.utc),
        average_turnover_inr=20_000_000.0,
    )
    assert signal.signal_time.astimezone(IST).time() == time(9, 15)
    assert signal.orb_break_time is not None


def test_rank_is_deterministic_and_does_not_claim_a_private_score():
    strong = evaluate_leader(
        "ZZZ",
        _history(current_open_volume=500.0),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )
    explosive = evaluate_leader(
        "AAA",
        _history(current_open_volume=1_000.0),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=20_000_000.0,
    )

    assert [row.symbol for row in rank_leaders([strong, explosive])] == ["AAA", "ZZZ"]
    assert "score" not in explosive.to_dict()


def test_universe_scan_returns_explicit_per_symbol_failures():
    signals, failures = scan_leaders(
        {"GOOD": _history(), "MISSING": _history(prior_count=2)},
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_by_symbol={"GOOD": 20_000_000.0},
    )

    assert [signal.symbol for signal in signals] == ["GOOD"]
    assert "fewer than 10" in failures["MISSING"]
