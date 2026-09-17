"""The release manifest must name the build, and must refuse to invent identity.

Two failure modes are worth more than the rest: a lane with no frozen rules
acquiring a plausible-looking identity hash, and a manifest that still reads as
matching after a rule hash moved.
"""
from __future__ import annotations

import json

import pytest

from app.core.lane_registry import LANES
from app.core.release_manifest import (
    build_release_manifest,
    read_manifest,
    render_manifest,
    verify_manifest,
    write_manifest,
)


@pytest.fixture()
def manifest():
    return build_release_manifest(
        sha="a" * 40, tag="test-release-1.0", generated_at="2026-09-18T00:00:00+00:00"
    )


def test_every_declared_lane_appears(manifest):
    keys = {lane["lane_key"] for lane in manifest["lanes"]}
    assert keys == set(LANES)
    assert manifest["lane_count"] == 10


def test_a_lane_without_frozen_rules_gets_no_identity(manifest):
    by_key = {lane["lane_key"]: lane for lane in manifest["lanes"]}

    for lane_key, lane in LANES.items():
        row = by_key[lane_key]
        if lane.rules_defined:
            continue
        assert row["identity"] is None, f"{lane_key} fabricated an identity"
        assert row["identity_unavailable"] == "rules_not_frozen"


def test_lanes_with_rules_carry_a_distinct_identity_hash(manifest):
    hashes = [
        lane["identity"]["identity_hash"]
        for lane in manifest["lanes"]
        if lane["identity"]
    ]
    assert hashes, "no lane produced an identity"
    assert len(set(hashes)) == len(hashes), "two lanes share one identity hash"


def test_the_build_is_recorded_on_every_identity(manifest):
    for lane in manifest["lanes"]:
        if lane["identity"]:
            assert lane["identity"]["runtime_sha"] == "a" * 40
            assert lane["identity"]["release_tag"] == "test-release-1.0"


def test_an_unknown_build_sha_refuses_identity_rather_than_faking_one():
    # A row that cannot name its build is not authoritative evidence, so the
    # manifest must record the refusal instead of stamping "UNKNOWN" on it.
    manifest = build_release_manifest(sha="UNKNOWN", tag="t")
    frozen = [
        lane
        for lane in manifest["lanes"]
        if LANES[lane["lane_key"]].rules_defined
    ]
    assert frozen
    for lane in frozen:
        assert lane["identity"] is None
        assert "runtime_sha is UNKNOWN" in lane["identity_unavailable"]


def test_verify_matches_itself(manifest):
    verdict = verify_manifest(manifest, manifest)
    assert verdict.matches
    assert verdict.drift == ()


def test_a_moved_rule_hash_is_identity_drift(manifest):
    stored = json.loads(json.dumps(manifest))
    target = next(lane for lane in stored["lanes"] if lane["identity"])
    target["identity"]["rule_hash"] = "0" * 16

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(d.field == "rule_hash" for d in verdict.drift)
    assert verdict.identity_drift, "a moved rule hash must count as identity drift"


def test_a_moved_build_is_drift_but_not_identity_drift(manifest):
    stored = json.loads(json.dumps(manifest))
    stored["build"]["runtime_sha"] = "b" * 40

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(d.field == "runtime_sha" for d in verdict.drift)
    assert not verdict.identity_drift


def test_a_removed_lane_is_reported(manifest):
    stored = json.loads(json.dumps(manifest))
    dropped = stored["lanes"].pop()["lane_key"]

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert verdict.unexpected_lanes == (dropped,)


def test_write_and_read_round_trip(tmp_path, manifest):
    path = write_manifest(tmp_path, manifest)

    assert path == tmp_path / "data/manifests/release.json"
    assert read_manifest(tmp_path) == manifest

    lane_files = sorted((tmp_path / "data/manifests/strategies").glob("*.json"))
    assert len(lane_files) == 10
    one = json.loads(lane_files[0].read_text())
    assert one["build"] == manifest["build"]


def test_read_manifest_is_none_when_never_frozen(tmp_path):
    assert read_manifest(tmp_path) is None


def test_render_lists_every_lane(manifest):
    out = render_manifest(manifest)
    for lane_key in LANES:
        assert lane_key in out
    assert "test-release-1.0" in out
