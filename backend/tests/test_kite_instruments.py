"""Kite instruments cache: parse, search, token resolution, TTL."""
import pytest

from app.services.exchanges.kite.instruments import InstrumentCache, parse_instruments_csv

_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange\n"
    "408065,1594,INFY,INFOSYS,1500.5,,0,0.05,1,EQ,NSE,NSE\n"
    "5633,22,ACC,ACC,2400,,0,0.05,1,EQ,NSE,NSE\n"
    "12345,48,NIFTY25JAN25000CE,NIFTY,120.5,2025-01-30,25000,0.05,50,CE,NFO-OPT,NFO\n"
)


def _cache(ttl=3600.0):
    calls = {"n": 0}

    async def fetch(exchange):
        calls["n"] += 1
        return _CSV

    c = InstrumentCache(fetch, ttl=ttl)
    return c, calls


def test_parse_coerces_numeric_columns():
    rows = parse_instruments_csv(_CSV)
    assert rows[0]["instrument_token"] == 408065
    assert rows[0]["strike"] == 0.0
    assert rows[2]["strike"] == 25000.0
    assert rows[2]["lot_size"] == 50


async def test_search_matches_symbol_and_name():
    c, _ = _cache()
    res = await c.search("INFY", exchange="NSE")
    assert any(r["tradingsymbol"] == "INFY" for r in res)
    by_name = await c.search("infosys", exchange="NSE")
    assert any(r["tradingsymbol"] == "INFY" for r in by_name)


async def test_universal_search_option_strike_multitoken():
    # "NIFTY 25000 CE" must find the option by name + strike + type across the full dump
    c, _ = _cache()
    res = await c.search("NIFTY 25000 CE", exchange="")
    assert any(r["tradingsymbol"] == "NIFTY25JAN25000CE" for r in res)
    # token-AND: a strike that isn't present yields nothing
    assert await c.search("NIFTY 26000 CE", exchange="") == []


async def test_universal_search_ranks_equity_before_options():
    csv = (
        "instrument_token,tradingsymbol,name,strike,instrument_type,segment,exchange\n"
        "408065,INFY,INFOSYS,0,EQ,NSE,NSE\n"
        "111,INFY25JAN1600CE,INFY,1600,CE,NFO-OPT,NFO\n"
    )

    async def fetch(ex):
        return csv
    c = InstrumentCache(fetch)
    res = await c.search("INFY", exchange="")
    # both match; the exact-symbol equity (shorter) ranks above the option strike
    assert res[0]["tradingsymbol"] == "INFY"


async def test_universal_search_orders_options_chronologically():
    # The option flood must come back nearest-expiry-first (CE before PE), NOT
    # alphabetical-by-symbol (which scrambled JUN/JUL/AUG into 26AUG,26DEC,26JUL,…).
    csv = (
        "instrument_token,tradingsymbol,name,expiry,strike,instrument_type,segment,exchange\n"
        "1,NIFTY26AUG24000CE,NIFTY,2026-08-27,24000,CE,NFO-OPT,NFO\n"
        "2,NIFTY26JUN24000CE,NIFTY,2026-06-26,24000,CE,NFO-OPT,NFO\n"
        "3,NIFTY26JUL24000CE,NIFTY,2026-07-30,24000,CE,NFO-OPT,NFO\n"
        "4,NIFTY26JUN24000PE,NIFTY,2026-06-26,24000,PE,NFO-OPT,NFO\n"
    )

    async def fetch(ex):
        return csv
    c = InstrumentCache(fetch)
    res = await c.search("NIFTY 24000", exchange="")
    syms = [r["tradingsymbol"] for r in res]
    # CE side ordered by expiry (Jun → Jul → Aug); PE comes after all CE.
    assert syms == [
        "NIFTY26JUN24000CE",
        "NIFTY26JUL24000CE",
        "NIFTY26AUG24000CE",
        "NIFTY26JUN24000PE",
    ]


async def test_resolve_token_exact():
    c, _ = _cache()
    assert await c.resolve_token("INFY", "NSE") == 408065
    with pytest.raises(KeyError):
        await c.resolve_token("NOPE", "NSE")


async def test_cache_hits_avoid_refetch_within_ttl():
    c, calls = _cache(ttl=3600.0)
    await c.load("NSE")
    await c.load("NSE")
    assert calls["n"] == 1  # second call served from cache


async def test_get_product_id_returns_token(monkeypatch):
    from app.services.exchanges.kite.client import KiteClient
    c = KiteClient(is_paper=True)

    async def fake_fetch(exchange):
        return _CSV
    c._instruments._fetch = fake_fetch
    assert await c.get_product_id("NSE:INFY") == 408065
    assert await c.get_product_id("NSE:NOPE") == 0  # graceful, never raises


async def test_universal_search_sensex_ranking_parity():
    # Parity with Kite: SENSEX index -> SENSEX futures -> SENSEX50 futures -> ATM options (CE/PE paired) -> ETFs
    csv = (
        "instrument_token,tradingsymbol,name,expiry,strike,instrument_type,segment,exchange\n"
        "100,SENSEXAXIS,AXIS BSE SENSEX ETF,,0,EQ,NSE,NSE\n"
        "101,SENSEX,SENSEX,,0,EQ,INDICES,BSE\n"
        "102,SENSEX26OCTFUT,SENSEX,2026-10-29,0,FUT,BFO-FUT,BFO\n"
        "103,SENSEX26SEPFUT,SENSEX,2026-09-24,0,FUT,BFO-FUT,BFO\n"
        "104,SENSEX5026SEPFUT,SENSEX50,2026-09-24,0,FUT,BFO-FUT,BFO\n"
        "105,SENSEX2691074800PE,SENSEX,2026-09-10,74800,PE,BFO-OPT,BFO\n"
        "106,SENSEX2691074800CE,SENSEX,2026-09-10,74800,CE,BFO-OPT,BFO\n"
        "107,SENSEX2691070000CE,SENSEX,2026-09-10,70000,CE,BFO-OPT,BFO\n"
    )
    async def fetch(ex):
        return csv
    c = InstrumentCache(fetch)
    res = await c.search("SENSEX", exchange="")
    syms = [r["tradingsymbol"] for r in res]
    assert syms[0] == "SENSEX"              # 1. Exact index
    assert syms[1] == "SENSEX26SEPFUT"      # 2. Near month fut
    assert syms[2] == "SENSEX26OCTFUT"      # 3. Next month fut
    assert syms[3] == "SENSEX5026SEPFUT"    # 4. SENSEX50 fut
    # 5 & 6. Options paired near spot (74800 CE before PE)
    assert syms[4] == "SENSEX2691074800CE"
    assert syms[5] == "SENSEX2691074800PE"
    assert "SENSEXAXIS" in syms[6:]         # ETF ranked after core derivatives

