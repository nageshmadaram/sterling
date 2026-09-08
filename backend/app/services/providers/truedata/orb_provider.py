"""TrueData-native provider for ORB."""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.engines.nifty_orb_options import Bar, OptionContract, StrategyConfig, is_monthly_expiry
from app.services.market_data.truedata import TrueDataHistoricalClient
from app.services.nifty_orb_option_chain import filter_chain, normalize_chain

IST = ZoneInfo("Asia/Kolkata")


class TrueDataOrbProvider:
    def __init__(self, client: TrueDataHistoricalClient) -> None:
        self.client = client

    @staticmethod
    def _parse_provider_time(raw: Any) -> datetime:
        text = str(raw or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text) if "T" in text or "+" in text[10:] else datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return (dt.replace(tzinfo=IST) if dt.tzinfo is None else dt).astimezone(timezone.utc)

    async def bars(self, symbol: str, cfg: StrategyConfig, *, limit: int | None = None) -> list[Bar]:
        n = int(limit) if limit else 200
        rows = await self.client.get_last_bars(symbol, n, interval=f"{cfg.interval_minutes}min", bidask=0)
        return [Bar(self._parse_provider_time(r.get("timestamp") or r.get("time")), float(r.get("open", 0)), float(r.get("high", 0)), float(r.get("low", 0)), float(r.get("close", 0)), float(r.get("volume", 0))) for r in rows]

    async def latest_tick(self, symbol: str, *, bidask: bool = True) -> dict[str, Any] | None:
        rows = await self.client.get_last_ticks(symbol, 1, bidask=1 if bidask else 0)
        return rows[-1] if rows else None

    @staticmethod
    def _tick_time(tick: dict[str, Any]) -> datetime | None:
        raw = tick.get("timestamp") or tick.get("time")
        if not raw:
            return None
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw) / 1000, tz=timezone.utc)
            text = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text) if "T" in text or "+" in text[10:] else datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return (dt.replace(tzinfo=IST) if dt.tzinfo is None else dt).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _extract_expiries(payload: Any) -> list[date]:
        values: set[date] = set()
        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if str(key).lower() in {"expiry", "expiry_date", "expirydate"}:
                        visit(value)
                    elif isinstance(value, (dict, list)):
                        visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
            elif isinstance(node, str):
                try:
                    values.add(date.fromisoformat(node[:10]))
                except ValueError:
                    pass
        visit(payload)
        return sorted(values)

    async def resolve_expiry(self, symbol: str, cfg: StrategyConfig, *, today: date | None = None) -> str:
        """Resolve the configured expiry preference against TrueData's calendar.

        The preference vocabulary and the weekly/monthly rule are the engine's
        (:func:`is_monthly_expiry`), so a provider-resolved expiry and an
        engine-selected contract can never disagree about what "weekly" means.
        """
        cfg.validate()
        payload = await self.client.get_all_symbols("NFO", search=symbol, allexpiry=True)
        today = today or datetime.now(IST).date()
        eligible = [
            d for d in self._extract_expiries(payload)
            if d >= today
            and cfg.expiry_dte_min <= (d - today).days <= cfg.expiry_dte_max
            and not (cfg.avoid_expiry_day and d == today)
        ]
        if not eligible:
            raise ValueError(f"No TrueData expiry for {symbol} satisfies configured DTE range")
        selection = cfg.expiry_selection.strip().lower()
        if selection in {"nearest", "any"}:
            return min(eligible).isoformat()
        want_monthly = selection == "monthly"
        matching = [d for d in eligible if is_monthly_expiry(d) is want_monthly]
        if not matching:
            raise ValueError(f"No eligible {selection} TrueData expiry for {symbol}")
        return min(matching).isoformat()

    async def option_chain(self, symbol: str, expiry: str, cfg: StrategyConfig, *, today: date | None = None) -> list[OptionContract]:
        today = today or datetime.now(IST).date()
        resolved = await self.resolve_expiry(symbol, cfg, today=today) if expiry.strip().lower() in {"nearest", "weekly", "monthly", "any", ""} else expiry
        payload = await self.client.get_option_chain(symbol, resolved)
        return filter_chain(normalize_chain(payload), cfg, today=today)

    async def refresh_contract(self, contract: OptionContract, cfg: StrategyConfig) -> tuple[OptionContract, float | None]:
        if not cfg.truedata_use_ticks:
            return contract, None
        tick = await self.latest_tick(contract.symbol, bidask=cfg.truedata_use_bid_ask)
        if not tick:
            raise ValueError("TrueData returned no latest tick for selected option")
        stamp = self._tick_time(tick)
        age = None if stamp is None else max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
        if cfg.truedata_use_quote_freshness and (age is None or age > cfg.max_quote_staleness_s):
            raise ValueError(f"TrueData quote is stale: {age if age is not None else 'unknown'}s")
        ltp = float(tick.get("ltp") or contract.ltp)
        bid = float(tick.get("bid") or contract.bid)
        ask = float(tick.get("ask") or contract.ask)
        volume = float(tick.get("volume") or contract.volume)
        oi = float(tick.get("oi") or contract.open_interest)
        if not cfg.truedata_use_bid_ask:
            bid = bid if bid > 0 else ltp
            ask = ask if ask >= bid and ask > 0 else ltp
        if cfg.truedata_use_bid_ask and (bid <= 0 or ask < bid):
            raise ValueError("invalid TrueData bid/ask")
        refreshed = OptionContract(contract.symbol, contract.strike, contract.expiry, contract.option_type, ltp, bid, ask, contract.lot_size, contract.delta, volume, oi, stamp.astimezone(IST) if stamp else None)
        if cfg.truedata_use_bid_ask and refreshed.spread_pct > cfg.max_spread_pct:
            raise ValueError("TrueData spread above configured maximum")
        if cfg.truedata_use_oi and oi < cfg.min_open_interest:
            raise ValueError("TrueData OI below configured minimum")
        if volume < cfg.min_option_volume:
            raise ValueError("TrueData option volume below configured minimum")
        return refreshed, age

    async def symbols(self, segment: str = "NFO", search: str | None = None) -> Any:
        return await self.client.get_all_symbols(segment, search=search, allexpiry=False)
