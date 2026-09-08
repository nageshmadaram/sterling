import { describe, expect, it } from 'vitest';
import { REPLAY_SPEEDS, speedLabel, stepSpeed } from '../replaySpeeds';

describe('the speed ladder', () => {
  // The keyboard used to walk [1,5,10,50,100,250,500,1000,5000] while the
  // toolbar rendered six of those, so `+` could land on 250 and leave every
  // pill unhighlighted — a control that looked broken.
  it('is the only list, and every rung has a label', () => {
    expect(REPLAY_SPEEDS).toEqual([1, 5, 10, 25, 50, 100, 5000]);
    REPLAY_SPEEDS.forEach((s) => expect(speedLabel(s)).toBeTruthy());
  });

  it('labels the top of the ladder MAX rather than 5000x', () => {
    expect(speedLabel(5000)).toBe('MAX');
    expect(speedLabel(50)).toBe('50×');
  });

  it('walks the whole ladder upward and stops at the top', () => {
    let s: number = REPLAY_SPEEDS[0];
    const seen = [s];
    for (let i = 0; i < 10; i += 1) {
      s = stepSpeed(s, 1);
      seen.push(s);
    }
    expect(seen[6]).toBe(5000);
    expect(seen[7]).toBe(5000);
    expect(seen[9]).toBe(5000);
    seen.forEach((v) => expect(REPLAY_SPEEDS).toContain(v as never));
  });

  it('walks downward and stops at the bottom', () => {
    expect(stepSpeed(5, -1)).toBe(1);
    expect(stepSpeed(1, -1)).toBe(1);
  });

  it('recovers from an off-ladder speed set elsewhere', () => {
    // The backend accepts any float and clamps to [0.5, 5000]; landing on 250
    // must still leave +/- working rather than freezing at an unmatched index.
    expect(stepSpeed(250, 1)).toBe(5000);
    expect(stepSpeed(250, -1)).toBe(100);
  });
});
