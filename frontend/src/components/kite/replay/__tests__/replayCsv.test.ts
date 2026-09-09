import { describe, expect, it } from 'vitest';
import { replayCsvName, toCsv } from '../replayCsv';
import { toCsv as sharedToCsv } from '../../../../utils/csvExport';
import { SIGNAL_CSV_COLUMNS, signalKey, tradeCsvColumns } from '../replayColumns';

describe('escaping, through the shared exporter', () => {
  // Escaping itself is covered by utils/csvExport.test.ts. What is pinned here
  // is that the replay path actually goes THROUGH it — the two exporters this
  // replaced escaped nothing, so a symbol containing a comma corrupted the
  // file, and a third bespoke implementation would have repeated that.
  it('is the shared implementation, not a replay-local copy', () => {
    expect(toCsv).toBe(sharedToCsv);
  });

  it('quotes a value containing every special character at once', () => {
    const csv = toCsv([{ name: 'a,"b"\nc' }], [{ header: 'Name', value: (r) => r.name }]);
    expect(csv.split('\r\n')[0]).toBe('Name');
    expect(csv).toContain('"a,""b""\nc"');
  });

  it('renders an absent cell as empty, not as "null" or "0"', () => {
    const csv = toCsv(
      [{ slippage: null as number | null }],
      [{ header: 'Slippage', value: (r) => r.slippage }],
    );
    expect(csv).toBe('Slippage\r\n');
  });
});

describe('trade columns', () => {
  it('omits the friction columns when nothing measured friction', () => {
    const headers = tradeCsvColumns(false).map((c) => c.header);
    expect(headers).not.toContain('Slippage (INR)');
    expect(headers).not.toContain('Raw Entry');
  });

  it('includes them when friction was measured', () => {
    const headers = tradeCsvColumns(true).map((c) => c.header);
    expect(headers).toContain('Slippage (INR)');
    expect(headers).toContain('Raw Entry');
  });

  it('includes the Exit Reason column', () => {
    const cols = tradeCsvColumns(false);
    const exitReasonCol = cols.find((c) => c.header === 'Exit Reason');
    expect(exitReasonCol).toBeDefined();
    expect(exitReasonCol?.value({ exit_reason: 'TRAILING_STOP' } as never)).toBe('TRAILING_STOP');
    expect(exitReasonCol?.value({} as never)).toBe('');
  });

  it('rounds Invested (INR) and PnL cleanly without float precision artifacts', () => {
    const cols = tradeCsvColumns(false);
    const investedCol = cols.find((c) => c.header === 'Invested (INR)')!;
    const pnlUsdCol = cols.find((c) => c.header === 'PnL (INR)')!;
    const pnlPctCol = cols.find((c) => c.header === 'PnL (%)')!;

    // 8.13 * 6500 produces 52845.00000000001 in raw JavaScript
    const trade = {
      entry_price: 8.13,
      quantity: 6500,
      pnl_usd: 5720.000000000001,
      pnl_pct: 10.820000000000002,
    } as never;

    expect(investedCol.value(trade)).toBe(52845);
    expect(pnlUsdCol.value(trade)).toBe(5720);
    expect(pnlPctCol.value(trade)).toBe(10.82);
  });
});

describe('signal columns', () => {
  it('exports the contract and the underlying separately', () => {
    const headers = SIGNAL_CSV_COLUMNS.map((c) => c.header);
    expect(headers).toContain('Contract');
    expect(headers).toContain('Underlying');
  });

  it('leaves R:R empty rather than writing Infinity', () => {
    const col = SIGNAL_CSV_COLUMNS.find((c) => c.header === 'R:R')!;
    expect(col.value({ entry: 100, stop: 100, target: 130 } as never)).toBe('');
  });
});

describe('file names', () => {
  it('carries the session and its time span', () => {
    expect(replayCsvName('signals', '2026-09-04', '09:00:00', '15:30:00'))
      .toBe('sterling_replay_signals_2026-09-04_09-00-15-30.csv');
  });

  it('omits the span when it is unknown', () => {
    expect(replayCsvName('trades', '2026-09-04')).toBe('sterling_replay_trades_2026-09-04.csv');
  });
});

describe('row identity', () => {
  it('distinguishes two signals that collide on time, strategy and instrument', () => {
    // The natural key is not unique — a strategy can fire twice on one symbol
    // inside the same second — and React then drops or duplicates rows. This
    // was caught in the browser, not by a test, which is why it has one now.
    const a = makeCollider();
    expect(signalKey(a, 0)).not.toBe(signalKey(a, 1));
  });

  it('is stable for a given position', () => {
    const a = makeCollider();
    expect(signalKey(a, 3)).toBe(signalKey(a, 3));
  });
});

function makeCollider() {
  return {
    time_iso: '09:15:00',
    strategy: 'supertrend',
    instrument: 'NIFTY',
    direction: 'BULLISH',
    strength: 'STRONG',
    entry: 100,
    stop: 90,
    target: 130,
  } as never;
}
