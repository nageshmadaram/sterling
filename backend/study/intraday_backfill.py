"""Backfill years of 5-minute history into the OHLCV store.

Why this exists: every honest measurement of the intraday pack is limited by
data, not by ideas. The store holds about 173 sessions. That is six walk-forward
folds, and a 3-to-20 basis-point effect against a 2.3 bp cost cannot be
separated from luck on six folds — which is precisely what the deflated Sharpe
keeps saying.

Run:

    .venv/bin/python -m study.intraday_backfill --years 3
    .venv/bin/python -m study.intraday_backfill --years 3 --symbols NIFTY BANKNIFTY

Needs a live Kite session (read-only: historical candles, no orders). Resumable
— it writes each window as it lands and skips what the store already has, so a
rate limit or a dropped connection costs one window rather than the run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

_IST = timezone(timedelta(hours=5, minutes=30))

#: Kite caps a single 5-minute historical request. The documented limit is 100
#: days; 90 leaves room for the boundary being inclusive at one end.
WINDOW_DAYS = 90

#: Kite's published rate limit for the historical endpoint is 3 requests/second.
#: A token bucket cannot hold that reliably — a burst empties it and every
#: subsequent request arrives at the refill rate anyway, which is how an earlier
#: loader in this repo spent its budget on retries. Space the requests instead.
MIN_SPACING_S = 0.40

#: The instruments the intraday pack can name a contract for, which is the only
#: set worth having history for.
def default_symbols() -> list[str]:
    from app.engines.intraday.contracts import SPECS
    return sorted(SPECS)


def _index_token(name: str) -> Optional[int]:
    """Spot token for an index, from the engine's own universe file."""
    import json
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / "app/services/kite_engine/universe.json"
    try:
        cfg = json.loads(p.read_text())
    except Exception:
        return None
    for idx in cfg.get("indices", []):
        if str(idx.get("option_name")) == name or str(idx.get("name")) == name:
            tok = int(idx.get("spot_token") or 0)
            if tok:
                return tok
    return None


async def _resolve_tokens(client, symbols: Sequence[str]) -> dict[str, int]:
    """Symbol -> instrument token, indices from the universe file and equities
    from the NSE dump. A symbol that cannot be resolved is REPORTED, not
    silently skipped: a missing instrument is the difference between "no data"
    and "no data for a reason nobody wrote down".
    """
    from app.engines.intraday.contracts import SPECS, canonical
    out: dict[str, int] = {}
    unresolved: list[str] = []
    equities: list[dict] = []
    for raw in symbols:
        name = canonical(raw)
        spec = SPECS.get(name)
        if spec is None:
            unresolved.append(f"{raw} (not an instrument this engine knows)")
            continue
        if spec.is_index:
            tok = _index_token(name)
            if tok:
                out[name] = tok
            else:
                unresolved.append(f"{name} (no spot token in universe.json)")
            continue
        if not equities:
            equities = await client.search_instruments("", "NSE", limit=1_000_000)
        match = next((r for r in equities
                      if str(r.get("tradingsymbol")) == name
                      and str(r.get("segment", "")).startswith("NSE")), None)
        if match and int(match.get("instrument_token") or 0):
            out[name] = int(match["instrument_token"])
        else:
            unresolved.append(f"{name} (not listed on NSE)")
    for u in unresolved:
        print(f"  unresolved: {u}")
    return out


def _have(symbol: str, resolution: str) -> tuple[Optional[int], Optional[int]]:
    from app.services.ohlcv_store import get_earliest_time, get_latest_time
    try:
        lo, hi = get_earliest_time(symbol, resolution), get_latest_time(symbol, resolution)
    except Exception:
        return None, None
    # A timestamp this far out is a unit error upstream, not a date. Reporting
    # it as one beats formatting it and crashing on "year must be in 1..9999".
    for v in (lo, hi):
        if v and v > 100_000_000_000:
            print(f"  {symbol}: stored timestamps are in MILLISECONDS, not seconds")
            return None, None
    return lo, hi


async def backfill_symbol(client, symbol: str, token: int, *, years: float,
                          resolution: str = "5m", interval: str = "5minute",
                          force: bool = False) -> int:
    """Walk backwards in windows, writing each one as it lands. Returns rows written."""
    from app.services.exchanges.kite.client import _parse_kite_ts
    from app.services.ohlcv_store import upsert_candles

    earliest, _ = _have(symbol, resolution)
    target = datetime.now(_IST) - timedelta(days=365 * years)
    cursor = (datetime.fromtimestamp(earliest, tz=_IST) if (earliest and not force)
              else datetime.now(_IST))
    written = 0
    while cursor > target:
        start = max(target, cursor - timedelta(days=WINDOW_DAYS))
        frm = start.strftime("%Y-%m-%d %H:%M:%S")
        to = cursor.strftime("%Y-%m-%d %H:%M:%S")
        rows: list[dict] = []
        for attempt in range(4):
            try:
                data = await client.get_historical(token, interval, frm, to)
                for c in data.get("candles", []) or []:
                    try:
                        # `_parse_kite_ts` returns MILLISECONDS; the OHLCV store
                        # keeps seconds. Writing the wrong unit does not fail —
                        # it lands rows dated in the year 57969, which every
                        # reader silently treats as the future and skips.
                        rows.append({"time": _parse_kite_ts(str(c[0])) // 1000,
                                     "open": float(c[1]), "high": float(c[2]),
                                     "low": float(c[3]), "close": float(c[4]),
                                     "volume": float(c[5]) if len(c) > 5 else 0.0})
                    except (IndexError, TypeError, ValueError):
                        continue
                break
            except Exception as exc:                               # noqa: BLE001
                # A 429 is worth waiting out; a 400 means this window will fail
                # identically every time, and burning four retries on it is how
                # a backfill spends its whole budget going nowhere.
                msg = str(exc)
                if "400" in msg or "403" in msg:
                    print(f"    {symbol} {frm[:10]}..{to[:10]} refused: {msg[:90]}")
                    break
                if attempt == 3:
                    print(f"    {symbol} {frm[:10]}..{to[:10]} failed: {msg[:90]}")
                await asyncio.sleep(0.8 * (2 ** attempt))
        if rows:
            written += upsert_candles(symbol, resolution, rows)
        cursor = start
        await asyncio.sleep(MIN_SPACING_S)
    return written


async def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--resolution", default="5m")
    ap.add_argument("--interval", default="5minute")
    ap.add_argument("--uid", default=None, help="Kite user id; the first active account otherwise")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch from today rather than extending backwards "
                         "from what the store already has")
    args = ap.parse_args(argv)

    from app.services import db, ohlcv_store
    db.init()
    ohlcv_store.init_ohlcv_table()
    from app.services.exchanges.kite import accounts
    accounts.bootstrap()
    acct = (accounts.get_active(args.uid) if args.uid else
            next((a for a in accounts.all_accounts() if a.is_active), None))
    if acct is None:
        print("No active Kite account. This needs a live session — it reads "
              "historical candles only, and places no orders.")
        return 2
    client = await accounts.acquire_client(acct)

    symbols = args.symbols or default_symbols()
    print(f"Resolving {len(symbols)} instruments…")
    tokens = await _resolve_tokens(client, symbols)
    if not tokens:
        print("Nothing resolved. Nothing to fetch.")
        return 2
    print(f"  {len(tokens)} resolved\n")

    total = 0
    for i, (sym, tok) in enumerate(sorted(tokens.items()), 1):
        before_e, before_l = _have(sym, args.resolution)
        n = await backfill_symbol(client, sym, tok, years=args.years,
                                  resolution=args.resolution,
                                  interval=args.interval, force=args.force)
        after_e, after_l = _have(sym, args.resolution)
        span = ""
        if after_e and after_l:
            span = (f"{datetime.fromtimestamp(after_e, tz=_IST):%Y-%m-%d}"
                    f" -> {datetime.fromtimestamp(after_l, tz=_IST):%Y-%m-%d}")
        print(f"[{i:2d}/{len(tokens)}] {sym:12s} +{n:6d} rows   {span}")
        total += n
    print(f"\n{total:,} rows written. Re-run the harness — more folds is the "
          "one thing that moves the deflated Sharpe.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
