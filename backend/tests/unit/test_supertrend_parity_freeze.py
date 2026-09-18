"""The frozen SuperTrend transformation, pinned bar by bar.

The cleanup the specification asks for — removing duplicate work in the core —
is only safe if "the output did not change" can be demonstrated rather than
believed. These fixtures are that demonstration: the Heikin-Ashi basis, the
three SuperTrend lines, their trend flags, the ATR, and the entry arrows, for
four price paths chosen to cover a clean trend, a transition, chop around a
level, and a gap.

A failure here is not a flaky test. It means the strategy's arithmetic moved,
and the correct response is either to revert the change or to create
`supertrend_core_v2` with its own identity and its own sample — never to
regenerate the fixture so the suite goes quiet.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.fixtures.supertrend_parity.generate import cases, snapshot

FROZEN = Path(__file__).resolve().parents[1] / "fixtures" / "supertrend_parity" / "frozen.json"

ARRAYS = (
    "bull", "bear", "t_fast", "t_mid", "t_slow",
    "l_fast", "l_mid", "l_slow", "basis_close", "atr",
    "entry_long", "entry_short",
)


@pytest.fixture(scope="module")
def frozen() -> dict:
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def test_the_fixture_covers_every_declared_case(frozen):
    assert set(frozen) == set(cases())


@pytest.mark.parametrize("case", sorted(cases()))
@pytest.mark.parametrize("series_name", ARRAYS)
def test_the_transformation_is_unchanged(frozen, case, series_name):
    current = snapshot(cases()[case])[series_name]
    expected = frozen[case][series_name]

    assert len(current) == len(expected), (
        f"{case}/{series_name}: bar count changed "
        f"({len(expected)} frozen, {len(current)} now)"
    )

    for i, (was, now) in enumerate(zip(expected, current)):
        if isinstance(was, float) or isinstance(now, float):
            if was is None or now is None:
                assert was is now, f"{case}/{series_name}[{i}]: {was!r} -> {now!r}"
                continue
            assert np.isclose(was, now, rtol=0, atol=1e-6), (
                f"{case}/{series_name}[{i}]: {was} -> {now}"
            )
        else:
            assert was == now, f"{case}/{series_name}[{i}]: {was!r} -> {now!r}"


def test_the_warmup_is_unchanged(frozen):
    for case, values in cases().items():
        assert snapshot(values)["warmup"] == frozen[case]["warmup"], case


def test_the_fixtures_are_not_degenerate():
    """A fixture of all-False arrows would pass while proving nothing."""
    transitions = sum(
        sum(snapshot(values)["entry_long"]) + sum(snapshot(values)["entry_short"])
        for values in cases().values()
    )
    assert transitions > 0, "no entry arrow fires in any fixture case"

    flips = 0
    for values in cases().values():
        trend = snapshot(values)["t_fast"]
        flips += sum(1 for a, b in zip(trend, trend[1:]) if a != b)
    assert flips > 0, "no trend flip in any fixture case"
