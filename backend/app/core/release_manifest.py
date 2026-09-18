"""The release manifest: exactly which build, and exactly which ten lanes, ran.

Gate 0 of the handoff roadmap asks a narrow question — *is the thing that is
running the thing we think is running?* — and it has to be answerable months
later, from an artifact, by someone who was not there. That is what this module
writes.

Three facts are kept apart on purpose:

* the **build** (``runtime_sha``, ``release_tag``) — what is executing;
* the **lane identity** (strategy/mode version, config hash, rule hash) — what
  each lane decides;
* the **evidence schema** — what shape the rows recording it take.

A lane whose rules are not written down gets no identity at all. Manufacturing
a hash for it would produce an identity that looks exactly as authoritative as
a real one, and the whole point of the identity column is that an evidence row
can be traced to rules somebody actually froze. Such a lane is recorded with
``identity: null`` and the reason it has none.

Verification compares a stored manifest against what the current process would
build. Any difference in a rule hash, a config hash or the build SHA is drift,
and drift means the forward evidence collected under the old manifest belongs
to the old identity — never silently to the new one.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from app.core.execution_vehicle import (
    VEHICLE_POLICY_VERSION,
    VehicleError,
    lane_vehicle,
)
from app.core.horizon import MODE_TIMELINES, HorizonMode
from app.core.lane_registry import LANES, LaneDefinition
from app.core.strategy_identity import EVIDENCE_SCHEMA_VERSION, IdentityError

#: Bumped when the shape of the manifest itself changes.
MANIFEST_SCHEMA_VERSION: Final[str] = "1"

#: Written relative to the repository root.
RELEASE_MANIFEST_PATH: Final[str] = "data/manifests/release.json"
STRATEGY_MANIFEST_DIR: Final[str] = "data/manifests/strategies"

UNKNOWN: Final[str] = "UNKNOWN"


def repo_root() -> Path:
    """The repository root, from this file's location rather than the cwd."""
    return Path(__file__).resolve().parents[3]


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root(),
        )
    except Exception:  # pragma: no cover - environment dependent
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def runtime_sha() -> str:
    """The commit actually executing. Read from git, never from the environment.

    A stale ``STERLING_RUNTIME_SHA`` is precisely how an artifact comes to name
    a build it did not run under, so the variable is not consulted.
    """
    from app.services.snapback_identity import build_sha

    return build_sha()


def release_tag() -> str:
    """The human-readable release name.

    ``STERLING_RELEASE_TAG`` wins when set, because a release is a decision
    somebody makes, not something git can infer. Otherwise the nearest tag, and
    otherwise the short SHA, which is still a truthful name for the build.
    """
    declared = (os.environ.get("STERLING_RELEASE_TAG") or "").strip()
    if declared:
        return declared
    described = _git("describe", "--tags", "--always", "--dirty")
    return described or UNKNOWN


def working_tree_clean() -> bool | None:
    """``None`` when git cannot be consulted — which is not a pass."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root(),
        )
    except Exception:  # pragma: no cover - environment dependent
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() == ""


def _identity_builder(strategy_id: str):
    if strategy_id == "snapback":
        from app.engines.snapback.lanes import identity_for

        return identity_for
    if strategy_id == "supertrend":
        from app.engines.sterling_kite_engine.lanes import identity_for

        return identity_for
    return None


@dataclass(frozen=True)
class LaneManifest:
    """One lane's frozen description, identity included when it has one."""

    lane_key: str
    strategy_id: str
    mode: str
    state: str
    rules_defined: bool
    note: str

    #: The mode's time budget, restated here so the artifact is readable
    #: without the code that produced it.
    horizon: Mapping[str, Any]

    #: ``None`` for a lane whose rules are not frozen, plus the reason.
    identity: Mapping[str, Any] | None = None
    identity_unavailable: str | None = None

    #: The vehicle this lane trades, exposed as a first-class field. The rule
    #: hash already absorbs it, but a hash cannot be read: an operator opening
    #: this artifact in two years must see OPTIONS_LONG or FUTURES written out.
    execution_vehicle: str | None = None
    vehicle_policy_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "state": self.state,
            "rules_defined": self.rules_defined,
            "note": self.note,
            "horizon": dict(self.horizon),
            "identity": dict(self.identity) if self.identity else None,
            "identity_unavailable": self.identity_unavailable,
            "execution_vehicle": self.execution_vehicle,
            "vehicle_policy_version": self.vehicle_policy_version,
        }


def _horizon_row(mode: HorizonMode) -> dict[str, Any]:
    budget = MODE_TIMELINES[mode]
    return {
        "signal_timeframe": budget.signal_timeframe,
        "execution_timeframe": budget.execution_timeframe,
        "expected_hold_min": budget.expected_hold_min,
        "expected_hold_max": budget.expected_hold_max,
        "duration_unit": str(budget.duration_unit),
        "hard_hold_limit": budget.hard_hold_limit,
        "allow_overnight": budget.allow_overnight,
        "force_close_time": budget.force_close_time,
        "mode_version": budget.version,
    }


def lane_manifest(
    lane: LaneDefinition,
    *,
    sha: str,
    tag: str,
) -> LaneManifest:
    """Describe one lane, with its identity when the lane has frozen rules."""
    try:
        vehicle = lane_vehicle(lane.lane_key).value
    except VehicleError:
        # A lane with no declared vehicle is recorded as such rather than
        # guessed at. Guessing is how an options result becomes a futures one.
        vehicle = None

    common = {
        "execution_vehicle": vehicle,
        "vehicle_policy_version": VEHICLE_POLICY_VERSION if vehicle else None,
        "lane_key": lane.lane_key,
        "strategy_id": lane.strategy_id,
        "mode": lane.mode.value,
        "state": lane.state.value,
        "rules_defined": lane.rules_defined,
        "note": lane.note,
        "horizon": _horizon_row(lane.mode),
    }

    if not lane.rules_defined:
        return LaneManifest(
            **common,
            identity=None,
            identity_unavailable="rules_not_frozen",
        )

    builder = _identity_builder(lane.strategy_id)
    if builder is None:  # pragma: no cover - guarded by the lane registry
        return LaneManifest(
            **common,
            identity=None,
            identity_unavailable=f"no identity builder for {lane.strategy_id}",
        )

    try:
        identity = builder(lane.mode, runtime_sha=sha, release_tag=tag)
    except IdentityError as exc:
        # The usual cause is an unknown build SHA. Refusing an identity is
        # correct: an evidence row that cannot name its build is not
        # authoritative, so the manifest must not pretend otherwise.
        return LaneManifest(**common, identity=None, identity_unavailable=str(exc))

    return LaneManifest(**common, identity=identity.as_row())


def build_release_manifest(
    *,
    sha: str | None = None,
    tag: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """The whole manifest: build, schemas, and all ten lanes."""
    resolved_sha = sha if sha is not None else runtime_sha()
    resolved_tag = tag if tag is not None else release_tag()

    lanes = [
        lane_manifest(lane, sha=resolved_sha, tag=resolved_tag).as_dict()
        for lane in LANES.values()
    ]

    from app.services.snapback_candidate_universe import UNIVERSE_SCHEMA_VERSION

    return {
        "challengers": _challenger_manifests(sha=resolved_sha, tag=resolved_tag),
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "build": {
            "runtime_sha": resolved_sha,
            "release_tag": resolved_tag,
            "working_tree_clean": working_tree_clean(),
        },
        "schemas": {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "universe_schema_version": str(UNIVERSE_SCHEMA_VERSION),
        },
        # What this release was certified against, frozen with it. Read from
        # the certification and CI stores rather than asserted: a manifest that
        # claims CI passed, written by a hand that did not check, is worse than
        # one that says UNKNOWN.
        "certification": _certification_facts(resolved_sha),
        # Which account the evidence under this release belongs to, and the one
        # switch that separates shadow from capital.
        "account_binding_id": _active_binding_id(),
        "live_execution_enabled": _live_execution_enabled(),
        "lane_count": len(lanes),
        "lanes": lanes,
    }


def _certification_facts(sha: str) -> dict[str, Any]:
    """CI and live-acceptance facts for this SHA, as the stores hold them."""
    facts: dict[str, Any] = {"ci": {"all_required_contexts": UNKNOWN},
                             "live_acceptance": {"overall": UNKNOWN}}
    try:
        from app.core.ci_certification import ci_report

        report = ci_report(sha)
        facts["ci"] = {
            "all_required_contexts": "PASS" if report.all_passed else UNKNOWN,
            "contexts": {r.context: {"conclusion": r.conclusion, "run_id": r.run_id}
                         for r in report.records},
        }
    except Exception:  # noqa: BLE001 - a manifest must still be writable
        pass

    try:
        from app.services.release_certification import CertificationStore

        gate = CertificationStore().read(sha).get("kite_live_acceptance")
        if gate is not None:
            facts["live_acceptance"] = {
                "overall": gate.status,
                "artifact_ref": gate.evidence_ref,
                "artifact_sha256": _artifact_sha256(gate.evidence_ref),
                "observed_by": gate.attested_by,
            }
    except Exception:  # noqa: BLE001
        pass
    return facts


def _artifact_sha256(reference: str) -> str:
    """Checksum the acceptance report so the manifest pins the bytes it means."""
    if not reference:
        return ""
    path = Path(reference)
    if not path.is_absolute():
        path = repo_root() / reference
    if not path.exists():
        return ""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _active_binding_id() -> str:
    try:
        from app.services.account_binding_service import active_binding

        binding = active_binding()
        return str(getattr(binding, "binding_id", "") or "") if binding else ""
    except Exception:  # noqa: BLE001
        return ""


def _live_execution_enabled() -> bool | None:
    try:
        from app.core.lane_registry import LIVE_EXECUTION_ENABLED

        return bool(LIVE_EXECUTION_ENABLED)
    except Exception:  # noqa: BLE001
        return None


def _challenger_manifests(*, sha: str, tag: str) -> list[dict[str, Any]]:
    """Declared challengers, each with its own identity and zero sample.

    A challenger is listed separately from the ten lanes on purpose. Folding it
    into the lane list would invite a reader — or a report — to treat its rows
    as that lane's evidence, which is the one thing a challenger must never be.
    """
    rows: list[dict[str, Any]] = []
    try:
        from app.engines.sterling_kite_engine.directional_challenger import (
            challenger_manifest,
        )

        rows.append(challenger_manifest(runtime_sha=sha, release_tag=tag))
    except IdentityError as exc:
        rows.append({"challenger": "supertrend_directional_v1", "identity_unavailable": str(exc)})
    return rows


@dataclass(frozen=True)
class ManifestDrift:
    """One field that moved between the stored manifest and the live build."""

    scope: str
    field: str
    stored: Any
    current: Any

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.scope}.{self.field}: stored={self.stored!r} current={self.current!r}"


@dataclass(frozen=True)
class ManifestVerdict:
    """Whether the stored manifest still describes what is running."""

    matches: bool
    drift: tuple[ManifestDrift, ...] = field(default_factory=tuple)
    missing_lanes: tuple[str, ...] = field(default_factory=tuple)
    unexpected_lanes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def identity_drift(self) -> tuple[ManifestDrift, ...]:
        """Drift that invalidates evidence attribution, as opposed to a rebuild."""
        return tuple(d for d in self.drift if d.field in _IDENTITY_FIELDS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "drift": [
                {"scope": d.scope, "field": d.field, "stored": d.stored, "current": d.current}
                for d in self.drift
            ],
            "missing_lanes": list(self.missing_lanes),
            "unexpected_lanes": list(self.unexpected_lanes),
            "identity_drift": len(self.identity_drift),
        }


#: Fields whose movement means the evidence identity changed, not merely the
#: build. A row collected under the old value belongs to the old lane identity.
_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "rule_hash",
        "config_hash",
        "strategy_version",
        "mode_version",
        "identity_hash",
        "evidence_schema_version",
        # A vehicle change is an economics change, not a rebuild: the same
        # signal in futures and in bought options are two experiments.
        "execution_vehicle",
        "vehicle_contract_hash",
    }
)

#: Compared for every lane. ``state`` and ``note`` are operational commentary
#: and move without changing what a lane decides, so they are reported but do
#: not count as identity drift.
_LANE_FIELDS: Final[tuple[str, ...]] = (
    "state",
    "rules_defined",
    "identity_unavailable",
    "execution_vehicle",
    "vehicle_policy_version",
)


def verify_manifest(
    stored: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> ManifestVerdict:
    """Compare a stored manifest against the live build."""
    live = dict(current) if current is not None else build_release_manifest()
    drift: list[ManifestDrift] = []

    for key in ("runtime_sha", "release_tag"):
        was = (stored.get("build") or {}).get(key)
        now = (live.get("build") or {}).get(key)
        if was != now:
            drift.append(ManifestDrift("build", key, was, now))

    for key, now in (live.get("schemas") or {}).items():
        was = (stored.get("schemas") or {}).get(key)
        if was != now:
            drift.append(ManifestDrift("schemas", key, was, now))

    stored_lanes = {row["lane_key"]: row for row in stored.get("lanes", [])}
    live_lanes = {row["lane_key"]: row for row in live.get("lanes", [])}

    missing = tuple(sorted(set(stored_lanes) - set(live_lanes)))
    unexpected = tuple(sorted(set(live_lanes) - set(stored_lanes)))

    for lane_key in sorted(set(stored_lanes) & set(live_lanes)):
        was_lane, now_lane = stored_lanes[lane_key], live_lanes[lane_key]
        for key in _LANE_FIELDS:
            if was_lane.get(key) != now_lane.get(key):
                drift.append(
                    ManifestDrift(lane_key, key, was_lane.get(key), now_lane.get(key))
                )
        was_id = was_lane.get("identity") or {}
        now_id = now_lane.get("identity") or {}
        for key in sorted(_IDENTITY_FIELDS):
            if key in was_id or key in now_id:
                if was_id.get(key) != now_id.get(key):
                    drift.append(
                        ManifestDrift(lane_key, key, was_id.get(key), now_id.get(key))
                    )

    return ManifestVerdict(
        matches=not drift and not missing and not unexpected,
        drift=tuple(drift),
        missing_lanes=missing,
        unexpected_lanes=unexpected,
    )


def write_manifest(root: Path | str | None = None, manifest: Mapping[str, Any] | None = None) -> Path:
    """Write ``release.json`` plus one file per lane, and return the release path."""
    base = Path(root) if root is not None else repo_root()
    payload = dict(manifest) if manifest is not None else build_release_manifest()

    release_path = base / RELEASE_MANIFEST_PATH
    release_path.parent.mkdir(parents=True, exist_ok=True)
    release_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lane_dir = base / STRATEGY_MANIFEST_DIR
    lane_dir.mkdir(parents=True, exist_ok=True)
    for lane in payload.get("lanes", []):
        name = lane["lane_key"].replace(":", "__") + ".json"
        body = dict(lane)
        body["build"] = payload.get("build")
        body["generated_at"] = payload.get("generated_at")
        (lane_dir / name).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")

    return release_path


def read_manifest(root: Path | str | None = None) -> dict[str, Any] | None:
    """The stored manifest, or ``None`` when the release was never frozen."""
    base = Path(root) if root is not None else repo_root()
    path = base / RELEASE_MANIFEST_PATH
    if not path.exists():
        return None
    return json.loads(path.read_text())


def render_manifest(payload: Mapping[str, Any]) -> str:
    """Operator-readable summary. One line per lane, identity hash included."""
    build = payload.get("build") or {}
    lines = [
        f"release  {build.get('release_tag', UNKNOWN)}",
        f"build    {build.get('runtime_sha', UNKNOWN)}",
        f"clean    {build.get('working_tree_clean')}",
        f"evidence schema {(payload.get('schemas') or {}).get('evidence_schema_version')}",
        "",
        f"{'lane':<28}{'state':<10}{'identity':<22}note",
    ]
    for lane in payload.get("lanes", []):
        identity = lane.get("identity") or {}
        stamp = identity.get("identity_hash") or f"none: {lane.get('identity_unavailable')}"
        lines.append(
            f"{lane['lane_key']:<28}{lane['state']:<10}{stamp[:21]:<22}{lane.get('note', '')}"
        )
    return "\n".join(lines)


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "RELEASE_MANIFEST_PATH",
    "STRATEGY_MANIFEST_DIR",
    "LaneManifest",
    "ManifestDrift",
    "ManifestVerdict",
    "build_release_manifest",
    "lane_manifest",
    "read_manifest",
    "release_tag",
    "render_manifest",
    "repo_root",
    "runtime_sha",
    "verify_manifest",
    "working_tree_clean",
    "write_manifest",
]
