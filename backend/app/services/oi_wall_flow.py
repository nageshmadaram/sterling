"""Runtime plumbing for the OI Wall Flow strategy.

Config persistence, instrument resolution, and the operator snapshot. All
strategy mathematics lives in ``app.engines.oi_wall_flow`` and is reachable
from here without any broker object, so replay exercises the same code the
live path runs.

Instrument resolution goes through the cached NFO dump rather than building
option symbols by string formatting. A fabricated key is an order that either
rejects or hits a contract nobody chose.

BFO / SENSEX is skipped in v1: the dump, quote keys and GTT path are NFO.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.oi_wall_flow import (CONTRACT_VERSION, JUDGEMENT, JUDGEMENT_FIELDS,
                                      InstrumentRef, OIWallFlowConfig, STRATEGY_ID,
                                      STRATEGY_NAME)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "oi_wall_flow_config"

#: The NFO dump is ~32k rows and changes once a day. Refetching it per scan would
#: be the classic hot-path mistake this codebase has already had to fix once.
_DUMP_TTL_S = 900.0
_dump_cache: dict[str, tuple[float, list[dict]]] = {}

#: Display names the settings page writes (INDEX_OPTIONS values) onto the NFO
#: option ``name`` the dump actually carries. ``"NSE:BANKNIFTY"`` does not
#: resolve; the quote key is ``"NSE:NIFTY BANK"``.
INDEX_DISPLAY_TO_OPTION: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "FINNIFTY": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
    "NIFTY NXT 50": "NIFTYNXT50",
    "NIFTYNXT50": "NIFTYNXT50",
}

#: Indices this engine will not scan. SENSEX / BANKEX live on BFO; v1 is NFO.
SKIP_INDICES: frozenset[str] = frozenset({"SENSEX", "BANKEX", "SENSEX 50"})

INDEX_SPOT_KEY: dict[str, str] = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "NIFTYNXT50": "NSE:NIFTY NXT 50",
}

_TUPLE_KEYS = (
    "scan_stocks", "scan_indices",
    "scan_expiries_indices", "scan_expiries_stocks",
    "scan_weekly_series_indices", "scan_monthly_series_indices",
    "scan_monthly_series_stocks",
)


def ist_today() -> date:
    return datetime.now(_IST).date()


def ist_now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def option_name_of(display: str) -> Optional[str]:
    """NFO dump ``name`` for a settings-page index, or None if skipped / unknown."""
    raw = str(display or "").strip().upper()
    if not raw or raw in SKIP_INDICES:
        return None
    mapped = INDEX_DISPLAY_TO_OPTION.get(raw, raw)
    if mapped in SKIP_INDICES:
        return None
    return mapped


def spot_quote_key(option_name: str) -> str:
    """The NSE quote key for an option chain's underlying."""
    name = str(option_name or "").upper()
    return INDEX_SPOT_KEY.get(name, f"NSE:{name}")


# ------------------------------------------------------------------ config

def get_config(uid: str | None = None) -> OIWallFlowConfig:
    """The configuration currently in force.

    Two different fallbacks, kept apart on purpose:

    * **Nothing stored** -> the real defaults, whatever they are.
    * **Stored but unreadable or invalid** -> defaults with the engine OFF.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    try:
        from app.services import db
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception:                                              # noqa: BLE001
        log.warning("%s: config store unavailable; running with defaults OFF", STRATEGY_ID)
        return OIWallFlowConfig(enabled=False)
    if not raw:
        return OIWallFlowConfig()
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = OIWallFlowConfig.field_names()
        merged = {**OIWallFlowConfig().as_dict(),
                  **{k: v for k, v in dict(stored).items() if k in known}}
        for key in _TUPLE_KEYS:
            if isinstance(merged.get(key), list):
                merged[key] = tuple(merged[key])
        return OIWallFlowConfig(**merged).validate()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); running with defaults OFF",
                  STRATEGY_ID, exc)
        return OIWallFlowConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> OIWallFlowConfig:
    """Persist a config change. Validation is the engine's, not a second copy."""
    current = get_config(uid).as_dict()
    unknown = sorted(set(values) - set(current))
    if unknown:
        raise ValueError(f"Unknown {STRATEGY_ID} config fields: {', '.join(unknown)}")
    current.update(values)
    for key in _TUPLE_KEYS:
        if isinstance(current.get(key), list):
            current[key] = tuple(current[key])
    cfg = OIWallFlowConfig(**current).validate()
    from app.services import db
    target_key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    db.set_config(target_key, json.dumps(cfg.as_dict(), separators=(",", ":")))
    return cfg


# -------------------------------------------------- instrument resolution

async def nfo_dump(uid: str) -> list[dict]:
    from app.services.exchanges.kite import accounts
    cached = _dump_cache.get(uid)
    now = datetime.now(timezone.utc).timestamp()
    if cached and now - cached[0] < _DUMP_TTL_S:
        return cached[1]
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)
    rows = await client.search_instruments("", "NFO", limit=1_000_000)
    _dump_cache[uid] = (now, rows)
    return rows


def to_instrument_ref(row: dict) -> InstrumentRef:
    """Map a raw Kite NFO dump row onto the engine's instrument model."""
    exchange = str(row.get("exchange") or "NFO").upper() or "NFO"
    return InstrumentRef(
        instrument_id=str(row.get("instrument_token") or ""),
        tradingsymbol=str(row.get("tradingsymbol") or ""),
        option_type="CE" if str(row.get("instrument_type")) == "CE" else "PE",
        strike=float(row.get("strike") or 0.0),
        expiry=str(row.get("expiry") or "")[:10],
        lot_size=int(row.get("lot_size") or 1) or 1,
        tick_size=float(row.get("tick_size") or 0.05) or 0.05,
        exchange=exchange,
    )


def scan_underlyings(rows: list[dict], cfg: OIWallFlowConfig) -> list[str]:
    """Every NFO option name this config is willing to scan.

    Bounded by the same curated high-liquidity registry every other engine here
    uses. Indices are mapped from the display names the settings page writes
    onto the dump's ``name``. SENSEX / BFO names drop out rather than reach
    the scanner.
    """
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    eligible = set(HIGH_LIQUIDITY_STOCK_NAMES)

    listed = {str(r.get("name") or "").upper()
              for r in rows
              if r.get("segment") == "NFO-OPT"
              and str(r.get("exchange") or "NFO").upper() == "NFO"}

    names: set[str] = set()
    if cfg.stock_contracts:
        wanted = eligible if cfg.scan_all_stocks else {n.upper() for n in cfg.scan_stocks}
        names |= (wanted & eligible & listed)
    for display in cfg.scan_indices:
        option = option_name_of(display)
        if option and option in listed:
            names.add(option)
    return sorted(names)


# ------------------------------------------------------------------ status

def descriptor() -> dict:
    """Static identity, mirroring the contract. No live state."""
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "tagline": "Buy the first-resistance CE (or first-support PE) the chain is writing.",
        "how_it_works": (
            "Reads one expiry's option chain the way a desk does: classify each "
            "strike's OI+premium change, locate the put and call walls, score "
            "near-ATM flow, and buy the call wall when writers are covering calls "
            "and writing puts — never ATM. Stops are on the premium; a second kill "
            "is the opposing wall breaking on the underlying."
        ),
        "provenance": (
            "Motivated by the BSE Ltd 29-Sep-2026 chain (spot 3392.50, call wall "
            "3500, put wall 3300). That chain is a golden test: if it ever arms a "
            "PE, the engine is wrong. See docs/strategy/oi-wall-flow/."
        ),
        "validated": False,
        "calibration": JUDGEMENT,
        "calibrated_fields": [],
        "judgement_fields": sorted(JUDGEMENT_FIELDS),
        "headline_finding": (
            "Thresholds are judgement from one motivating chain, not a calibrated "
            "sample. The engine is the chain-reading, not a measured edge."
        ),
        "what_to_do": (
            "Trust the wall and the near-ATM flow on the row. A 3500 CE on a chain "
            "whose call wall is 3500 and whose puts are being written is the trade; "
            "a PE on that same chain is the engine being wrong."
        ),
        "evidence": (
            "BSE Ltd 29-Sep-2026, spot 3392.50: near-ATM calls short-covering, puts "
            "being written, call wall 3500 / put wall 3300. The engine must arm "
            "3500 CE at 84.15 and must not arm a PE. Thresholds in JUDGEMENT were "
            "read off that picture, not fitted to a sample."
        ),
    }


async def snapshot(uid: str) -> dict:
    """Operator view: config, what the scan found, and why nothing is armed."""
    cfg = get_config()
    from app.services.oi_wall_flow_runner import session_status, scan_state

    out: dict[str, Any] = {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "scan": scan_state(uid),
        "session": session_status(uid),
        "candidates": [],
        "positions": [],
        "record": {"trades": 0, "verdict": "no realised trades yet"},
        "orphan_positions": [],
        "blockers": [],
    }
    session = session_status(uid) or {}
    out["candidates"] = session.get("candidates") or []
    out["positions"] = session.get("positions") or []
    out["record"] = session.get("record") or out["record"]

    from app.services.oi_wall_flow_runner import auto_execute, is_paper
    paper, auto = is_paper(uid), auto_execute(uid)
    out["mode"] = {
        "is_paper": paper,
        "auto_execute": auto,
        "note": ("Paper/live is the account's Trading Mode setting and manual/auto is the "
                 "engine's — both shared with every Kite strategy, neither stored here."),
    }
    if not cfg.enabled:
        out["blockers"].append(
            "this engine is switched off — turn it on in its own settings; "
            "paper/live and manual/auto are elsewhere and unaffected")
    out["warnings"] = list(cfg.warnings())
    out["warnings"].append(
        "not validated: thresholds are judgement from one chain, not a calibrated "
        "sample. See docs/strategy/oi-wall-flow/.")
    if not paper:
        out["warnings"].append(
            "LIVE: this account places real orders. Every entry carries a stop and, "
            f"under stop_mode={cfg.stop_mode}, a broker-side GTT.")

    try:
        rows = await nfo_dump(uid)
        names = scan_underlyings(rows, cfg)
        out["universe"] = {"underlyings": len(names), "sample": names[:10]}
        if not names:
            out["blockers"].append(
                "universe is empty — pick indices or high-liquidity stocks in settings")
    except Exception as exc:                                       # noqa: BLE001
        out["blockers"].append(f"instrument dump unavailable: {exc}")
        out["universe"] = {"underlyings": 0, "sample": []}

    try:
        from app.services.oi_wall_flow_runner import orphan_positions
        orphans = await orphan_positions(uid, cfg)
    except Exception as exc:                                       # noqa: BLE001
        orphans = []
        log.debug("OI Wall Flow orphan check failed for %s: %s", uid, exc)
    out["orphan_positions"] = orphans
    for o in orphans:
        out["blockers"].append(
            f"open position {o['symbol']} ({o['quantity']} @ {o['entry_price']}) "
            f"is not accounted for — adopt or close it")
    return out
