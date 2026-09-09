"""When a Sterling Kite Engine entry dies, and why. Pure — no I/O, no broker types.

Two independent rules, whichever fires FIRST:

* the RED COUNTER — ``exit_mode`` many SuperTrend lines have flipped against the
  position (plus a fresh counter-arrow for ``three_red_signal``);
* the TRAILING STOP — price traded through the trail.

This lives in the engine package, not in the scanner, because the live scanner and the
backtest replay must answer this question identically. They previously each carried
their own copy of the red-count loop (the backtest's was documented as "identical to
the live ``scanner.is_active`` loop"), which is exactly the kind of duplication that
silently desyncs the moment one side gains a rule the other lacks.
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.engines.common.exit_counter import exit_needs_counter_signal, get_exit_threshold
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig, TrailTarget


def trail_level(r, direction: str, entry_i: int, at_i: int,
                cfg: SterlingKiteEngineConfig) -> float:
    """The trail level to show/enforce for an entry at ``entry_i``, as of bar ``at_i``.

    Default: the tightest still-aligned line (the validated fast trail). With
    ``exit_aligned_trail`` on: the line whose flip is the ``exit_mode``-th red, so the
    stop breach coincides with the red count instead of the tightest line pre-empting
    it. Falls back to the entry bar's ``trail_target`` line when no aligned line remains.

    The threshold line is used ONLY while it is still aligned (audit lead 28). Once a
    SuperTrend flips it jumps to the other side of price, so an unguarded
    ``trail_value_for_threshold`` put the stop above a long's price and the very next
    bar "breached" it. That fires at the WRONG count: under ``three_red`` the threshold
    line is the slow one, and slow can flip while fast and mid are still green — one
    red, not three — so the trade exited immediately under the setting whose entire
    purpose is to wait for three. Falling back to the tightest still-aligned line keeps
    the intent (do not let the fast line pre-empt the counter) without ever resting the
    stop on the wrong side of price; when NO line is aligned the count has reached three
    and the red-count rule is what ends the trade anyway.
    """
    if getattr(cfg, "exit_aligned_trail", False):
        threshold = get_exit_threshold(cfg.exit_mode)
        name = {1: "fast", 2: "mid", 3: "slow"}.get(int(threshold), "fast")
        aligned = r.green_lines(direction, at_i)
        value = (r.trail_value_for_threshold(at_i, threshold) if name in aligned
                 else r.best_trail_line_value(direction, at_i))
    else:
        value = r.best_trail_line_value(direction, at_i)
    if value > 0:
        return float(value)
    target: TrailTarget = cfg.trail_target
    return float(r.line(target)[entry_i])


def trail_exit_index(r, direction: str, entry_i: int, last_idx: int,
                     cfg: SterlingKiteEngineConfig) -> Optional[int]:
    """First bar after entry whose price traded through the trail, or None.

    The level tested at bar ``j`` is the one that stood at the CLOSE OF ``j-1`` — the
    only level a live stop could actually have been resting at when bar ``j`` opened.
    Testing bar ``j`` against bar ``j``'s own trail would be lookahead: the SuperTrend
    at ``j`` is computed from ``j``'s own high/low, so the bar that breaks the stop
    would also be the bar that moved it.

    HA prices are synthetic indicator inputs. Only raw traded high/low can
    establish that an order at the previous close's stop could have triggered.
    """
    if not getattr(cfg, "price_stop_exit", True):
        return None
    if r.raw_low.size <= last_idx or r.raw_high.size <= last_idx:
        return None  # raw prices not carried — cannot compare honestly, so do not guess
    level = trail_level(r, direction, entry_i, entry_i, cfg)
    for j in range(entry_i + 1, last_idx + 1):
        candidate = trail_level(r, direction, entry_i, j - 1, cfg)
        level = max(level, candidate) if direction == "long" else min(level, candidate)
        if level <= 0:
            continue
        if direction == "long":
            if float(r.raw_low[j]) <= level:
                return j
        elif float(r.raw_high[j]) >= level:
            return j
    return None


def ratcheted_trail_level(r, direction: str, entry_i: int, at_i: int,
                         cfg: SterlingKiteEngineConfig) -> float:
    """Stop standing at a completed bar, including every intervening ratchet."""
    levels = (trail_level(r, direction, entry_i, j, cfg)
              for j in range(entry_i, max(entry_i, at_i) + 1))
    return (max if direction == "long" else min)(levels)


def reported_trail_level(r, direction: str, entry_i: int, exit_i: Optional[int],
                         last_idx: int, cfg: SterlingKiteEngineConfig) -> float:
    """The trail level to SHOW for a row — running or ended.

    Running: the level standing at the latest bar. Ended: the level that was
    actually in force when it ended, i.e. as of ``exit_i - 1``, which is the same
    level ``resolve_exit`` names in its reason string.

    Reading the level AT the exit bar instead looks at the SuperTrend after it
    flipped, so no aligned line remains, ``trail_level`` falls back to the ENTRY
    bar's line, and the board prints a stop far below the one its own exit chip
    quotes ("TSL 163.97" beside "TSL exit ≤ 581.44").
    """
    at = last_idx if exit_i is None else max(int(exit_i) - 1, entry_i)
    return ratcheted_trail_level(r, direction, entry_i, at, cfg)


def red_count_exit_index(r, direction: str, entry_i: int, last_idx: int,
                         cfg: SterlingKiteEngineConfig, longs, shorts) -> Optional[int]:
    """First bar at/after entry where the red counter satisfies ``exit_mode``, or None."""
    threshold = get_exit_threshold(cfg.exit_mode)
    needs_counter = exit_needs_counter_signal(cfg.exit_mode)
    for j in range(entry_i, last_idx + 1):
        if r.red_line_count(direction, j) < threshold:
            continue
        if not needs_counter:
            return j
        # Reds hit the threshold but a fresh counter-entry arrow is also required.
        if (direction == "long" and shorts[j]) or (direction == "short" and longs[j]):
            return j
    return None


def resolve_time_decay_exit_index(
    r, direction: str, entry_i: int, last_idx: int,
    cfg: SterlingKiteEngineConfig, *, is_stock: bool = False,
    max_consolidation_bars: int = 18, min_expansion_atr_mult: float = 0.5,
) -> Optional[int]:
    """Emit time-decay exit if momentum on a stock option stalls for >3 trading days (18 1H bars)."""
    if not is_stock or not getattr(cfg, "theta_time_stop", True):
        return None
    if (last_idx - entry_i) < max_consolidation_bars:
        return None

    atr = float(r.atr[entry_i]) if hasattr(r, "atr") and r.atr.size > entry_i and r.atr[entry_i] > 0 else 0.0
    if atr <= 0:
        return None

    check_idx = entry_i + max_consolidation_bars
    entry_px = float(r.basis_close[entry_i]) if hasattr(r, "basis_close") and r.basis_close.size > entry_i else 0.0
    check_px = float(r.basis_close[check_idx]) if hasattr(r, "basis_close") and r.basis_close.size > check_idx else 0.0
    if entry_px <= 0 or check_px <= 0:
        return None

    expansion = (check_px - entry_px) if direction == "long" else (entry_px - check_px)
    if expansion < (min_expansion_atr_mult * atr):
        return check_idx
    return None


def resolve_exit(
    r, direction: str, entry_i: int, last_idx: int,
    cfg: SterlingKiteEngineConfig, longs, shorts,
    *, is_stock: bool = False,
) -> Tuple[Optional[int], str]:
    """``(exit_bar_index, reason)`` for an entry at ``entry_i``, or ``(None, "")`` if it
    is still running at ``last_idx``.
    """
    red_j = red_count_exit_index(r, direction, entry_i, last_idx, cfg, longs, shorts)
    trail_j = trail_exit_index(r, direction, entry_i, last_idx, cfg)
    time_j = resolve_time_decay_exit_index(r, direction, entry_i, last_idx, cfg, is_stock=is_stock)

    candidates: list[Tuple[int, str]] = []
    if red_j is not None:
        threshold = get_exit_threshold(cfg.exit_mode)
        candidates.append((red_j, f"red count exit {threshold}/{threshold} ({cfg.exit_mode})"))
    if trail_j is not None:
        level = ratcheted_trail_level(r, direction, entry_i, trail_j - 1, cfg)
        side = "≤" if direction == "long" else "≥"
        candidates.append((trail_j, f"trail breach ({side} {level:.2f})"))
    if time_j is not None:
        candidates.append((time_j, "time decay exit (momentum stalled > 18 bars)"))
    if getattr(cfg, "time_stop_bars", 0) > 0 and (last_idx - entry_i) >= cfg.time_stop_bars:
        candidates.append((entry_i + cfg.time_stop_bars, f"time stop exit ({cfg.time_stop_bars} bars)"))

    if not candidates:
        return None, ""

    earliest_j, reason = min(candidates, key=lambda c: c[0])
    return earliest_j, reason
