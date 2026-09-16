"""Identity of the Snapback runtime.

Two distinct facts, deliberately kept apart:

* the historical STRATEGY identity — the frozen commit, config hash and rule hash that
  define what Snapback decides. Immutable.
* the executable BUILD identity — the commit that is actually running. This changes
  whenever an operational or P0 fix lands, and every evidence row must carry it, so a
  report can never claim it ran under a build it did not.

The build SHA is discovered from the repository, never taken on trust from an
environment variable, because a stale variable is exactly how provenance goes wrong.
"""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

# Historical strategy identity — frozen, never edited.
HISTORICAL_STRATEGY_SHA = "5a1354202e2c960c66b7003fce9cb80abd152008"
HISTORICAL_CONFIG_HASH = "6ecbeb53e9768a91"
HISTORICAL_RULE_HASH = "e03ddf75f29463a8"
HISTORICAL_FREEZE_TAG = "snapback-prospective-freeze-1.0"
HISTORICAL_FREEZE_RUNTIME_SHA = "9e989dd910995deb5e77385b983e5992c58883c0"

UNKNOWN_BUILD = "UNKNOWN"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@lru_cache(maxsize=1)
def build_sha() -> str:
    """The commit that is actually executing, read from the repository."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_REPO_ROOT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("Snapback identity: build SHA could not be read: %s", exc)
    return UNKNOWN_BUILD


def expected_build_sha() -> str:
    """The build this release is declared to be, if declared."""
    return (os.environ.get("STERLING_EXPECTED_BUILD_SHA") or "").strip()


def verify_build_identity(*, require_expectation: bool = False) -> Tuple[bool, List[str]]:
    """Compare the declared build with the executing one. Unknown never passes."""
    reasons: List[str] = []
    actual = build_sha()
    expected = expected_build_sha()

    if actual == UNKNOWN_BUILD:
        reasons.append("build_sha_unknown")

    if not expected:
        if require_expectation:
            reasons.append("expected_build_sha_not_declared")
        return (not reasons), reasons

    if actual != expected:
        reasons.append(f"build_sha_mismatch:expected={expected} actual={actual}")

    return (not reasons), reasons


def identity_payload() -> Dict[str, Any]:
    """Both identities together, for stamping on evidence and artifacts."""
    from app.engines.snapback.manifest import compute_config_hash, compute_rule_hash

    try:
        config_hash = compute_config_hash()
        rule_hash = compute_rule_hash()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Snapback identity: hash computation failed: %s", exc)
        config_hash = rule_hash = UNKNOWN_BUILD

    return {
        "historical_strategy_sha": HISTORICAL_STRATEGY_SHA,
        "historical_freeze_tag": HISTORICAL_FREEZE_TAG,
        "historical_freeze_runtime_sha": HISTORICAL_FREEZE_RUNTIME_SHA,
        "config_hash": config_hash,
        "rule_hash": rule_hash,
        "build_sha": build_sha(),
        "expected_build_sha": expected_build_sha() or None,
    }
