"""Backfill years of DAILY history for every F&O underlying, and their specs.

Why this exists: Snapback's two failing gate checks — the deflated Sharpe and a
confidence interval that includes zero — are both statements about SAMPLE SIZE,
not about the edge. The validation report says so and names this as the remedy.
The store held 19 instruments and 739 sessions, which is about 120 out-of-sample
entry days. Nothing can be proven on that.

Daily bars are the cheapest data there is: Kite serves up to 2000 days of them
per request, so the whole F&O list is a couple of hundred requests rather than
the tens of thousands a 5-minute backfill needs.

Two things are written:

* **daily candles** into the OHLCV store at resolution ``1d``;
* **an instrument spec file** — lot size and strike step per underlying, derived
  from the live instrument dump rather than typed by hand. The engine's
  hardcoded table covers 21 names; a universe of 200 needs the exchange's own
  numbers, and a wrong lot size is a wrong quantity on a real order.

Run:

    .venv/bin/python -m study.snapback_backfill --years 9
    .venv/bin/python -m study.snapback_backfill --years 9 --limit 20   # a probe

Needs a live Kite session (read-only: historical candles and the instrument
dump, no orders). Resumable — each instrument is written as it lands and one
already covered is skipped, so a rate limit costs one instrument rather than the
run.

**Survivorship.** The F&O list is today's. Applying it to 2017 means the names
that were dropped from the segment are absent, which flatters any result whose
level depends on which instruments existed. It does not flatter the
entry-timing permutation, because the null is drawn from the SAME instruments
over the SAME sessions — that test is about the dates, and survivorship is not a
date effect.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

IST = timezone(timedelta(hours=5, minutes=30))

#: Kite serves at most 2000 days of daily candles in one request. 1800 leaves
#: room for the boundary being inclusive at one end.
WINDOW_DAYS = 1800

#: Kite's published limit on the historical endpoint is 3 requests/second. A
#: token bucket cannot hold that — a burst empties it and everything after
#: arrives at the refill rate anyway, which is how an earlier loader in this
#: repo spent its budget on retries. Space the requests instead.
MIN_SPACING_S = 0.40

SPEC_FILE = (pathlib.Path(__file__).resolve().parents[1]
             / "app/engines/instrument_specs.json")


def _field(row: Any, name: str, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def derive_specs(nfo: list, bfo: list) -> dict[str, dict]:
    """Lot size and strike step per underlying, from the exchange's own dump.

    The strike step is the MODAL gap between adjacent listed strikes of one
    expiry, not the mean. A chain often lists a dense ladder near the money and
    a sparse one in the wings, so the mean lands between two real rungs and
    every strike computed from it rounds to something unlisted.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for rows, exch in ((nfo, "NFO"), (bfo, "BFO")):
        for r in rows or ():
            if str(_field(r, "instrument_type", "")) not in ("CE", "PE"):
                continue
            name = str(_field(r, "name", "") or "").strip().upper()
            if not name:
                continue
            strike = float(_field(r, "strike", 0) or 0)
            lot = int(_field(r, "lot_size", 0) or 0)
            expiry = str(_field(r, "expiry", "") or "")
            slot = by_name.setdefault(name, {"exchange": exch, "lots": Counter(),
                                             "strikes": {}})
            if lot > 0:
                slot["lots"][lot] += 1
            if strike > 0 and expiry:
                slot["strikes"].setdefault(expiry, set()).add(round(strike, 2))

    out: dict[str, dict] = {}
    for name, slot in by_name.items():
        lot = slot["lots"].most_common(1)[0][0] if slot["lots"] else 0
        gaps: Counter = Counter()
        for strikes in slot["strikes"].values():
            ordered = sorted(strikes)
            for a, b in zip(ordered, ordered[1:]):
                gap = round(b - a, 2)
                if gap > 0:
                    gaps[gap] += 1
        step = gaps.most_common(1)[0][0] if gaps else 0.0
        if lot > 0 and step > 0:
            out[name] = {"lot_size": lot, "strike_step": step,
                         "exchange": slot["exchange"]}
    return out


def _index_token(name: str) -> Optional[int]:
    p = (pathlib.Path(__file__).resolve().parents[1]
         / "app/services/kite_engine/universe.json")
    try:
        cfg = json.loads(p.read_text())
    except Exception:                                              # noqa: BLE001
        return None
    for idx in cfg.get("indices", []):
        if name in (str(idx.get("option_name")), str(idx.get("name"))):
            tok = int(idx.get("spot_token") or 0)
            if tok:
                return tok
    return None


def resolve_tokens(names: list[str], equities: list) -> tuple[dict[str, int], list[str]]:
    """Underlying -> spot instrument token. Unresolved names are REPORTED.

    A missing instrument is the difference between "no data" and "no data for a
    reason nobody wrote down", and a silently skipped name looks identical to a
    quiet one in the results.
    """
    by_symbol: dict[str, int] = {}
    for r in equities or ():
        seg = str(_field(r, "segment", "") or "")
        if seg and seg not in ("NSE", "BSE"):
            continue
        sym = str(_field(r, "tradingsymbol", "") or "").strip().upper()
        tok = int(_field(r, "instrument_token", 0) or 0)
        if sym and tok and sym not in by_symbol:
            by_symbol[sym] = tok
    out: dict[str, int] = {}
    missing: list[str] = []
    for n in names:
        tok = _index_token(n) or by_symbol.get(n)
        if tok:
            out[n] = tok
        else:
            missing.append(n)
    return out, missing


def stored_span(symbol: str) -> tuple[Optional[int], Optional[int]]:
    from app.services import ohlcv_store
    return (ohlcv_store.get_earliest_time(symbol, "1d"),
            ohlcv_store.get_latest_time(symbol, "1d"))


async def backfill_one(client, symbol: str, token: int, start: date,
                       end: date) -> int:
    """Fetch and store one instrument's daily history. Returns rows written."""
    from app.services import ohlcv_store
    written = 0
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=WINDOW_DAYS), end)
        try:
            data = await client.get_historical(
                token, "day",
                f"{cursor:%Y-%m-%d} 09:15:00", f"{stop:%Y-%m-%d} 15:30:00")
        except Exception as exc:                                   # noqa: BLE001
            raise RuntimeError(f"{symbol} {cursor}..{stop}: {exc}") from exc
        rows = data.get("candles", []) or []
        candles = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(str(r[0])).timestamp()
            except Exception:                                      # noqa: BLE001
                continue
            candles.append({"time": int(ts), "open": float(r[1]), "high": float(r[2]),
                            "low": float(r[3]), "close": float(r[4]),
                            "volume": float(r[5] if len(r) > 5 else 0.0)})
        if candles:
            written += ohlcv_store.upsert_candles(symbol, "1d", candles)
        cursor = stop + timedelta(days=1)
        await asyncio.sleep(MIN_SPACING_S)
    return written


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=9)
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N underlyings — for a quick probe")
    ap.add_argument("--uid", default="default")
    ap.add_argument("--specs-only", action="store_true")
    args = ap.parse_args()

    from app.services import db, ohlcv_store
    db.init()
    ohlcv_store.init_ohlcv_table()
    from app.services.exchanges.kite import accounts
    accounts.bootstrap()
    acct = accounts.get_active(args.uid)
    if not acct:
        raise SystemExit("no active Kite account")
    client = await accounts.acquire_client(acct)

    nfo, bfo, nse, bse = await asyncio.gather(
        client.search_instruments("", "NFO", limit=1_000_000),
        client.search_instruments("", "BFO", limit=1_000_000),
        client.search_instruments("", "NSE", limit=1_000_000),
        client.search_instruments("", "BSE", limit=1_000_000),
    )
    specs = derive_specs(nfo, bfo)
    SPEC_FILE.write_text(json.dumps(specs, indent=1, sort_keys=True))
    print(f"instrument specs: {len(specs)} underlyings -> {SPEC_FILE}")
    if args.specs_only:
        return

    names = sorted(specs)
    tokens, missing = resolve_tokens(names, list(nse) + list(bse))
    if missing:
        print(f"unresolved ({len(missing)}): {', '.join(missing[:12])}"
              f"{' …' if len(missing) > 12 else ''}")
    if args.limit:
        tokens = dict(list(tokens.items())[:args.limit])

    today = datetime.now(IST).date()
    start = today - timedelta(days=int(args.years * 365.25))
    print(f"backfilling {len(tokens)} underlyings, {start} .. {today}")

    done = failed = skipped = 0
    t0 = time.monotonic()
    for i, (symbol, token) in enumerate(sorted(tokens.items()), 1):
        first, last = stored_span(symbol)
        if first is not None and last is not None:
            have_from = datetime.fromtimestamp(first, tz=IST).date()
            have_to = datetime.fromtimestamp(last, tz=IST).date()
            if have_from <= start + timedelta(days=7) and have_to >= today - timedelta(days=5):
                skipped += 1
                continue
        try:
            n = await backfill_one(client, symbol, token, start, today)
            done += 1
            if i % 20 == 0 or n == 0:
                print(f"  [{i}/{len(tokens)}] {symbol}: {n} bars "
                      f"({time.monotonic() - t0:.0f}s)")
        except Exception as exc:                                   # noqa: BLE001
            failed += 1
            print(f"  [{i}/{len(tokens)}] {symbol}: FAILED {exc}")
    print(f"done: {done} written, {skipped} already covered, {failed} failed, "
          f"{time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
