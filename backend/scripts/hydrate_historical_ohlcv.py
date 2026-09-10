"""
Hydrate real historical 5-minute candles from Zerodha Kite API into ohlcv_store
for all core indices and F&O universe across August 1 to September 7, 2026.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.client import KiteClient
from app.services import ohlcv_store
from app.services.ohlcv_store import INDEX_ALIASES
from app.services.simulation import KITE_TOKENS

async def main():
    accounts.bootstrap()
    acct = accounts.get_active("default") or next(
        (a for a in accounts._accounts.values() if a.is_active and a.access_token),
        None,
    )
    if not acct or not acct.access_token:
        print("No active Kite account found. Aborting hydration.")
        return

    kc = KiteClient(api_key=acct.api_key or "", access_token=acct.access_token)
    from_str = "2026-08-01 09:15:00"
    to_str = "2026-09-07 15:30:00"

    symbols = [
        "NIFTY 50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
        "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS",
        "INFY", "BHARTIARTL", "LT", "BAJFINANCE", "BAJAJFINSV",
        "AXISBANK", "KOTAKBANK", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    ]

    print(f"Starting historical hydration for {len(symbols)} symbols from {from_str} to {to_str}...")

    total_written = 0
    for sym in symbols:
        tok = KITE_TOKENS.get(sym.upper()) or KITE_TOKENS.get(INDEX_ALIASES.get(sym.upper(), ""))
        if not tok:
            print(f"  [SKIP] No Kite token found for {sym}")
            continue

        try:
            hist_data = await kc.get_historical(tok, "5minute", from_str, to_str)
            raw_list = hist_data.get("candles", []) if isinstance(hist_data, dict) else []
            try:
                from zoneinfo import ZoneInfo
                ist = ZoneInfo("Asia/Kolkata")
            except ImportError:
                ist = timezone(timedelta(hours=5, minutes=30))
            parsed_candles = []
            for row in raw_list:
                dt_c = datetime.fromisoformat(row[0])
                if dt_c.tzinfo is None:
                    dt_c = dt_c.replace(tzinfo=ist)
                parsed_candles.append({
                    "time": int(dt_c.timestamp()),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 else 0.0,
                })

            if parsed_candles:
                written = ohlcv_store.upsert_candles(sym, "5m", parsed_candles)
                alias = INDEX_ALIASES.get(sym.upper())
                if alias and alias != sym.upper():
                    ohlcv_store.upsert_candles(alias, "5m", parsed_candles)
                total_written += written
                print(f"  [OK] {sym}: {written} candles saved (latest: {raw_list[-1][0]})")
            else:
                print(f"  [WARN] {sym}: 0 candles returned from Kite")
            await asyncio.sleep(0.35)
        except Exception as exc:
            print(f"  [ERROR] {sym}: {exc}")

    print(f"Hydration complete! Total candles upserted: {total_written}")

if __name__ == "__main__":
    asyncio.run(main())

