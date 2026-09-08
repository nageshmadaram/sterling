import { describe, expect, it } from 'vitest';
import {
  ABSENT,
  ensureSeconds,
  extractDate,
  fmtDuration,
  fmtElapsed,
  fmtInr,
  fmtLots,
  fmtPct,
  fmtSessionDate,
  fmtSignedInr,
  fmtSignedPct,
  fmtSmartDate,
  fmtTime,
  isBullish,
  minutesToTime,
  rewardRisk,
  timeToMinutes,
} from '../replayFormat';

describe('absent values', () => {
  // The rule the whole redesign turns on: a value the engine never measured
  // renders an em dash, never a zero that reads as a measurement.
  it.each([undefined, null, NaN, Infinity])('renders %s as an em dash', (v) => {
    expect(fmtInr(v as number)).toBe(ABSENT);
    expect(fmtSignedInr(v as number)).toBe(ABSENT);
    expect(fmtPct(v as number)).toBe(ABSENT);
    expect(fmtLots(v as number)).toBe(ABSENT);
    expect(fmtDuration(v as number)).toBe(ABSENT);
  });

  it('does not confuse a real zero with an absent value', () => {
    expect(fmtInr(0)).toBe('₹0.00');
    expect(fmtSignedInr(0)).toBe('₹0.00');
  });
});

describe('money', () => {
  it('groups in the Indian system', () => {
    expect(fmtInr(1248.5)).toBe('₹1,248.50');
    expect(fmtInr(1234567)).toBe('₹12,34,567.00');
  });

  it('always signs a P&L figure', () => {
    expect(fmtSignedInr(1248.5)).toBe('+₹1,248.50');
  });

  it('uses a real minus sign, not a hyphen', () => {
    // U+002D does not align in tabular figures; U+2212 does.
    expect(fmtSignedInr(-312)).toContain('−');
    expect(fmtSignedInr(-312)).not.toContain('-');
  });
});

describe('percentages', () => {
  it('signs a change but not a rate', () => {
    expect(fmtSignedPct(2.4)).toBe('+2.4%');
    expect(fmtSignedPct(-1)).toBe('−1.0%');
    expect(fmtPct(62)).toBe('62%');
  });
});

describe('time', () => {
  it('accepts a bare clock and a full ISO string', () => {
    expect(fmtTime('10:47:05')).toBe('10:47:05');
    expect(fmtTime('2026-09-04T10:47:05')).toBe('10:47:05');
  });

  it('renders a placeholder rather than crashing on nothing', () => {
    expect(fmtTime(undefined)).toBe('--:--:--');
  });

  it.each([
    [0, '< 1m'],
    [47, '47m'],
    [72, '1h 12m'],
  ])('formats %i minutes as %s', (mins, want) => {
    expect(fmtDuration(mins)).toBe(want);
  });

  it('formats elapsed wall time', () => {
    expect(fmtElapsed(41)).toBe('41s');
    expect(fmtElapsed(192)).toBe('3m 12s');
  });

  it('round-trips minutes and clock times', () => {
    expect(timeToMinutes('09:15:00')).toBe(555);
    expect(minutesToTime(555)).toBe('09:15:00');
  });

  it('clamps a time past midnight rather than wrapping', () => {
    expect(minutesToTime(99999)).toBe('23:59:00');
    expect(minutesToTime(-50)).toBe('00:00:00');
  });

  it('formats a session date in IST regardless of the runner timezone', () => {
    expect(fmtSessionDate('2026-09-04')).toBe('Fri 4 Sep 2026');
    expect(fmtSessionDate('2026-09-04', true)).toBe('4 Sep');
    expect(fmtSessionDate('not-a-date')).toBe('not-a-date');
  });

  it('formats smart dates relative to IST market day', () => {
    // 2026-09-08 10:00:00 IST is 2026-09-08T04:30:00Z
    const refDate = new Date('2026-09-08T04:30:00Z');
    expect(fmtSmartDate('2026-09-08', refDate)).toBe('Today');
    expect(fmtSmartDate('2026-09-07', refDate)).toBe('Yesterday');
    expect(fmtSmartDate('2026-09-04', refDate)).toBe('4 Sep');
    expect(fmtSmartDate(null)).toBe(ABSENT);
    expect(fmtSmartDate(undefined)).toBe(ABSENT);
  });
});

describe('direction', () => {
  it.each(['BULLISH', 'LONG', 'BUY', 'bullish'])('treats %s as bullish', (d) => {
    expect(isBullish(d)).toBe(true);
  });

  it.each(['BEARISH', 'SHORT', 'SELL', undefined])('treats %s as not bullish', (d) => {
    expect(isBullish(d)).toBe(false);
  });
});

describe('reward to risk', () => {
  it('divides reward by risk', () => {
    expect(rewardRisk(100, 90, 130)).toBeCloseTo(3);
  });

  it('works for a short, where the stop is above the entry', () => {
    expect(rewardRisk(100, 110, 70)).toBeCloseTo(3);
  });

  it('returns null when the stop sits on the entry', () => {
    // Infinity is not an answer; "undefined" is, and the UI draws an em dash.
    expect(rewardRisk(100, 100, 130)).toBeNull();
  });

  it('returns null on missing inputs', () => {
    expect(rewardRisk(100, undefined, 130)).toBeNull();
  });
});

describe('ensureSeconds', () => {
  it('appends :00 to HH:MM', () => {
    expect(ensureSeconds('09:15')).toBe('09:15:00');
    expect(ensureSeconds('15:30')).toBe('15:30:00');
  });

  it('preserves existing seconds in HH:MM:SS', () => {
    expect(ensureSeconds('09:15:30')).toBe('09:15:30');
    expect(ensureSeconds('15:30:00')).toBe('15:30:00');
  });

  it('pads single digits with leading zero', () => {
    expect(ensureSeconds('9:5')).toBe('09:05:00');
    expect(ensureSeconds('9:5:2')).toBe('09:05:02');
  });

  it('falls back to default on invalid or empty values', () => {
    expect(ensureSeconds('')).toBe('09:00:00');
    expect(ensureSeconds(null)).toBe('09:00:00');
    expect(ensureSeconds(undefined)).toBe('09:00:00');
    expect(ensureSeconds('invalid')).toBe('09:00:00');
    expect(ensureSeconds('invalid', '15:30:00')).toBe('15:30:00');
  });
});

describe('extractDate', () => {
  it('extracts date from ISO strings with T', () => {
    expect(extractDate('2026-09-04T10:47:05')).toBe('2026-09-04');
    expect(extractDate('2026-08-03T09:15:00+05:30')).toBe('2026-08-03');
  });

  it('extracts date from bare date strings', () => {
    expect(extractDate('2026-09-04')).toBe('2026-09-04');
  });

  it('extracts date from timestamp_ms in IST', () => {
    // 2026-08-03 09:15:00 IST = 2026-08-03 03:45:00 UTC
    const ts = Date.UTC(2026, 7, 3, 3, 45, 0);
    expect(extractDate('09:15:00', ts)).toBe('2026-08-03');
  });

  it('returns empty string for missing or invalid inputs', () => {
    expect(extractDate(null, null)).toBe('');
    expect(extractDate(undefined, undefined)).toBe('');
    expect(extractDate('10:47:05', null)).toBe('');
    expect(extractDate('invalid', undefined)).toBe('');
  });
});


