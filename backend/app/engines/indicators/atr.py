import numpy as np
from numpy.typing import NDArray


def atr_percentile(atr: NDArray[np.float64], lookback: int = 100) -> float:
    """Current ATR as percentile rank vs trailing lookback bars. Returns 0-100."""
    recent = atr[-lookback:]
    valid = recent[~np.isnan(recent)]
    if len(valid) < 5 or np.isnan(atr[-1]):
        return 50.0
    return float(np.sum(atr[-1] > valid) / len(valid) * 100)


def true_range(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Wilder's true range. ``tr[0]`` is 0, as the recurrence below expects."""
    n = len(closes)
    tr = np.zeros(n)
    if n < 2:
        return tr
    prev = closes[:-1]
    tr[1:] = np.maximum.reduce([
        highs[1:] - lows[1:],
        np.abs(highs[1:] - prev),
        np.abs(lows[1:] - prev),
    ])
    return tr


def compute_atr(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Wilder's ATR (RMA smoothing).

    True range is vectorised, and the RMA is a one-pole IIR
    (``y[i] = a*y[i-1] + (1-a)*x[i]`` with ``a = (period-1)/period``) evaluated
    with :func:`scipy.signal.lfilter`.

    The same numbers as the loops this replaced to about 1e-14 absolute — the
    loop divided by ``period`` each step where the filter multiplies by
    ``1/period``, which is the same arithmetic in a different order and so
    rounds differently in the last bit or two. That is many orders of magnitude
    below a tick; it is recorded here because "identical" would have been the
    easier claim and it is not quite the true one.

    Speed is the reason, and it is not cosmetic: ATR is computed inside
    SuperTrend as well as on its own, so a walk-forward run over every bar of
    every fold spent most of its time in these two loops. A harness too slow to
    finish is a harness nobody runs.
    """
    n = len(closes)
    tr = true_range(highs, lows, closes)

    atr = np.zeros(n)
    if n <= period or period < 1:
        return atr

    seed = float(np.mean(tr[1 : period + 1]))
    atr[period] = seed
    if n == period + 1:
        return atr

    a = (period - 1) / period
    tail = tr[period + 1 :]
    try:
        from scipy.signal import lfilter
        out, _ = lfilter([1.0 / period], [1.0, -a], tail, zi=[a * seed])
        atr[period + 1 :] = out
    except Exception:                                              # noqa: BLE001
        prev = seed
        for i in range(period + 1, n):
            prev = (prev * (period - 1) + tr[i]) / period
            atr[i] = prev
    return atr
