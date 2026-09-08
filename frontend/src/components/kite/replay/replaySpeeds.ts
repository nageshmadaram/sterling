/**
 * The replay speed ladder — one array, consumed by the pills AND the keyboard.
 *
 * They used to disagree: `+`/`-` walked `[1,5,10,50,100,250,500,1000,5000]`
 * while the toolbar rendered only six of those, so pressing `+` could land on
 * 250 and leave every pill unhighlighted, which reads as a broken control.
 */

export const MAX_SPEED = 5000;
export const REPLAY_SPEEDS = [1, 5, 10, 25, 50, 100, MAX_SPEED] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];

/** Above this, per-event feedback (toasts, value flashes) becomes a strobe. */
export const HIGH_SPEED_THRESHOLD = 100;

export function speedLabel(speed: number): string {
  return speed >= MAX_SPEED ? 'MAX' : `${speed}×`;
}

/**
 * Step along the ladder.
 *
 * Defensive about a speed that is not on it — the backend clamps to
 * `[0.5, 5000]` and can be driven from elsewhere, so an off-ladder value must
 * still leave `+`/`-` working rather than freezing at an unmatched index.
 */
export function stepSpeed(current: number, dir: 1 | -1): number {
  const exact = REPLAY_SPEEDS.indexOf(current as ReplaySpeed);
  if (exact >= 0) {
    const next = Math.min(REPLAY_SPEEDS.length - 1, Math.max(0, exact + dir));
    return REPLAY_SPEEDS[next];
  }
  // Off-ladder: move to the nearest rung in the requested direction.
  if (dir === 1) {
    return REPLAY_SPEEDS.find((s) => s > current) ?? REPLAY_SPEEDS[REPLAY_SPEEDS.length - 1];
  }
  const below = REPLAY_SPEEDS.filter((s) => s < current);
  return below.length ? below[below.length - 1] : REPLAY_SPEEDS[0];
}
