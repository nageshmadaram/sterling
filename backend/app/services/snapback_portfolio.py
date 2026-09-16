"""Portfolio equity curve for the prospective book.

`daily_mtm.total_mtm` is per-position liquidation MTM: once a trade closes it stops
appearing, so summing it by date silently erases every realized result. Equity is

    realized net P&L to date  +  open liquidation MTM  -  accrued costs

carried forward across sessions, which is the only curve a drawdown limit can
honestly be measured against.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _session(value: Any) -> str:
    return str(value or "")[:10]


def build_equity_curve(
    *,
    daily_mtm: Iterable[Dict[str, Any]],
    outcomes: Iterable[Dict[str, Any]],
    costs: Optional[Iterable[Dict[str, Any]]] = None,
    allocation_capital: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """One row per session: realized to date, open MTM, accrued costs, equity."""
    daily_mtm = list(daily_mtm or [])
    outcomes = list(outcomes or [])
    costs = list(costs or [])

    # When each opportunity was realized, and for how much.
    realized_on: Dict[str, List[Dict[str, Any]]] = {}
    for outcome in outcomes:
        session = _session(outcome.get("exit_ts") or outcome.get("observed_at"))
        if not session:
            continue
        realized_on.setdefault(session, []).append(outcome)

    closed_before: Dict[str, str] = {}
    for session, rows in realized_on.items():
        for row in rows:
            opp = str(row.get("opportunity_id") or "")
            if opp:
                closed_before[opp] = session

    costs_on: Dict[str, float] = {}
    for row in costs:
        session = _session(row.get("session_date") or row.get("observed_at"))
        if session:
            costs_on[session] = costs_on.get(session, 0.0) + _f(row.get("total_cost"))

    sessions = sorted(
        {_session(r.get("session_date")) for r in daily_mtm if _session(r.get("session_date"))}
        | set(realized_on)
        | set(costs_on)
    )

    curve: List[Dict[str, Any]] = []
    realized_to_date = 0.0
    accrued_costs = 0.0

    for session in sessions:
        for row in realized_on.get(session, []):
            realized_to_date += _f(row.get("actual_total_pnl"))

        accrued_costs += costs_on.get(session, 0.0)

        # Open MTM excludes any position already realized on or before this session,
        # so a closed trade is counted once, as realized.
        open_mtm = 0.0
        for row in daily_mtm:
            if _session(row.get("session_date")) != session:
                continue
            opp = str(row.get("opportunity_id") or "")
            closed = closed_before.get(opp)
            if closed and closed <= session:
                continue
            open_mtm += _f(row.get("total_mtm"))

        curve.append(
            {
                "session_date": session,
                "realized_net_pnl_to_date": realized_to_date,
                "open_liquidation_mtm": open_mtm,
                "accrued_costs": accrued_costs,
                "equity_pnl": realized_to_date + open_mtm,
                "allocation_capital": allocation_capital,
            }
        )

    return curve


def max_drawdown_pct(
    curve: Iterable[Dict[str, Any]],
    *,
    allocation_capital: float,
) -> Optional[float]:
    """Peak-to-trough drawdown of the equity curve, as a share of allocated capital.

    Unknown capital gives UNKNOWN, never a flattering zero.
    """
    if not allocation_capital or allocation_capital <= 0:
        return None

    peak = None
    worst = 0.0
    for row in curve or []:
        equity = _f(row.get("equity_pnl"))
        peak = equity if peak is None else max(peak, equity)
        worst = max(worst, peak - equity)

    return worst / allocation_capital * 100.0


def equity_series(curve: Iterable[Dict[str, Any]]) -> List[float]:
    return [_f(row.get("equity_pnl")) for row in curve or []]
