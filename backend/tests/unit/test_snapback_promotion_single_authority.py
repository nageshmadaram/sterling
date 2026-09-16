"""Only one module may decide promotion, and only one may record it.

Two promotion paths eventually disagree, and the disagreement surfaces as a family
being told they may trade by one screen and may not by another.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

ALLOWED_GATE_CALLERS = {
    "app/services/snapback_promotion.py",
    "study/snapback_authoritative_gate.py",
    # Test-facing shim: delegates for scripts, never a production authority.
    "study/snapback_forward_gate.py",
}


def _sources(*relative_dirs):
    for directory in relative_dirs:
        for path in (BACKEND / directory).rglob("*.py"):
            if "test" in path.name:
                continue
            yield path


def test_only_promotion_service_calls_the_authoritative_gate():
    offenders = []
    for path in _sources("app", "study"):
        relative = str(path.relative_to(BACKEND))
        if relative in ALLOWED_GATE_CALLERS:
            continue
        text = path.read_text(encoding="utf-8")
        if "evaluate_authoritative_snapback_gate(" in text:
            offenders.append(relative)

    assert offenders == [], f"gate called outside the promotion service: {offenders}"


def test_only_promotion_service_writes_promotion_records():
    offenders = []
    for path in _sources("app", "study"):
        relative = str(path.relative_to(BACKEND))
        if relative == "app/services/snapback_promotion.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "record_promotion_record(" in text and "def record_promotion_record" not in text:
            offenders.append(relative)

    assert offenders == [], f"promotion records written outside the service: {offenders}"


def test_no_module_writes_a_second_promotion_record_file():
    offenders = []
    for path in _sources("app", "study"):
        text = path.read_text(encoding="utf-8")
        if "promotion_record.json" in text:
            offenders.append(str(path.relative_to(BACKEND)))

    assert offenders == [], f"a second promotion artifact is produced by: {offenders}"


def test_the_reconciliation_report_is_diagnostic_only():
    from study import snapback_reconciliation_report as report

    source = inspect.getsource(report)

    assert "RESEARCH_DIAGNOSTIC_ONLY" in source
    assert "RESEARCH_AUTHORITY" in source
    assert report.RESEARCH_AUTHORITY["promotable"] is False


def test_the_family_endpoint_reads_the_record_rather_than_evaluating():
    from app.services import snapback_family_ops

    source = inspect.getsource(snapback_family_ops)

    assert "PromotionService" in source
    assert "evaluate_forward_gate_from_warehouse" not in source


def test_the_forward_report_consumes_the_service():
    from study import snapback_forward_report as report

    source = inspect.getsource(report)

    assert "PromotionService" in source or "promotion" in source.lower()
