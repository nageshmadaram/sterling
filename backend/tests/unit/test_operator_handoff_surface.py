"""The handoff surface must exist, and must not require code to reach safety.

These are documentation tests in the literal sense: they assert that the
artifacts the handoff depends on are present and internally consistent. A
runbook that references a command nobody implemented is worse than no runbook,
because the operator discovers it during an incident.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
OPERATOR = REPO / "docs/operator"
EVIDENCE = REPO / "docs/evidence"
STERLINGCTL = REPO / "scripts/sterlingctl"


#: Every command the operator docs tell someone to type.
REQUIRED_COMMANDS = (
    "status",
    "doctor",
    "start",
    "stop",
    "safe",
    "reconcile",
    "backup",
    "restore-check",
    "manifest",
    "freeze",
    "verify",
    "report",
    "lanes",
)

REQUIRED_DOCS = (
    OPERATOR / "START_HERE.md",
    OPERATOR / "DAILY_RUNBOOK.md",
    OPERATOR / "EMERGENCY_CARD.md",
    OPERATOR / "RECOVERY.md",
    OPERATOR / "ACCESS_CONTINUITY.md",
    OPERATOR / "FAILURE_DRILLS.md",
    EVIDENCE / "PROMOTION_POLICY.md",
    EVIDENCE / "LANE_MANIFESTS.md",
)


@pytest.mark.parametrize("path", REQUIRED_DOCS, ids=lambda p: p.name)
def test_the_handoff_document_exists(path):
    assert path.exists(), f"missing handoff artifact: {path.relative_to(REPO)}"
    assert path.read_text().strip(), f"empty handoff artifact: {path.name}"


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_sterlingctl_implements_the_command(command):
    body = STERLINGCTL.read_text()
    # Matches the case arm, e.g. "  restore-check)" — not a mention in a comment.
    assert re.search(rf"^\s*{re.escape(command)}\)", body, re.M), (
        f"sterlingctl has no `{command}` case arm"
    )


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_usage_lists_the_command(command):
    usage = STERLINGCTL.read_text().split("USAGE\n")[1]
    assert command in usage, f"`{command}` is implemented but not in the usage text"


def test_sterlingctl_is_executable():
    assert STERLINGCTL.stat().st_mode & 0o111, "sterlingctl is not executable"


def test_every_link_between_handoff_docs_resolves():
    broken = []
    for doc in REQUIRED_DOCS:
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            resolved = (doc.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(f"{doc.name} -> {target}")
    assert not broken, f"broken links: {broken}"


def test_no_credential_looking_material_in_the_operator_docs():
    # The access page records WHERE credentials live, never the values. A real
    # secret committed here would be readable by anyone with the repository.
    patterns = (
        re.compile(r"api[_-]?(key|secret)\s*[:=]\s*\S{8,}", re.I),
        re.compile(r"password\s*[:=]\s*\S+", re.I),
        re.compile(r"\baccess[_-]?token\s*[:=]\s*\S{8,}", re.I),
    )
    offenders = []
    for doc in REQUIRED_DOCS:
        text = doc.read_text()
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f"{doc.name}: {pattern.pattern}")
    assert not offenders, f"possible credential material: {offenders}"


def test_the_emergency_card_says_broker_is_the_source_of_truth():
    text = (OPERATOR / "EMERGENCY_CARD.md").read_text().lower()
    assert "source of truth" in text
    assert "safe on" in text


def test_the_promotion_policy_states_the_full_gate():
    text = (EVIDENCE / "PROMOTION_POLICY.md").read_text()
    for clause in ("60", "300", "95%", "10%", "3×", "2×"):
        assert clause in text, f"promotion gate is missing its {clause} clause"


def test_the_promotion_policy_matches_the_code_minimums():
    from app.core.lane_promotion import MIN_SESSIONS, MIN_TRADES

    text = (EVIDENCE / "PROMOTION_POLICY.md").read_text()
    assert f"≥ {MIN_SESSIONS} fully observed sessions" in text
    assert f"≥ {MIN_TRADES} authoritative completed trades" in text


def test_the_lane_manifest_page_lists_every_declared_lane():
    from app.core.lane_registry import LANES

    text = (EVIDENCE / "LANE_MANIFESTS.md").read_text()
    for lane_key in LANES:
        assert lane_key in text, f"{lane_key} is undocumented"


def test_live_execution_is_disabled_at_code_level():
    # The policy page promises this. If it ever becomes True, the promise is a
    # lie and this test is the thing that says so.
    from app.core.lane_registry import LIVE_EXECUTION_ENABLED

    assert LIVE_EXECUTION_ENABLED is False


def test_status_passes_a_real_health_reading():
    # With no health argument every component reports "did not report" and the
    # system reads RECOVERY_REQUIRED forever. Failing closed is right; showing
    # it permanently is how an operator learns to ignore the one line that
    # matters.
    body = STERLINGCTL.read_text()
    assert "system_health()" in body
    assert "operator_dashboard(health)" in body


def test_backup_takes_the_full_section_14_contents():
    body = STERLINGCTL.read_text()
    assert "create_full_backup" in body


def test_report_surfaces_a_broken_read_instead_of_zeros():
    body = STERLINGCTL.read_text()
    assert "verify_source_tables" in body
    assert "extra_gap_codes" in body
