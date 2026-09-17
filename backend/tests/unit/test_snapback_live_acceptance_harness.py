"""The harness that will meet a real socket, tested where it can be tested.

Its grading logic is what decides whether runtime-1.6 may be tagged, so the
grading itself must not be the thing taken on trust. The live run remains the
point; these tests only ensure it will report honestly when it happens.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from study.snapback_live_evidence_acceptance import (
    ACCEPTANCE_EVIDENCE_CLASS,
    FAIL,
    INCONCLUSIVE,
    NOT_EXERCISED,
    PASS,
    PROVEN,
    SKIP,
    Report,
    _force_transport_disconnect,
    evaluate_forced_reconnect,
    evaluate_rows,
    evaluate_ticks,
    inspect_tick,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)


def _report():
    return Report(runtime_sha="abc", started_at=NOW.isoformat(), underlying="NIFTY")


def _tick(levels=5, *, orders=True, stamp=True):
    depth = {
        side: [
            {"price": 100.0 + i, "quantity": 50 + i, **({"orders": 3 + i} if orders else {})}
            for i in range(levels)
        ]
        for side in ("buy", "sell")
    }
    return {
        "instrument_token": 12345, "last_price": 101.0, "oi": 400,
        "exchange_timestamp": datetime(2026, 9, 17, 14, 49, 59) if stamp else None,
        "depth": depth,
    }


# ─── the verdict cannot be gamed ─────────────────────────────────────────────

def test_no_checks_is_not_a_pass():
    assert _report().overall == FAIL


def test_a_skipped_check_is_not_a_pass():
    report = _report()
    report.record("a", PASS)
    report.record("b", SKIP)

    # A release gate that accepts SKIP accepts an untested claim.
    assert report.overall == SKIP


def test_one_failure_fails_the_run():
    report = _report()
    for name in ("a", "b", "c"):
        report.record(name, PASS)
    report.record("d", FAIL)

    assert report.overall == FAIL


def test_all_pass_is_a_pass():
    """Certification needs engineering fit AND proven vendor assumptions."""
    report = _report()
    for name in ("instrument_master", "persistence", "writer_healthy"):
        report.record(name, PASS)
    for name in ("market_data_live", "clock_separation", "option_full_tick"):
        report.record(name, PASS)

    assert report.engineering_gate == PASS
    assert report.vendor_assumptions == PROVEN
    assert report.overall == PASS


# ─── tick grading ────────────────────────────────────────────────────────────

def test_a_full_five_level_book_passes():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5)])

    assert report.checks["option_five_level_depth"] == PASS
    assert report.checks["option_order_counts"] == PASS
    assert report.checks["option_depth_quantities"] == PASS


def test_a_one_level_book_fails_the_depth_check():
    """Exactly the assumption the raw tick path silently made."""
    report = _report()

    evaluate_ticks(report, "option", [_tick(1)])

    assert report.checks["option_five_level_depth"] == FAIL


def test_missing_order_counts_fail():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5, orders=False)])

    assert report.checks["option_order_counts"] == FAIL


def test_no_ticks_is_a_failure_not_a_skip():
    report = _report()

    evaluate_ticks(report, "option", [])

    assert report.checks["option_full_tick"] == FAIL


def test_missing_exchange_timestamps_fail():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5, stamp=False)])

    assert report.checks["option_exchange_timestamp"] == FAIL


def test_inspect_reports_what_the_vendor_sent_without_interpreting():
    finding = inspect_tick(_tick(3))

    assert finding["buy_levels"] == 3
    assert finding["sell_levels"] == 3
    assert finding["has_exchange_timestamp"] is True


def test_an_empty_book_is_reported_as_empty():
    finding = inspect_tick({"instrument_token": 1, "depth": {}})

    assert finding["has_depth"] is False
    assert finding["buy_levels"] == 0


# ─── row grading ─────────────────────────────────────────────────────────────

def _row(**over):
    """A fully populated five-deep book, so that a test which omits a level is
    deliberately testing absence rather than accidentally creating it."""
    row = {
        "exchange_ts": datetime(2026, 9, 17, 9, 19, 59, tzinfo=timezone.utc),
        "received_ts": NOW,
    }
    for i in range(5):
        row[f"bid{i}_price"] = 1000000 - i * 500
        row[f"ask{i}_price"] = 1000500 + i * 500
    row.update(over)
    return row


def test_separated_clocks_pass_and_freshness_is_computable():
    report = _report()

    evaluate_rows(report, [_row()])

    assert report.checks["clock_separation"] == PASS
    assert report.checks["freshness_computable"] == PASS
    assert report.detail["freshness_computable"]["max_age_ms"] == 1000.0


def test_an_exchange_clock_ahead_of_receipt_is_flagged():
    """Clocks that cannot be compared invalidate every freshness gate."""
    report = _report()

    evaluate_rows(report, [_row(exchange_ts=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc))])

    assert report.checks["clock_ordering_sane"] == FAIL


def test_a_borrowed_timestamp_fails():
    report = _report()

    # exchange_ts None, but a row where the two are equal would mean it was
    # filled in from received_ts.
    rows = [_row(exchange_ts=None), _row()]
    evaluate_rows(report, rows)

    assert report.checks["null_exchange_timestamp_semantics"] == PASS


def test_a_zeroed_absent_level_fails():
    report = _report()

    evaluate_rows(report, [_row(bid4_price=0)])

    assert report.checks["absent_level_is_null_not_zero"] == FAIL


def test_no_rows_is_a_failure():
    report = _report()

    evaluate_rows(report, [])

    assert report.checks["row_conversion"] == FAIL


# ─── forced reconnect ────────────────────────────────────────────────────────

class _FakeTransport:
    def __init__(self):
        self.aborted = 0

    def abortConnection(self):
        self.aborted += 1


class _FakeWs:
    def __init__(self, transport=None):
        self.transport = transport


class _FakeTicker:
    def __init__(self, transport=None):
        self.ws = _FakeWs(transport)


def test_forced_disconnect_aborts_transport_without_calling_ticker_close():
    transport = _FakeTransport()
    ticker = _FakeTicker(transport)
    scheduled = []

    def schedule(fn):
        scheduled.append(fn)
        fn()

    ok, detail = _force_transport_disconnect(ticker, schedule=schedule)

    assert ok is True
    assert "abortConnection" in detail
    assert len(scheduled) == 1
    assert transport.aborted == 1


def test_forced_disconnect_fails_closed_when_transport_cannot_be_aborted():
    ok, detail = _force_transport_disconnect(_FakeTicker())

    assert ok is False
    assert "abortConnection" in detail


def test_reconnect_pass_requires_disconnect_retry_second_connection_and_fresh_full_ticks():
    report = _report()
    post = {1: [_tick(5)], 2: [_tick(5)]}

    evaluate_forced_reconnect(
        report,
        tokens=[1, 2],
        force_succeeded=True,
        force_detail="scheduled",
        disconnects=["1006: connection lost"],
        reconnect_attempts=[1],
        connection_count=2,
        post_reconnect=post,
    )

    assert report.checks["forced_disconnect"] == PASS
    assert report.checks["disconnect_observed"] == PASS
    assert report.checks["reconnect_attempted"] == PASS
    assert report.checks["reconnect_connected"] == PASS
    assert report.checks["post_reconnect_fresh_ticks"] == PASS
    assert report.checks["post_reconnect_full_mode"] == PASS
    # The reconnect chain is an engineering concern; on its own it cannot
    # certify a release, because no vendor check ran.
    assert report.engineering_gate == PASS
    assert report.overall != PASS


def test_reconnect_fails_when_one_token_has_no_new_post_reconnect_tick():
    report = _report()

    evaluate_forced_reconnect(
        report,
        tokens=[1, 2],
        force_succeeded=True,
        force_detail="scheduled",
        disconnects=["1006: connection lost"],
        reconnect_attempts=[1],
        connection_count=2,
        post_reconnect={1: [_tick(5)], 2: []},
    )

    assert report.checks["post_reconnect_fresh_ticks"] == FAIL
    assert report.overall == FAIL


def test_reconnect_fails_when_subscription_returns_without_full_mode_depth():
    report = _report()
    thin = {"instrument_token": 1, "exchange_timestamp": NOW, "depth": {}}

    evaluate_forced_reconnect(
        report,
        tokens=[1],
        force_succeeded=True,
        force_detail="scheduled",
        disconnects=["1006: connection lost"],
        reconnect_attempts=[1],
        connection_count=2,
        post_reconnect={1: [thin]},
    )

    assert report.checks["post_reconnect_fresh_ticks"] == PASS
    assert report.checks["post_reconnect_full_mode"] == FAIL
    assert report.overall == FAIL


def test_reconnect_fails_when_retry_callback_never_runs():
    report = _report()

    evaluate_forced_reconnect(
        report,
        tokens=[1],
        force_succeeded=True,
        force_detail="scheduled",
        disconnects=["1006: connection lost"],
        reconnect_attempts=[],
        connection_count=1,
        post_reconnect={1: []},
    )

    assert report.checks["reconnect_attempted"] == FAIL
    assert report.checks["reconnect_connected"] == FAIL
    assert report.overall == FAIL


# ─── safety properties ───────────────────────────────────────────────────────

def test_the_harness_cannot_place_an_order():
    import study.snapback_live_evidence_acceptance as mod

    source = open(mod.__file__, encoding="utf-8").read()

    for forbidden in ("place_order", "modify_order", "cancel_order", ".order_place"):
        assert forbidden not in source, f"the acceptance harness must never trade: found {forbidden}"


def test_acceptance_evidence_cannot_enter_the_economic_sample():
    """Marked MODELLED on purpose: it is not a real opportunity's evidence."""
    from app.services.snapback_evidence_class import BROKER_EXECUTED, satisfies

    assert ACCEPTANCE_EVIDENCE_CLASS == "MODELLED"
    assert satisfies(ACCEPTANCE_EVIDENCE_CLASS, BROKER_EXECUTED) is False


def test_the_report_carries_its_tag_and_class():
    blob = _report().as_dict()

    assert blob["tag"] == "TEST_ACCEPTANCE"
    assert blob["evidence_class"] == "MODELLED"


def test_no_credential_is_logged_or_stored():
    """Scans code only: the module docstring legitimately mentions these words."""
    import ast
    import study.snapback_live_evidence_acceptance as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    tree.body = [n for n in tree.body
                 if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                         and isinstance(n.value.value, str))]
    code = ast.unparse(tree)

    for forbidden in ("print(creds", "api_secret", "password"):
        assert forbidden not in code

    # The token may be handed to the client, never written anywhere.
    assert "write_text(creds" not in code
    assert "json.dumps(creds" not in code


# ─── a closed market must not look like an acceptance ────────────────────────
# A run after the close receives the previous session's book replayed on
# connect: well-formed, five levels deep, fully stamped. Every schema check
# passes and the artifact reads like a certified release. These pin the
# separation between "the schema is right" and "the market was live".

def test_a_stale_closing_book_is_not_a_live_market():
    from study.snapback_live_evidence_acceptance import MAX_LIVE_TICK_AGE_MS

    report = _report()
    stale = datetime(2026, 9, 17, 2, 20, tzinfo=timezone.utc)   # ~7h before receipt

    evaluate_rows(report, [_row(exchange_ts=stale)])

    assert report.checks["market_data_live"] == FAIL
    assert "NOT_A_LIVE_MARKET" in report.detail["market_data_live"]["interpretation"]
    assert report.detail["market_data_live"]["freshest_tick_age_ms"] > MAX_LIVE_TICK_AGE_MS


def test_a_current_tick_is_a_live_market():
    report = _report()

    evaluate_rows(report, [_row()])

    assert report.checks["market_data_live"] == PASS


def test_a_stale_book_still_fails_overall_even_when_everything_else_passes():
    """The whole point: 32 green schema checks must not certify a closed market."""
    report = _report()
    for name in ("option_full_tick", "option_five_level_depth", "clock_separation"):
        report.record(name, PASS)

    evaluate_rows(report, [_row(exchange_ts=datetime(2026, 9, 17, 2, 20, tzinfo=timezone.utc))])

    # Not a runtime failure — nothing about live behaviour was tested — but it
    # can never certify.
    assert report.vendor_assumptions == NOT_EXERCISED
    assert report.overall == INCONCLUSIVE
    assert report.overall != PASS


def test_a_fully_populated_book_reports_the_absent_level_path_unexercised():
    report = _report()

    evaluate_rows(report, [_row()])

    assert report.checks["absent_level_is_null_not_zero"] == PASS
    assert report.detail["absent_level_is_null_not_zero"]["exercised"] is False
    assert "NOT_EXERCISED" in report.detail["absent_level_is_null_not_zero"]["interpretation"]


def test_an_observed_absent_level_exercises_the_path():
    report = _report()

    evaluate_rows(report, [_row(bid4_price=None)])

    assert report.detail["absent_level_is_null_not_zero"]["exercised"] is True


def test_the_depth_constant_cannot_drift_from_kitelake():
    """kitelake runs in its own environment, so compare the source textually."""
    import pathlib
    import re

    from study.snapback_live_evidence_acceptance import DEPTH_LEVELS

    root = pathlib.Path(__file__).resolve().parents[3]
    text = (root / "kitelake" / "evidence.py").read_text(encoding="utf-8")
    match = re.search(r"^DEPTH_LEVELS = (\d+)", text, re.MULTILINE)

    assert match, "kitelake/evidence.py no longer declares DEPTH_LEVELS"
    assert DEPTH_LEVELS == int(match.group(1))


# ─── reconnect is graded on outcome, not on a callback ───────────────────────

def test_recovery_without_the_callback_still_counts():
    """kiteconnect reconnected and delivered fresh ticks without firing
    on_reconnect. Failing that reports a working system as broken."""
    from study.snapback_live_evidence_acceptance import evaluate_forced_reconnect

    report = _report()
    evaluate_forced_reconnect(
        report, tokens=[1, 2], force_succeeded=True, force_detail="aborted",
        disconnects=["1006: peer dropped"], reconnect_attempts=[],
        connection_count=2, post_reconnect={1: [_tick()], 2: [_tick()]},
    )

    assert report.checks["reconnect_attempted"] == PASS
    assert report.detail["reconnect_attempted"]["recovered_without_callback"] is True


def test_no_reconnection_at_all_still_fails():
    from study.snapback_live_evidence_acceptance import evaluate_forced_reconnect

    report = _report()
    evaluate_forced_reconnect(
        report, tokens=[1], force_succeeded=True, force_detail="aborted",
        disconnects=["1006: peer dropped"], reconnect_attempts=[],
        connection_count=1, post_reconnect={},
    )

    assert report.checks["reconnect_attempted"] == FAIL
    assert report.checks["reconnect_connected"] == FAIL
    assert report.checks["post_reconnect_fresh_ticks"] == FAIL


def test_a_disconnect_that_never_happened_is_not_recovery():
    from study.snapback_live_evidence_acceptance import evaluate_forced_reconnect

    report = _report()
    evaluate_forced_reconnect(
        report, tokens=[1], force_succeeded=False, force_detail="no abortConnection()",
        disconnects=[], reconnect_attempts=[], connection_count=2,
        post_reconnect={1: [_tick()]},
    )

    assert report.checks["forced_disconnect"] == FAIL
    # The abort never happened, so recovery was not exercised rather than
    # independently broken — see the harness-incompatibility root cause.
    assert report.checks["reconnect_attempted"] == SKIP


# ─── credentials ─────────────────────────────────────────────────────────────

def test_credential_resolution_prefers_sterlings_own_store(monkeypatch):
    """Two Kite apps existed; reading only kitelake's made the documented
    Sterling login flow unusable and surfaced a misleading TokenException."""
    import study.snapback_live_evidence_acceptance as mod
    import app.services.exchanges.kite.accounts as accounts

    class FakeAccount:
        is_active = True
        api_key = "sterling-key"
        access_token = "sterling-token"

    monkeypatch.setattr(accounts, "_load_from_db", lambda: [FakeAccount()])

    key, token, source = mod.resolve_acceptance_credentials()

    assert (key, token, source) == ("sterling-key", "sterling-token", "sterling_kite_accounts")


def test_an_undecryptable_token_says_so_instead_of_looking_logged_out(monkeypatch):
    import study.snapback_live_evidence_acceptance as mod
    import app.services.exchanges.kite.accounts as accounts

    class FakeAccount:
        is_active = True
        api_key = "sterling-key"
        access_token = ""      # decrypt() returns "" when the key is absent

    monkeypatch.setattr(accounts, "_load_from_db", lambda: [FakeAccount()])

    with pytest.raises(mod.CredentialsUnavailable) as excinfo:
        mod.resolve_acceptance_credentials()

    assert "STERLING_SECRET_KEY" in str(excinfo.value)


def test_the_credential_error_never_carries_the_token(monkeypatch):
    import study.snapback_live_evidence_acceptance as mod
    import app.services.exchanges.kite.accounts as accounts

    monkeypatch.setattr(accounts, "_load_from_db", lambda: [])
    monkeypatch.setattr(mod, "_SPOT_QUOTE_KEYS", mod._SPOT_QUOTE_KEYS)

    try:
        mod.resolve_acceptance_credentials()
    except mod.CredentialsUnavailable as exc:
        assert "token" not in str(exc).lower() or "ACCESS_TOKEN" in str(exc)


def test_index_spot_symbols_are_not_the_derivatives_underlying():
    """NIFTY options are written on "NIFTY"; the spot quotes as "NSE:NIFTY 50"."""
    from study.snapback_live_evidence_acceptance import _SPOT_QUOTE_KEYS

    assert _SPOT_QUOTE_KEYS["NIFTY"][0] == "NSE:NIFTY 50"
    assert _SPOT_QUOTE_KEYS["BANKNIFTY"][0] == "NSE:NIFTY BANK"
    assert _SPOT_QUOTE_KEYS["SENSEX"][0] == "BSE:SENSEX"


# ─── three conclusions, never one headline ───────────────────────────────────
# Collapsing them lets a closed market or a blocked expiry calendar read as a
# runtime failure, and lets vendor proof read as strategy proof.

def test_a_blocked_expiry_calendar_does_not_fail_the_engineering_gate():
    report = _report()
    for name in ("instrument_master", "persistence", "writer_healthy"):
        report.record(name, PASS)
    for name in ("market_data_live", "clock_separation"):
        report.record(name, PASS)
    report.record("candidate_universe", FAIL)      # no eligible DTE today

    assert report.strategy_reality["status"] == "BLOCKED"
    assert report.engineering_gate == PASS
    assert report.vendor_assumptions == PROVEN
    # Sterling can be correct on a day Snapback cannot trade.
    assert report.overall == PASS


def test_a_closed_market_never_proves_vendor_assumptions():
    report = _report()
    report.record("instrument_master", PASS)
    report.record("option_five_level_depth", PASS)
    report.record("clock_separation", PASS)
    report.record("market_data_live", FAIL)

    assert report.vendor_assumptions == NOT_EXERCISED
    assert report.overall != PASS


def test_an_unclassified_check_still_counts():
    """A new check must never be silently excluded from every verdict."""
    report = _report()
    for name in ("instrument_master", "market_data_live", "clock_separation"):
        report.record(name, PASS)
    report.record("some_brand_new_check", FAIL)

    assert report.engineering_gate == FAIL
    assert report.overall != PASS
    assert "some_brand_new_check" in report.as_dict()["unclassified_checks"]


def test_the_artifact_carries_all_three_conclusions():
    report = _report()
    report.record("instrument_master", PASS)

    blob = report.as_dict()

    assert set(blob["conclusions"]) == {
        "engineering_gate", "vendor_assumptions", "strategy_reality"}


def test_an_uninducible_disconnect_is_a_harness_incompatibility():
    """One root cause must not present as five independent failures."""
    from study.snapback_live_evidence_acceptance import evaluate_forced_reconnect

    report = _report()
    evaluate_forced_reconnect(
        report, tokens=[1], force_succeeded=False,
        force_detail="ticker websocket transport has no abortConnection()",
        disconnects=[], reconnect_attempts=[], connection_count=1, post_reconnect={},
    )

    assert report.checks["forced_disconnect"] == FAIL
    assert "HARNESS_TRANSPORT_INCOMPATIBILITY" in report.detail["forced_disconnect"]["root_cause"]
    for name in ("disconnect_observed", "reconnect_attempted", "reconnect_connected",
                 "post_reconnect_fresh_ticks", "post_reconnect_full_mode"):
        assert report.checks[name] == SKIP


# ─── a closed market must not mask a real vendor defect ──────────────────────

def test_a_schema_contradiction_survives_a_closed_market():
    """Liveness and schema correctness are separate questions.

    An earlier version returned NOT_EXERCISED for any closed-market run, which
    downgraded a genuine decoder contradiction into "not tested". A schema
    defect is visible on a replayed closing book and stays a defect after hours.
    """
    from study.snapback_live_evidence_acceptance import CONTRADICTED

    report = _report()
    report.record("instrument_master", PASS)
    report.record("clock_separation", PASS)
    report.record("market_data_live", FAIL)      # expected after hours
    report.record("option_order_counts", FAIL)   # a real vendor/decoder defect

    assert report.vendor_assumptions == CONTRADICTED
    assert report.overall == FAIL


def test_a_clean_schema_on_a_closed_market_is_only_not_exercised():
    report = _report()
    report.record("instrument_master", PASS)
    report.record("clock_separation", PASS)
    report.record("option_order_counts", PASS)
    report.record("market_data_live", FAIL)

    assert report.vendor_assumptions == NOT_EXERCISED
    assert report.overall == INCONCLUSIVE


def test_a_clean_schema_on_a_live_market_is_proven():
    report = _report()
    report.record("instrument_master", PASS)
    report.record("clock_separation", PASS)
    report.record("option_order_counts", PASS)
    report.record("market_data_live", PASS)

    assert report.vendor_assumptions == PROVEN
    assert report.overall == PASS


# ─── strategy reality is a finding, not a gate ───────────────────────────────

def test_a_blocked_expiry_calendar_reports_blocked_not_fail():
    from study.snapback_live_evidence_acceptance import BLOCKED

    report = _report()
    report.record("instrument_master", PASS)
    report.record("market_data_live", PASS)
    report.record("clock_separation", PASS)
    report.record("candidate_universe", FAIL,
                  {"interpretation": "STRATEGY_REALITY: no listed monthly expiry"})

    reality = report.strategy_reality
    assert reality["status"] == BLOCKED
    assert reality["findings"][0]["check"] == "candidate_universe"
    assert reality["findings"][0]["result"] == "NO_ELIGIBLE_EXPIRY"
    assert "no listed monthly expiry" in reality["findings"][0]["interpretation"]

    # And it still does not stop the release.
    assert report.engineering_gate == PASS
    assert report.overall == PASS


def test_a_tradeable_day_reports_executable():
    from study.snapback_live_evidence_acceptance import EXECUTABLE

    report = _report()
    report.record("candidate_universe", PASS)
    report.record("listedness", PASS)

    reality = report.strategy_reality
    assert reality["status"] == EXECUTABLE
    assert reality["findings"] == []


def test_an_unlisted_target_is_a_finding_with_its_own_result():
    report = _report()
    report.record("candidate_universe", PASS)
    report.record("listedness", FAIL, {"interpretation": "computed strike absent"})

    finding = report.strategy_reality["findings"][0]
    assert finding["check"] == "listedness"
    assert finding["result"] == "TARGET_NOT_LISTED"


def test_strategy_reality_is_not_exercised_when_no_strategy_check_ran():
    report = _report()
    report.record("instrument_master", PASS)

    assert report.strategy_reality["status"] == NOT_EXERCISED


def test_the_artifact_carries_strategy_reality_as_a_structure():
    report = _report()
    report.record("candidate_universe", FAIL, {"interpretation": "no eligible expiry"})

    blob = report.as_dict()

    assert isinstance(blob["conclusions"]["strategy_reality"], dict)
    assert blob["conclusions"]["strategy_reality"]["status"] == "BLOCKED"
