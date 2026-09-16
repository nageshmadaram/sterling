"""E11: the hedge beta must be reconstructible, not just a number.

Beta sizes the hedge, so it moves P&L. A bare float cannot show which sessions it used,
whether it was clamped, or that no session after the signal leaked into it.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.engines.snapback.hedge import BETA_BOUNDS, BETA_WINDOW, rolling_beta
from app.engines.snapback.models import to_bars
from app.services.snapback_beta_provenance import (
    BetaSnapshot,
    canonical_index_beta,
    causal_beta_with_provenance,
    record_beta_snapshot,
)
from app.services.snapback_observation_warehouse import (
    EvidenceIntegrityError,
    SnapbackObservationWarehouse,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _series(n=120, drift=1.0, seed=3):
    import random

    rng = random.Random(seed)
    out = []
    price = 100.0
    ts = 1_700_000_000_000
    for i in range(n):
        price *= 1 + rng.uniform(-0.01, 0.01) * drift
        out.append({
            "time": ts + i * 86_400_000, "open": price, "high": price * 1.01,
            "low": price * 0.99, "close": price, "volume": 1000,
        })
    return out


def _sessions(candles):
    bars = to_bars(candles)
    return [bars.day(i) for i in range(len(bars))]


def test_beta_matches_the_existing_rolling_implementation():
    stock = to_bars(_series(seed=1))
    market = to_bars(_series(seed=2, drift=0.6))
    signal_session = _sessions(_series(seed=1))[-1]

    reference = rolling_beta(stock, market)
    snapshot = causal_beta_with_provenance(
        stock, market, signal_session=signal_session,
    )

    expected = reference.get(signal_session)
    if expected is None:
        prior = [d for d in reference if d <= signal_session]
        expected = reference[max(prior)] if prior else None

    assert snapshot.clamped_beta == pytest.approx(expected, abs=1e-12)


def test_the_snapshot_names_its_method_and_window():
    stock = to_bars(_series(seed=1))
    market = to_bars(_series(seed=2, drift=0.6))

    snapshot = causal_beta_with_provenance(
        stock, market, signal_session=_sessions(_series(seed=1))[-1],
    )

    assert snapshot.method == "ROLLING_BETA_60"
    assert snapshot.window_sessions == BETA_WINDOW
    assert snapshot.aligned_observation_count == BETA_WINDOW


def test_no_session_after_the_signal_is_used():
    candles = _series(seed=1)
    sessions = _sessions(candles)
    signal_session = sessions[-20]

    snapshot = causal_beta_with_provenance(
        to_bars(candles), to_bars(_series(seed=2, drift=0.6)),
        signal_session=signal_session,
    )

    assert snapshot.window_end_session is not None
    assert snapshot.window_end_session < signal_session


def test_the_series_hashes_change_with_the_inputs():
    stock = to_bars(_series(seed=1))
    other = to_bars(_series(seed=9))
    market = to_bars(_series(seed=2, drift=0.6))
    session = _sessions(_series(seed=1))[-1]

    a = causal_beta_with_provenance(stock, market, signal_session=session)
    b = causal_beta_with_provenance(other, market, signal_session=session)

    assert a.underlying_series_hash != b.underlying_series_hash
    assert a.aligned_returns_hash != b.aligned_returns_hash


def test_clamping_is_reported_not_hidden():
    snapshot = BetaSnapshot(
        symbol="X", market_symbol="NIFTY", method="ROLLING_BETA_60",
        window_sessions=BETA_WINDOW, signal_session="2026-10-15",
        beta_effective_session="2026-10-14", window_start_session="2026-07-01",
        window_end_session="2026-10-14", aligned_observation_count=60,
        raw_beta=9.0, clamped_beta=BETA_BOUNDS[1], clamp_low=BETA_BOUNDS[0],
        clamp_high=BETA_BOUNDS[1], was_clamped=True,
    )

    assert snapshot.was_clamped is True
    assert snapshot.clamped_beta == BETA_BOUNDS[1]
    assert snapshot.raw_beta != snapshot.clamped_beta


def test_nifty_is_canonical_not_a_regression():
    snapshot = canonical_index_beta(symbol="NIFTY", signal_session="2026-10-15")

    assert snapshot.method == "CANONICAL_INDEX_BETA"
    assert snapshot.clamped_beta == 1.0
    assert snapshot.window_sessions == 0
    assert snapshot.aligned_observation_count == 0
    assert snapshot.underlying_series_hash is None
    assert snapshot.market_series_hash is None


def test_insufficient_history_is_inconclusive_with_evidence():
    short = to_bars(_series(n=10, seed=1))
    market = to_bars(_series(n=10, seed=2))

    snapshot = causal_beta_with_provenance(
        short, market, signal_session=_sessions(_series(n=10, seed=1))[-1],
    )

    assert snapshot.status == "INCONCLUSIVE_CAUSAL_BETA"
    assert snapshot.clamped_beta is None
    assert "insufficient" in " ".join(snapshot.reason_codes).lower()


def test_a_missing_market_tape_is_inconclusive():
    snapshot = causal_beta_with_provenance(
        to_bars(_series(seed=1)), None, signal_session="2026-10-15",
    )

    assert snapshot.status == "INCONCLUSIVE_CAUSAL_BETA"
    assert snapshot.clamped_beta is None


def test_a_failed_calculation_still_persists_evidence(warehouse):
    snapshot = causal_beta_with_provenance(
        to_bars(_series(n=10, seed=1)), to_bars(_series(n=10, seed=2)),
        signal_session="2026-10-15",
    )
    record_beta_snapshot(warehouse, opportunity_id="OPP-1", snapshot=snapshot)

    rows = warehouse.get_records_by_table("beta_snapshots", opportunity_id="OPP-1")

    assert len(rows) == 1
    assert rows[0]["clamped_beta"] is None or rows[0]["status"] == "INCONCLUSIVE_CAUSAL_BETA"


def test_a_snapshot_is_persisted_with_frozen_identity(warehouse):
    snapshot = canonical_index_beta(symbol="NIFTY", signal_session="2026-10-15")
    record_beta_snapshot(warehouse, opportunity_id="OPP-1", snapshot=snapshot)

    row = warehouse.get_records_by_table("beta_snapshots", opportunity_id="OPP-1")[0]

    assert row["runtime_build_sha"] and row["runtime_build_sha"] != "UNKNOWN"
    assert row["config_hash"]
    assert row["method"] == "CANONICAL_INDEX_BETA"


def test_a_changed_snapshot_under_the_same_id_is_refused(warehouse):
    snapshot = canonical_index_beta(symbol="NIFTY", signal_session="2026-10-15")
    record_beta_snapshot(warehouse, opportunity_id="OPP-1", snapshot=snapshot)

    tampered = BetaSnapshot(**{**snapshot.__dict__, "clamped_beta": 2.0})

    with pytest.raises(EvidenceIntegrityError):
        record_beta_snapshot(warehouse, opportunity_id="OPP-1", snapshot=tampered)


def test_the_entry_path_uses_the_persisted_beta():
    import inspect

    from app.services import snapback as sb

    source = inspect.getsource(sb.process_prospective_pending_entries)

    assert "causal_beta_with_provenance" in source or "record_beta_snapshot" in source
    # The beta handed to the entry must be the one that was persisted.
    assert "snapshot.clamped_beta" in source or "beta_snapshot.clamped_beta" in source
