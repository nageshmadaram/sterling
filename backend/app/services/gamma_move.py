"""Runtime plumbing for the Gamma Move strategy.

Config persistence and instrument resolution only. All strategy mathematics lives
in ``app.engines.gamma_move`` and is reachable from here without any broker
object, so replay exercises the same code the live path runs.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (CALIBRATION, CALIBRATED_FIELDS, CONTRACT_VERSION,
                                    GammaMoveConfig, InstrumentRef, SOURCE_GATES,
                                    STRATEGY_ID, STRATEGY_NAME)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "gamma_move_config"
_DUMP_TTL_S = 900.0
_dump_cache: dict[str, tuple[float, list[dict]]] = {}

INDEX_NAMES = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"})


def ist_today() -> date:
    return datetime.now(_IST).date()


def ist_now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


# ------------------------------------------------------------------ config

def get_config(uid: str | None = None) -> GammaMoveConfig:
    """The configuration currently in force.

    Two different fallbacks, kept apart on purpose:

    * **Nothing stored** -> the real defaults, whatever they are. A default is
      what applies when nobody has said otherwise; hardcoding `enabled=False`
      here used to make the shipped default a lie, because the dataclass said on
      and this said off.
    * **Stored but unreadable or invalid** -> defaults with the engine OFF. A
      config that will not validate must never become a trading config, and the
      failure would otherwise surface deep inside the engine mid-session. This
      one is a safety fallback and stays.

    A stored value always wins over a default. Changing a default must not
      overrule an operator who deliberately set something.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    try:
        from app.services import db
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception:
        log.warning("%s: config store unavailable; running with defaults OFF", STRATEGY_ID)
        return GammaMoveConfig(enabled=False)
    if not raw:
        return GammaMoveConfig()
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = GammaMoveConfig.field_names()
        merged = {**GammaMoveConfig().as_dict(),
                  **{k: v for k, v in dict(stored).items() if k in known}}
        for key in ("scan_stocks", "scan_indices",
                    "scan_expiries_indices", "scan_expiries_stocks",
                    "scan_weekly_series_indices", "scan_monthly_series_indices",
                    "scan_monthly_series_stocks"):
            if isinstance(merged.get(key), list):
                merged[key] = tuple(merged[key])
        return GammaMoveConfig(**merged).validate()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); running with defaults OFF",
                  STRATEGY_ID, exc)
        return GammaMoveConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> GammaMoveConfig:
    """Persist a config change. Validation is the engine's, not a second copy."""
    current = get_config(uid).as_dict()
    unknown = sorted(set(values) - set(current))
    if unknown:
        raise ValueError(f"Unknown {STRATEGY_ID} config fields: {', '.join(unknown)}")
    current.update(values)
    for key in ("scan_stocks", "scan_indices",
                "scan_expiries_indices", "scan_expiries_stocks",
                "scan_weekly_series_indices", "scan_monthly_series_indices",
                "scan_monthly_series_stocks"):
        if isinstance(current.get(key), list):
            current[key] = tuple(current[key])
    cfg = GammaMoveConfig(**current).validate()
    from app.services import db
    target_key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    db.set_config(target_key, json.dumps(cfg.as_dict(), separators=(",", ":")))
    return cfg


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
    return InstrumentRef(
        instrument_id=str(row.get("instrument_token") or ""),
        tradingsymbol=str(row.get("tradingsymbol") or ""),
        option_type="CE" if str(row.get("instrument_type")) == "CE" else "PE",
        strike=float(row.get("strike") or 0.0),
        expiry=str(row.get("expiry") or "")[:10],
        lot_size=int(row.get("lot_size") or 1) or 1,
        tick_size=float(row.get("tick_size") or 0.05) or 0.05,
        exchange="NFO",
    )


def stock_underlyings(rows: list[dict], cfg: GammaMoveConfig) -> list[str]:
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    eligible = set(HIGH_LIQUIDITY_STOCK_NAMES)
    listed = {str(r.get("name") or "").upper()
              for r in rows if r.get("segment") == "NFO-OPT"}
    names: set[str] = set()
    if cfg.stock_contracts:
        wanted = eligible if cfg.scan_all_stocks else {n.upper() for n in cfg.scan_stocks}
        names |= (wanted & eligible & listed)
    names |= ({n.upper() for n in cfg.scan_indices} & listed)
    return sorted(names)


def descriptor() -> dict:
    """Static identity. `validated` is a measurement, not a source-checklist."""
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "tagline": "Buys the option that writers are covering at a level.",
        "how_it_works": (
            "Finds F&O stocks trading at a support or resistance level, picks the strike "
            "carrying the most open interest there, and buys it when open interest falls, "
            "volume spikes and the premium rises together on the same 15-minute bar — the "
            "signature of option writers covering. Holds one to two sessions."
        ),
        "provenance": "Transcribed from a public podcast walkthrough; see docs/strategy/gamma-move/",
        "validated": False,
        "source_aligned": True,
        "source_aligned_at": "2026-09-09",
        "calibration": CALIBRATION,
        "calibrated_fields": sorted(CALIBRATED_FIELDS),
        # Occupancy on one snapshot, not a measured edge. Published so the
        # settings page can label the two source-rule toggles without mirroring
        # a second copy of the numbers.
        "source_gates": SOURCE_GATES,
        "headline_finding": (
            "Only the level filter is proven. A setup with spot inside its proximity "
            "band worked about twice as often as an average bar — the open-interest "
            "trigger on its own did no better than picking a bar at random."
        ),
        "what_to_do": (
            "Trust the distance badge on the row. Inside the band is the setup that was "
            "measured; outside it, the trigger is the only thing left and it showed no edge."
        ),
        "evidence": (
            "Share of bars reaching +30% within two sessions, at the calibrated 1% band: "
            "46.2% [31.6, 61.4] within 1% of a level, against 24.7% [20.9, 28.9] for the entry triple alone and 21.7% "
            "[21.5, 21.9] for every bar. The trigger's interval overlaps the baseline's; "
            "the level filter's does not. 598 contracts, 193,135 fifteen-minute bars, "
            "measured 2026-08-26. Source rules aligned 2026-09-09; thresholds unchanged "
            "because that sample cannot re-measure wall/spot-through. See "
            "docs/strategy/gamma-move/VALIDATION_REPORT.md."
        ),
    }


async def snapshot(uid: str) -> dict:
    cfg = get_config()
    from app.services.gamma_move_runner import session_status, scan_state
    from app.services.gamma_move_sim import state as _sim_state

    out: dict[str, Any] = {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "scan": scan_state(uid),
        "session": session_status(uid),
        "simulation": _sim_state(uid),
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

    from app.services.gamma_move_runner import auto_execute, is_paper
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
        "not validated: in calibration the entry trigger alone showed no edge — the "
        "measured edge is the level filter. Source-aligned 2026-09-09; thresholds still "
        "the 2026-08-26 calibration. See docs/strategy/gamma-move/VALIDATION_REPORT.md")
    if not paper:
        out["warnings"].append(
            "LIVE: this account places real orders. Every entry carries a stop and, "
            f"under stop_mode={cfg.stop_mode}, a broker-side GTT.")

    try:
        rows = await nfo_dump(uid)
        names = stock_underlyings(rows, cfg)
        out["universe"] = {"underlyings": len(names), "sample": names[:10]}
    except Exception as exc:
        out["blockers"].append(f"instrument dump unavailable: {exc}")
        out["universe"] = {"underlyings": 0, "sample": []}

    try:
        from app.services.gamma_move_runner import orphan_positions
        orphans = await orphan_positions(uid, cfg)
    except Exception as exc:
        orphans = []
        log.debug("Gamma Move orphan check failed for %s: %s", uid, exc)
    out["orphan_positions"] = orphans
    for o in orphans:
        out["blockers"].append(
            f"open position {o['symbol']} ({o['quantity']} @ {o['entry_price']}) "
            f"is not accounted for — adopt or close it")
    return out
