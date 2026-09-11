import numpy as np
from numpy.typing import NDArray
from typing import Tuple


def compute_ema(values: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Standard EMA with an SMA seed for the first ``period`` bars.

    The recurrence ``y[i] = k*x[i] + (1-k)*y[i-1]`` is a one-pole IIR filter, so
    it is evaluated with :func:`scipy.signal.lfilter` rather than a Python loop.
    Bit-for-bit the same numbers; roughly two orders of magnitude faster on the
    shapes this repo uses.

    That is not a micro-optimisation. This function is called once per EMA per
    evaluation, and a walk-forward run evaluates a strategy on every bar of
    every window of every fold — one four-EMA strategy spent 75% of its replay
    in here, and a harness too slow to finish is a harness nobody runs.

    The loop is kept as a fallback so the module still imports without scipy,
    and as the reference the equivalence test checks against.
    """
    n = len(values)
    ema = np.zeros(n)
    if n < period or period < 1:
        return ema

    k = 2.0 / (period + 1)
    seed = float(np.mean(values[:period]))
    ema[period - 1] = seed
    if n == period:
        return ema

    tail = np.asarray(values[period:], dtype=np.float64)
    try:
        from scipy.signal import lfilter
        # zi carries the seed into the first output sample, which is what makes
        # this identical to the loop rather than merely similar.
        out, _ = lfilter([k], [1.0, -(1.0 - k)], tail, zi=[(1.0 - k) * seed])
        ema[period:] = out
    except Exception:                                              # noqa: BLE001
        prev = seed
        for i in range(period, n):
            prev = values[i] * k + prev * (1.0 - k)
            ema[i] = prev
    return ema


def ema_dual(
    close: NDArray[np.float64], fast: int = 21, slow: int = 55
) -> Tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Returns (ema_fast, ema_slow). Used for dual-EMA crossover regime."""
    return compute_ema(close, fast), compute_ema(close, slow)
