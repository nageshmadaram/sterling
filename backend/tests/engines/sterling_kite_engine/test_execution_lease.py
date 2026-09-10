"""Cross-PROCESS execution ownership.

The in-process guards these leases back up are correct on one event loop, so a
threaded test cannot fail in the way that matters. These use real processes
against one SQLite file: that is the arrangement a second engine worker, a
restart overlapping its predecessor, or an operator script actually creates.

Broker protocol fixtures test safety; these are not market-performance data.
"""
import multiprocessing as mp
import time

import pytest

from app.services import db
from app.services.kite_engine import execution_lease as lease
from app.services.kite_engine import monitor, order_journal as journal, positions, state


@pytest.fixture(autouse=True)
def isolated_leases():
    db.init()
    lease.clear_for_tests()
    journal.clear_for_tests('u')
    monitor._exiting.clear()
    yield
    lease.clear_for_tests()
    journal.clear_for_tests('u')
    monitor._exiting.clear()


KEY = dict(account_id='account', uid='u', symbol='OPT')


# Module level so a forked child can run it.
def _try_acquire(_n):
    return lease.acquire(lease.EXIT, **KEY) is not None


def _try_claim(_n):
    return monitor._exiting.claim(('u', 'OPT'), account_id='account')


def _try_project(args):
    intent_key, version = args
    return journal.mark_projected(intent_key, version)


def _race(fn, arg, workers=8):
    ctx = mp.get_context('fork')
    with ctx.Pool(workers) as pool:
        return list(pool.map(fn, [arg] * workers if not isinstance(arg, list) else arg))


def test_only_one_process_takes_the_exit_lease():
    assert sum(_race(_try_acquire, 0)) == 1


def test_only_one_process_claims_an_exit_for_a_live_position():
    """``_exiting`` used to be a plain set, so every process passed its own check
    and every process placed a SELL."""
    assert sum(_race(_try_claim, 0)) == 1


def test_a_second_process_is_refused_while_the_holder_is_alive():
    assert lease.acquire(lease.EXIT, **KEY) is not None
    assert sum(_race(_try_acquire, 0)) == 0
    assert lease.holder(lease.EXIT, **KEY) is not None


def test_a_lease_left_behind_by_a_dead_process_can_be_taken_over():
    """A crashed worker must not strand a real position with no way to exit it.
    The durable fence against a duplicate SELL is the position's own
    ``exit_order_id``, persisted before the network call; this only closes the
    window in which two LIVE processes are inside the same preparation."""
    assert lease.acquire(lease.EXIT, ttl_s=0.05, **KEY) is not None
    time.sleep(0.1)
    assert lease.holder(lease.EXIT, **KEY) is None
    assert sum(_race(_try_acquire, 0)) == 1


def test_a_lease_is_released_only_by_the_process_that_holds_it():
    owner = lease.acquire(lease.EXIT, **KEY)
    assert not lease.release(lease.EXIT, owner='someone-else', **KEY)
    assert lease.holder(lease.EXIT, **KEY) is not None
    assert lease.release(lease.EXIT, owner=owner, **KEY)
    assert lease.holder(lease.EXIT, **KEY) is None


def test_exiting_and_arming_protection_are_separate_ownerships():
    assert lease.acquire(lease.EXIT, **KEY) is not None
    assert lease.acquire(lease.PROTECTION, **KEY) is not None


def test_dropping_the_claim_frees_it_for_another_process():
    assert monitor._exiting.claim(('u', 'OPT'), account_id='account')
    assert sum(_race(_try_acquire, 0)) == 0
    monitor._exiting.discard(('u', 'OPT'))
    assert sum(_race(_try_acquire, 0)) == 1


def test_a_paper_position_needs_no_database_to_exit():
    """Simulation must not depend on the lease store."""
    assert monitor._exiting.claim(('u', 'PAPER'), account_id='')
    assert not monitor._exiting.claim(('u', 'PAPER'), account_id='')
    assert lease.holder(lease.EXIT, account_id='', uid='u', symbol='PAPER') is None


def test_a_live_exit_is_blocked_when_ownership_cannot_be_established(monkeypatch):
    """Degrading to single-process safety on a live position is how two workers
    both sell. Refusing the exit is the recoverable direction."""
    monkeypatch.setattr(lease, "acquire",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("store down")))
    assert not monitor._exiting.claim(('u', 'OPT'), account_id='account')
    assert any('exit ownership cannot be established' in e.message
               for e in state.activity('u'))


def test_a_stale_projection_cannot_overwrite_a_newer_one():
    """Two processes holding different snapshots of one intent: only the one whose
    version still matches may acknowledge it."""
    intent = journal.reserve(uid='u', account_id='account', strategy_id='sterling-kite',
        generation_id='v1', signal_id='s', exchange='NFO', symbol='OPT', side='BUY',
        quantity=100, payload={})
    assert journal.claim_submission(intent.intent_key)
    journal.transition(intent.intent_key, 'SUBMITTED', order_id='O1')
    journal.observe_order(intent.intent_key, status='OPEN', order_id='O1',
                          filled_quantity=40, average_price=120)
    stale = journal.find(uid='u', account_id='account', order_id='O1').projection_version
    journal.observe_order(intent.intent_key, status='COMPLETE', order_id='O1',
                          filled_quantity=100, average_price=121)
    fresh = journal.find(uid='u', account_id='account', order_id='O1').projection_version
    assert stale != fresh
    results = _race(_try_project, [(intent.intent_key, stale)] * 4
                    + [(intent.intent_key, fresh)] * 4)
    assert sum(results) == 1
    assert not journal.find(uid='u', account_id='account', order_id='O1').projection_pending


# ── an unreadable registry is not an empty one (P0-4) ────────────────────────

def test_a_corrupt_registry_never_reports_an_empty_account():
    """Empty is a positive claim — "you hold nothing". Acting on it leaves every
    live position unguarded and frees the auto-open guards to re-enter."""
    from app.services.kite_engine import service
    db.set_config("kite_engine_positions_u", "{ this is not json")
    positions.reset("u")
    with pytest.raises(positions.RegistryUnreadable):
        positions.open_positions("u")
    assert positions.unreadable_reason("u")
    assert service.autoexec_preflight("u")[0].startswith("Position registry unreadable")


def test_one_corrupt_account_does_not_halt_every_other_one():
    """Scoped, not global: a second user's engine must keep running."""
    from app.services import live_safety
    db.set_config("kite_engine_positions_u", "{ this is not json")
    positions.reset("u")
    with pytest.raises(positions.RegistryUnreadable):
        positions._load("u")
    assert not live_safety.kill_switch_state()["enabled"]
    assert positions.open_positions("other-user") == []


def test_an_unreadable_registry_is_never_overwritten_by_what_is_in_memory():
    """The corrupt blob is the only copy of positions we cannot currently see."""
    db.set_config("kite_engine_positions_u", "{ this is not json")
    positions.reset("u")
    with pytest.raises(positions.RegistryUnreadable):
        positions._load("u")
    positions._persist("u")
    assert db.get_config("kite_engine_positions_u") == "{ this is not json"
    with pytest.raises(positions.RegistryUnreadable):
        positions.persist_strict("u")
