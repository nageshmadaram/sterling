"""CI proof, per context, bound to one commit.

"CI was green" cannot say which contexts ran, and the green run people remember
is sometimes against a different commit. These tests are about that gap.
"""
from __future__ import annotations

import pytest

from app.core.ci_certification import (
    REQUIRED_CONTEXTS,
    CIRecordStore,
    ci_report,
    render_ci,
)
from app.services.ci_context_sync import sync_ci_contexts

SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture()
def store(tmp_path):
    return CIRecordStore(tmp_path)


def _record_all(store, sha=SHA, conclusion="success"):
    for i, context in enumerate(REQUIRED_CONTEXTS):
        store.record(runtime_sha=sha, context=context,
                     conclusion=conclusion, run_id=str(1000 + i))


class TestTheRequiredNine:
    def test_the_contexts_match_the_specification(self):
        assert len(REQUIRED_CONTEXTS) == 9
        assert "Snapback release gate" in REQUIRED_CONTEXTS
        assert "E2E Playwright (webkit)" in REQUIRED_CONTEXTS

    def test_nothing_recorded_is_unknown_not_failed(self, store):
        report = ci_report(SHA, store=store)
        assert len(report.unknowns) == 9
        assert report.failures == ()
        assert report.all_passed is False

    def test_all_nine_succeeding_passes(self, store):
        _record_all(store)
        assert ci_report(SHA, store=store).all_passed is True

    def test_eight_of_nine_does_not_pass(self, store):
        for context in REQUIRED_CONTEXTS[:-1]:
            store.record(runtime_sha=SHA, context=context,
                         conclusion="success", run_id="1")
        report = ci_report(SHA, store=store)
        assert report.all_passed is False
        assert "A context with no record is not a context that passed." in render_ci(report)

    def test_one_failure_fails(self, store):
        _record_all(store)
        store.record(runtime_sha=SHA, context="Frontend Type Check",
                     conclusion="failure", run_id="9")
        report = ci_report(SHA, store=store)
        assert report.all_passed is False
        assert [r.context for r in report.failures] == ["Frontend Type Check"]


class TestRecordsBelongToOneCommit:
    def test_a_foreign_sha_satisfies_nothing(self, store):
        _record_all(store, OTHER_SHA)
        assert ci_report(SHA, store=store).all_passed is False
        assert ci_report(OTHER_SHA, store=store).all_passed is True

    def test_a_short_sha_is_refused(self, store):
        with pytest.raises(ValueError, match="exact 40-character commit"):
            store.record(runtime_sha="49cc66ee", context="Frontend Type Check",
                         conclusion="success", run_id="1")

    def test_an_unknown_context_name_is_refused(self, store):
        # A typo would otherwise become a tenth context while a required one
        # stayed permanently unrecorded.
        with pytest.raises(KeyError):
            store.record(runtime_sha=SHA, context="Frontend Typecheck",
                         conclusion="success", run_id="1")

    def test_a_success_must_carry_its_run_id(self, store):
        with pytest.raises(ValueError, match="run id"):
            store.record(runtime_sha=SHA, context="Frontend Type Check",
                         conclusion="success", run_id="")


class TestTheSync:
    def _run(self, name, conclusion="success", run_id=1, completed="2026-09-18T07:00:00Z"):
        return {"name": name, "conclusion": conclusion, "id": run_id,
                "completed_at": completed, "html_url": f"https://x/{run_id}"}

    def test_it_records_only_the_required_contexts(self, store):
        runs = [self._run(c) for c in REQUIRED_CONTEXTS]
        runs.append(self._run("Parity Matrix (kite)"))
        result = sync_ci_contexts(SHA, store=store, runs=runs)
        assert len(result.recorded) == 9
        assert "Parity Matrix (kite)" not in result.recorded

    def test_a_still_running_check_is_left_with_no_record(self, store):
        runs = [self._run("Frontend Type Check", conclusion=None)]
        result = sync_ci_contexts(SHA, store=store, runs=runs)
        assert result.recorded == ()
        assert "Frontend Type Check" in result.missing

    def test_a_failure_is_recorded_as_a_failure(self, store):
        runs = [self._run("Frontend Type Check", conclusion="failure")]
        sync_ci_contexts(SHA, store=store, runs=runs)
        assert ci_report(SHA, store=store).by_context["Frontend Type Check"].status == "FAIL"

    def test_a_rerun_supersedes_the_earlier_attempt(self, store):
        runs = [
            self._run("Frontend Type Check", conclusion="failure", run_id=1,
                      completed="2026-09-18T07:00:00Z"),
            self._run("Frontend Type Check", conclusion="success", run_id=2,
                      completed="2026-09-18T08:00:00Z"),
        ]
        sync_ci_contexts(SHA, store=store, runs=runs)
        record = ci_report(SHA, store=store).by_context["Frontend Type Check"]
        assert record.status == "PASS" and record.run_id == "2"

    def test_an_unreachable_github_is_an_error_not_an_empty_pass(self, store, monkeypatch):
        def _boom(sha, repo=None):
            raise RuntimeError("gh: not authenticated")

        monkeypatch.setattr("app.services.ci_context_sync.check_runs_for", _boom)
        result = sync_ci_contexts(SHA, store=store)
        assert "not authenticated" in result.error
        assert ci_report(SHA, store=store).all_passed is False


class TestTheReleaseGate:
    def test_the_gate_is_derived_and_cannot_be_attested(self, tmp_path):
        from app.services.release_certification import CertificationStore

        with pytest.raises(ValueError, match="derived from its own register"):
            CertificationStore(tmp_path).attest(SHA, "remote_ci", "PASS",
                                                attested_by="operator")

    def test_the_gate_reports_the_run_ids(self, store, monkeypatch):
        from app.services import release_certification as cert

        monkeypatch.setattr("app.core.ci_certification.CIRecordStore",
                            lambda *a, **k: store)
        _record_all(store)
        gate = cert._remote_ci_gate(SHA)
        assert gate.status == "PASS"
        assert "1000" in gate.detail
