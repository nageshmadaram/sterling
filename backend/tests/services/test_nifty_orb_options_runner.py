import asyncio

import pytest

from app.services import nifty_orb_options_runner as runner


@pytest.mark.asyncio
async def test_runner_start_is_singleton(monkeypatch):
    """start() schedules on the running loop, exactly as the startup hook does."""
    async def idle():
        await asyncio.sleep(60)

    monkeypatch.setattr(runner, "run_forever", idle)
    runner.stop()
    first = runner.start()
    second = runner.start()
    assert first is second
    runner.stop()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert first.cancelled() or first.done()


def test_market_open_fails_closed_when_calendar_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "app.services.navigator.calendar":
            raise ImportError("calendar unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert runner._is_verified_market_open() is False


def test_runner_does_not_execute_when_market_closed(monkeypatch):
    called = False

    async def fake_users():
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "_is_verified_market_open", lambda: False)
    asyncio.run(runner._tick())
    assert called is False


@pytest.mark.asyncio
async def test_runner_recovers_once_per_user_then_scans(monkeypatch):
    """Tick order: recover → expiry square-off → scan → execute_scan."""
    calls = []

    async def recover(uid):
        calls.append(("recover", uid))
        return {"trade_state": {"status": "recovered"}, "ticks": {}}

    async def square(client, uid):
        calls.append(("square", uid))
        return {"status": "ok", "squared": []}

    async def scan(uid, cfg):
        calls.append(("scan", uid))
        return {"signals": []}

    async def execute(uid, *, scan, max_trades):
        calls.append(("execute", uid, max_trades))
        return {"status": "no_trade", "executed": []}

    class Cfg:
        enabled = True
        max_trades_per_day = 2

    class Acct:
        pass

    monkeypatch.setattr(runner, "_is_verified_market_open", lambda: True)
    monkeypatch.setattr("app.services.nifty_orb_lifecycle.recover_after_restart", recover)
    monkeypatch.setattr("app.services.nifty_orb_lifecycle.square_off_expired", square)
    monkeypatch.setattr("app.services.nifty_orb_scanner.scan_user", scan)
    monkeypatch.setattr("app.services.nifty_orb_execution.execute_scan", execute)
    monkeypatch.setattr("app.services.nifty_orb_options.get_config", lambda: Cfg())
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: Acct())

    async def acquire(acct):
        return object()

    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
    runner._recovered.clear()
    first = await runner._run_user("u1")
    second = await runner._run_user("u1")
    assert first["status"] == "no_trade"
    assert [c[0] for c in calls] == ["recover", "square", "scan", "execute", "square", "scan", "execute"]
    assert "u1" in runner._recovered


@pytest.mark.asyncio
async def test_failed_recovery_is_retried_next_tick(monkeypatch):
    calls = []

    async def recover(uid):
        calls.append("recover")
        raise RuntimeError("broker down")

    async def square(client, uid):
        calls.append("square")
        return {"status": "ok", "squared": []}

    async def scan(uid, cfg):
        calls.append("scan")
        return {"signals": []}

    async def execute(uid, *, scan, max_trades):
        calls.append("execute")
        return {"status": "no_trade", "executed": []}

    class Cfg:
        enabled = True
        max_trades_per_day = 2

    class Acct:
        pass

    monkeypatch.setattr(runner, "_is_verified_market_open", lambda: True)
    monkeypatch.setattr("app.services.nifty_orb_lifecycle.recover_after_restart", recover)
    monkeypatch.setattr("app.services.nifty_orb_lifecycle.square_off_expired", square)
    monkeypatch.setattr("app.services.nifty_orb_scanner.scan_user", scan)
    monkeypatch.setattr("app.services.nifty_orb_execution.execute_scan", execute)
    monkeypatch.setattr("app.services.nifty_orb_options.get_config", lambda: Cfg())
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: Acct())

    async def acquire(acct):
        return object()

    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
    runner._recovered.clear()
    first = await runner._run_user("u1")
    second = await runner._run_user("u1")
    assert first["status"] == "no_trade"
    assert second["status"] == "no_trade"
    assert calls.count("recover") == 2
    assert "u1" not in runner._recovered
