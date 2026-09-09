"""Stage B: the wall is the chain max, and a wide quote never becomes a candidate."""
from __future__ import annotations

from datetime import date

import pytest

from app.engines.gamma_move import GammaMoveConfig, SpotLevel
from app.services import gamma_move_scanner as scanner

TODAY = date(2026, 9, 20)
LEVEL = SpotLevel(price=1300.0, kind="resistance", touches=3)


def _nfo(symbol: str, strike: float, token: int) -> dict:
    return {
        "instrument_token": token,
        "tradingsymbol": symbol,
        "name": "RELIANCE",
        "expiry": "2026-09-29",
        "strike": strike,
        "instrument_type": "CE",
        "segment": "NFO-OPT",
        "lot_size": 500,
        "tick_size": 0.05,
    }


def _opt_quote(oi: int, premium: float = 53.0, bid: float = 52.9, ask: float = 53.1) -> dict:
    return {
        "oi": oi,
        "last_price": premium,
        "volume": 5_000,
        "depth": {"buy": [{"price": bid}], "sell": [{"price": ask}]},
    }


def _quotes(nfo: dict) -> dict:
    out = {"NSE:RELIANCE": {"last_price": 1298.0}}
    out.update(nfo)
    return out


@pytest.fixture
def cfg() -> GammaMoveConfig:
    return GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000)


async def _scan(monkeypatch, cfg: GammaMoveConfig, nfo_quotes: dict) -> list:
    rows = [
        _nfo("RELIANCE26SEP1300CE", 1300.0, 11),
        _nfo("RELIANCE26SEP1500CE", 1500.0, 22),
    ]
    quotes = _quotes(nfo_quotes)

    async def dump(_uid):
        return rows

    async def batched(_client, _pacer, keys):
        return {k: quotes.get(k) or quotes.get(k.split(":", 1)[-1]) or {} for k in keys}

    monkeypatch.setattr(scanner, "nfo_dump", dump)
    monkeypatch.setattr(scanner, "_quote_batched", batched)
    return await scanner.scan_strikes(
        "u1", cfg, client=object(),
        levels={"RELIANCE": [LEVEL]},
        spots={"RELIANCE": 1298.0},
        regimes={"RELIANCE": "up"},
        today=TODAY,
    )


@pytest.mark.asyncio
async def test_runner_up_oi_is_refused_when_the_chain_max_sits_elsewhere(monkeypatch, cfg):
    """The far 1500 is the wall. 1300 is near the level but is a runner-up.

    If the scanner only quoted the top-of-window sample, 1300 would look like
    the wall and leak through. chain_oi_max has to come from the full chain.
    """
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(200_000),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(5_000_000, premium=12.0),
    })
    assert found == []


@pytest.mark.asyncio
async def test_the_near_chain_max_is_kept(monkeypatch, cfg):
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert len(found) == 1
    assert found[0].instrument.tradingsymbol == "RELIANCE26SEP1300CE"
    assert found[0].chain_oi_max == 5_000_000


@pytest.mark.asyncio
async def test_spread_of_3_1_percent_is_refused(monkeypatch, cfg):
    # (103.15 - 100) / 101.575 * 100 = 3.101%
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000, bid=100.0, ask=103.15),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert found == []


@pytest.mark.asyncio
async def test_spread_inside_3_percent_is_kept(monkeypatch, cfg):
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000, bid=100.0, ask=102.9),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert len(found) == 1
    assert found[0].instrument.tradingsymbol == "RELIANCE26SEP1300CE"
