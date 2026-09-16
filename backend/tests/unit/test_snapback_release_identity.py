"""Release identity: reports must name the build that actually executed.

The historical strategy identity (FROZEN_COMMIT_SHA, config hash, rule hash) stays
immutable. The executable build is a separate fact, and a mismatch between the
expected build and the real HEAD must halt startup rather than let evidence claim it
ran under a build it did not.
"""

from __future__ import annotations

import pytest

from app.services.snapback_identity import (
    HISTORICAL_STRATEGY_SHA,
    build_sha,
    expected_build_sha,
    identity_payload,
    verify_build_identity,
)


def test_historical_strategy_identity_is_unchanged():
    assert HISTORICAL_STRATEGY_SHA == "5a1354202e2c960c66b7003fce9cb80abd152008"


def test_build_sha_is_the_real_head_not_an_env_claim(monkeypatch):
    monkeypatch.setenv("STERLING_RUNTIME_SHA", "0000000000000000000000000000000000000000")

    actual = build_sha()

    # The executable identity is discovered, never taken on trust from the environment.
    assert actual != "0000000000000000000000000000000000000000"
    assert len(actual) == 40


def test_identity_payload_carries_both_identities():
    payload = identity_payload()

    assert payload["historical_strategy_sha"] == HISTORICAL_STRATEGY_SHA
    assert payload["build_sha"] == build_sha()
    assert "config_hash" in payload
    assert "rule_hash" in payload


def test_matching_expected_build_verifies(monkeypatch):
    monkeypatch.setenv("STERLING_EXPECTED_BUILD_SHA", build_sha())

    ok, reasons = verify_build_identity()

    assert ok is True
    assert reasons == []


def test_mismatched_expected_build_fails_closed(monkeypatch):
    monkeypatch.setenv("STERLING_EXPECTED_BUILD_SHA", "deadbeef" * 5)

    ok, reasons = verify_build_identity()

    assert ok is False
    assert any("build_sha_mismatch" in r for r in reasons)


def test_absent_expectation_is_not_a_silent_pass(monkeypatch):
    monkeypatch.delenv("STERLING_EXPECTED_BUILD_SHA", raising=False)

    ok, reasons = verify_build_identity(require_expectation=True)

    assert ok is False
    assert any("expected_build_sha_not_declared" in r for r in reasons)


def test_preflight_halts_on_build_mismatch(monkeypatch):
    from app.services.snapback_startup import run_startup_preflight

    monkeypatch.setenv("STERLING_EXPECTED_BUILD_SHA", "deadbeef" * 5)

    result = run_startup_preflight(
        db_writable_fn=lambda: True,
        manifest_fn=lambda: (True, []),
        config_store_available_fn=lambda: True,
        config_fn=lambda: type("C", (), {"enabled": True})(),
        config_hash_fn=lambda cfg: "h",
        frozen_config_hash="h",
    )

    assert result.status == "HALTED"
    assert result.may_start_runner is False
    assert "build_identity_mismatch" in result.errors
