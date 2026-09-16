"""1.5 P0-CAPITAL: no reconciliation snapshot is not a clean one.

`snapshot is None or snapshot.clean` made "we have never checked" indistinguishable
from "we checked and the books agree". Behind the economic gate that is a live
race: a restarted process, evidence PASSED, broker connected, health HEALTHY, no
reconciliation yet — and LIVE_ELIGIBLE.

The invariant the reconciliation module already states must hold at the consumer
too: unknown is never clean. Absent, stale, wrong-account and unreadable all mean
RECONCILING.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _snapshot(**over):
    from app.services.snapback_reconciliation import ReconciliationSnapshot

    base = dict(
        clean=True,
        account_id="KITE-FAMILY",
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    base.update(over)
    return ReconciliationSnapshot(**base)


@pytest.fixture(autouse=True)
def _bound_account(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapback_family_account.binding_configured", lambda: True,
    )
    from app.services.snapback_family_account import FamilyAccountBinding

    monkeypatch.setattr(
        "app.services.snapback_family_account.configured_binding",
        lambda: FamilyAccountBinding(user_id="default", account_id="KITE-FAMILY"),
    )


# --------------------------------------------------------------- the consumer


def test_no_snapshot_is_not_clean(monkeypatch):
    from app.services import snapback_readiness

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: None,
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_a_fresh_clean_snapshot_for_the_bound_account_is_clean(monkeypatch):
    from app.services import snapback_readiness

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: _snapshot(),
    )

    assert snapback_readiness._reconciliation_clean() is True


def test_a_stale_snapshot_is_not_clean(monkeypatch):
    """A snapshot older than a reconciliation cycle describes a book that may
    have moved since."""
    from app.services import snapback_readiness

    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: _snapshot(observed_at=old),
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_a_snapshot_for_another_account_is_not_clean(monkeypatch):
    """Reconciling somebody else's book proves nothing about this one."""
    from app.services import snapback_readiness

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: _snapshot(account_id="KITE-SOMEONE-ELSE"),
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_a_dirty_snapshot_is_not_clean(monkeypatch):
    from app.services import snapback_readiness

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: _snapshot(clean=False),
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_an_unreadable_reconciliation_is_not_clean(monkeypatch):
    from app.services import snapback_readiness

    def explode(**_k):
        raise RuntimeError("reconciliation store unreadable")

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation", explode,
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_an_unparseable_timestamp_is_not_clean(monkeypatch):
    from app.services import snapback_readiness

    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: _snapshot(observed_at="not-a-time"),
    )

    assert snapback_readiness._reconciliation_clean() is False


# ------------------------------------------------------------ account scoping


def test_latest_reconciliation_accepts_an_account():
    """The arm endpoint already calls it this way; before 1.5 that raised
    TypeError at the live boundary, and no test caught it because the endpoint
    test replaced the admission function wholesale."""
    import inspect

    from app.services.snapback_reconciliation import latest_reconciliation

    signature = inspect.signature(latest_reconciliation)

    assert "account_id" in signature.parameters


def test_a_snapshot_is_only_returned_for_the_account_asked_about():
    from app.services.snapback_reconciliation import _remember, latest_reconciliation

    _remember(_snapshot(account_id="KITE-A"))

    assert latest_reconciliation(account_id="KITE-A") is not None
    assert latest_reconciliation(account_id="KITE-B") is None


def test_snapshots_for_two_accounts_do_not_overwrite_each_other():
    from app.services.snapback_reconciliation import _remember, latest_reconciliation

    _remember(_snapshot(account_id="KITE-A", clean=True))
    _remember(_snapshot(account_id="KITE-B", clean=False))

    assert latest_reconciliation(account_id="KITE-A").clean is True
    assert latest_reconciliation(account_id="KITE-B").clean is False


def test_asking_without_an_account_still_returns_the_most_recent():
    from app.services.snapback_reconciliation import _remember, latest_reconciliation

    _remember(_snapshot(account_id="KITE-A"))

    assert latest_reconciliation() is not None


# ------------------------------------------------------------------- the TTL


def test_the_ttl_is_bounded_to_about_one_cycle():
    from app.services.snapback_reconciliation import RECONCILIATION_TTL_SECONDS

    assert 0 < RECONCILIATION_TTL_SECONDS <= 120


def test_snapshot_freshness_is_computable():
    from app.services.snapback_reconciliation import snapshot_is_fresh

    assert snapshot_is_fresh(_snapshot()) is True

    old = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
    assert snapshot_is_fresh(_snapshot(observed_at=old)) is False


def test_a_naive_timestamp_is_not_trusted():
    """A timestamp without a zone could be off by hours in either direction."""
    from app.services.snapback_reconciliation import snapshot_is_fresh

    naive = datetime.now().replace(tzinfo=None).isoformat()

    assert snapshot_is_fresh(_snapshot(observed_at=naive)) is False


# ------------------------------------------------- what reconciliation compares


def test_live_reconciliation_does_not_use_the_paper_warehouse():
    """The paper prospective warehouse is not the live book. Comparing the broker
    against it would report a real live position as an unknown external one, and
    a real paper position as a missing broker one."""
    import inspect

    from app.services import snapback_reconciliation

    source = inspect.getsource(snapback_reconciliation.reconcile_family_account)

    assert "get_active_paper_positions" not in source


def test_live_reconciliation_passes_canonical_intents():
    import inspect

    from app.services import snapback_reconciliation

    source = inspect.getsource(snapback_reconciliation.reconcile_family_account)

    # An empty intent list asserts "nothing is in flight", which is exactly the
    # claim that cannot be made without consulting the order journal.
    assert "sterling_intents=[]" not in source


# ----------------------------------------------------- protection is checked


def test_an_unprotected_live_position_is_a_mismatch():
    """PROTECTION_MISSING was declared as a mismatch code and never emitted.
    A live option position with no working stop is the single most expensive
    thing reconciliation can fail to notice."""
    import asyncio

    from app.services.snapback_reconciliation import (
        PROTECTION_MISSING, reconcile_account,
    )

    class _Client:
        async def get_positions_raw(self):
            return {"net": [{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}]}
        async def get_orders(self):
            return []
        async def get_trades(self):
            return []

    snapshot = asyncio.run(reconcile_account(
        client=_Client(),
        account_id="KITE-FAMILY",
        sterling_positions=[{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}],
        sterling_intents=[],
        protection_for=lambda symbol: None,   # nothing protecting it
    ))

    assert snapshot.clean is False
    assert any(m.code == PROTECTION_MISSING for m in snapshot.mismatches)


def test_a_protected_live_position_is_not_flagged():
    import asyncio

    from app.services.snapback_reconciliation import (
        PROTECTION_MISSING, reconcile_account,
    )

    class _Client:
        async def get_positions_raw(self):
            return {"net": [{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}]}
        async def get_orders(self):
            return []
        async def get_trades(self):
            return []

    snapshot = asyncio.run(reconcile_account(
        client=_Client(),
        account_id="KITE-FAMILY",
        sterling_positions=[{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}],
        sterling_intents=[],
        protection_for=lambda symbol: {"state": "ACTIVE", "quantity": 75},
    ))

    assert not any(m.code == PROTECTION_MISSING for m in snapshot.mismatches)


def test_unknown_protection_state_is_not_treated_as_protected():
    import asyncio

    from app.services.snapback_reconciliation import (
        PROTECTION_MISSING, reconcile_account,
    )

    class _Client:
        async def get_positions_raw(self):
            return {"net": [{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}]}
        async def get_orders(self):
            return []
        async def get_trades(self):
            return []

    snapshot = asyncio.run(reconcile_account(
        client=_Client(),
        account_id="KITE-FAMILY",
        sterling_positions=[{"tradingsymbol": "NIFTY26OCT25000PE", "quantity": 75,
                             "exchange": "NFO"}],
        sterling_intents=[],
        protection_for=lambda symbol: {"state": "RECONCILING", "quantity": 75},
    ))

    assert any(m.code == PROTECTION_MISSING for m in snapshot.mismatches)
