/**
 * Every board shows the same columns.
 *
 * This is the property the boards kept losing. Each one used to name its own
 * column list — ORB eleven, Adaptive Edge twelve, ATM eight — and on top of
 * that the board dropped any column no row could fill. The result was that
 * switching tabs moved the stop, renamed the exit, and made you re-find every
 * number.
 *
 * Asserted against the real adapters with real-shaped data, so it fails if an
 * engine quietly starts asking for its own set again.
 */
import { describe, it, expect, vi } from 'vitest';
import { ROW_METRICS } from '../signalRowSpec';

// BoardTicket pulls a live quote; the parity being tested is structural.
vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));
import { render } from '@testing-library/react';
import React from 'react';
import { SignalBoard, BOARD_COLUMNS, DEFAULT_HIDDEN_COLUMNS, visibleColumns, COLUMNS, type ColumnId } from '../SignalBoard';
import type { BoardSignal } from '../boardTypes';
import { BoardTicket } from '../BoardTicket';
import { supertrendToBoard } from '../supertrendAdapter';
import { orbToBoard } from '../orbAdapter';
import { adaptiveEdgeToBoard } from '../adaptiveEdgeAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';
import type { OrbFeedEntry } from '../../../../utils/niftyOrbSignalAdapter';
import type { AdaptiveEdgeRow } from '../../AdaptiveEdgePanel';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

const stLeg: OptionLeg = {
  moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY26AUG24000CE',
  strike: 24000, expiry: '2026-08-27', lot_size: 75,
  premium_spot: 200, entry_sl: 160, premium_sl: 185, is_active: true,
};
const stRow: EngineSignalRow = {
  underlying: 'NIFTY 50', token: 1, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
  legs: [stLeg], spot: 24100, stop_loss: 24000, score: 82,
  timestamp_ms: NOW, is_active: true, source: 'spot',
};

const orbEntry: OrbFeedEntry = {
  id: 'ORB-1', strategy: 'ORB', underlying: 'NIFTY', direction: 'long', state: 'SIGNAL',
  spot: 24100, orbHigh: 24120, orbLow: 24000, vwap: 24050, atr: 30, volumeRatio: 1.4,
  optionSymbol: 'NIFTY26AUG24100CE', optionStrike: 24100, optionType: 'CE',
  optionExpiry: '2026-08-27', optionPremium: 180, stopPremium: 140, targetPremium: 260,
  quantity: 150, riskInr: 3000, maxLossInr: 27000, deltaIsEstimated: true,
  deltaSource: 'implied', delta: 0.56, impliedVol: 0.11, gamma: 0.0009,
  thetaPerDay: -10, vegaPerPoint: 16.9, exchange: 'NFO', lotSize: 75,
  underlyingEntry: 24120, underlyingStop: 24030, dataSource: 'kite',
  quoteAgeS: 3, reason: null, timestamp: new Date(NOW).toISOString(),
  vwapBasis: 'volume', volumeConfirmed: true,
};

const aeRow: AdaptiveEdgeRow = {
  id: 'ae-1', parentId: 'p1', kind: 'option', origin: 'spot_scan' as AdaptiveEdgeRow['origin'],
  instrument: 'TCS26AUG3200CE', exchange: 'NFO', moneyness: 'ATM', optionType: 'CE',
  entry: 58, sl: 40, tsl: 51, exit: null, ltp: 62, strike: 3200, expiry: '2026-08-27',
  lotSize: 175, entryTime: new Date(NOW).toISOString(), exitTime: null, open: true,
  tapeSymbol: 'NFO:TCS26AUG3200CE', underlying: 'TCS',
  spotEntry: 3195, spotSl: 3150, spotTsl: 3170, spotExit: null,
  score: 85, poc: 3195, vwap: 3196, cvd: 1200,
  whyClosed: null, resolutionReason: null, observationTime: NOW,
  featureQuality: 'OPEN', decision: 'HOLD', horizon: 'IMPULSE',
};

/** Same shape every board is mounted with. */
const boards: Record<string, BoardSignal[]> = {
  supertrend: supertrendToBoard([stRow]),
  orb: [orbToBoard(orbEntry)],
  adaptive_edge: adaptiveEdgeToBoard([aeRow]),
};

const headersOf = (signals: BoardSignal[], hidden: ReadonlySet<ColumnId>) => {
  const { container } = render(
    <SignalBoard
      signals={signals}
      columns={BOARD_COLUMNS}
      hidden={hidden}
      openId={null}
      onToggle={() => {}}
      nowMs={NOW}
    />,
  );
  return [...container.querySelectorAll('.sb-head')].map((h) => h.textContent!.trim());
};

const DEFAULTS = new Set<ColumnId>(DEFAULT_HIDDEN_COLUMNS);

describe('column parity across engines', () => {
  it('gives every board the same headers, in the same order', () => {
    const [first, ...rest] = Object.entries(boards).map(([name, signals]) => [name, headersOf(signals, DEFAULTS)] as const);
    for (const [name, headers] of rest) {
      expect(headers, `${name} vs ${first[0]}`).toEqual(first[1]);
    }
  });

  it('opens on SuperTrend’s own column names, in its order', () => {
    // Labels and widths come from signalRowSpec, the table SuperTrend's rows
    // use, so a cell sits in the same place whichever board you are on.
    expect(headersOf(boards.orb, DEFAULTS)).toEqual([
      'Instrument', 'Status', 'Exc.', 'Leg (Δ)', 'Entry (Δpts)', 'SL', 'TSL', 'Target', 'Exited', 'LTP', 'Time',
      // Trade and Chart are columns now, on every board rather than only the one
      // whose bespoke table happened to have them. They are hideable like any
      // other, which is the point: a board is read far more often than it is
      // traded from.
      'Trade', 'Chart',
    ]);
  });

  it('keeps a column an engine cannot fill, rather than differing', () => {
    // ORB does not trail. The TSL column stays and reads as dashes, which
    // says so — the alternative is a board that silently differs from the
    // one beside it.
    expect(headersOf(boards.orb, DEFAULTS)).toContain('TSL');
    expect(boards.orb[0].levels.trail).toBeNull();
  });

  it('offers the same extras to every board', () => {
    const none = new Set<ColumnId>();
    for (const [name, signals] of Object.entries(boards)) {
      const headers = headersOf(signals, none);
      for (const extra of ['Qty', 'At risk', 'Score']) {
        expect(headers, `${name} missing ${extra}`).toContain(extra);
      }
    }
  });

  it('names every canonical column, so none is unreachable', () => {
    const known = new Set(COLUMNS.map((c) => c.id));
    for (const id of BOARD_COLUMNS) expect(known, id).toContain(id);
    for (const id of DEFAULT_HIDDEN_COLUMNS) expect(BOARD_COLUMNS, id).toContain(id);
  });

  it('hides the engine tag on a single-engine board and shows it on a mixed one', () => {
    // The one column that still comes and goes, because on a single-engine
    // board it repeats the same three letters down every row.
    const mixed = [...boards.orb, ...boards.adaptive_edge];
    expect(visibleColumns(boards.orb, BOARD_COLUMNS).map((c) => c.id)).not.toContain('engine');
    expect(visibleColumns(mixed, BOARD_COLUMNS).map((c) => c.id)).toContain('engine');
  });
});

describe('expanded-row parity across engines', () => {
  // The expanded row is where the boards diverged most: SuperTrend's leg
  // opened onto a calculator and a QuoteDetail with Buy and Sell, while the
  // others showed two read-only cards and no way to act. They now mount the
  // same BoardTicket.
  const expand = (signals: BoardSignal[]) => {
    const parent = signals[0];
    const target = parent.children?.[0] ?? parent;
    const { container } = render(
      <SignalBoard
        signals={signals}
        columns={BOARD_COLUMNS}
        hidden={DEFAULTS}
        openId={target.id}
        onToggle={() => {}}
        onToggleGroup={() => {}}
        renderDetail={(sig) => <BoardTicket signal={sig} />}
        nowMs={NOW}
      />,
    );
    return container;
  };

  it.each(Object.keys(boards))('%s expands onto the shared ticket', (name) => {
    const container = expand(boards[name]);
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent!.trim());
    expect(labels, `${name} has no BUY`).toContain('BUY');
    if (name === 'orb') {
      // Long-options-only: closing a held option is Positions, not this row.
      expect(labels, `${name} must not offer Sell-to-open`).not.toContain('SELL');
    } else {
      expect(labels, `${name} has no SELL`).toContain('SELL');
    }
  });

  it('offers sizing on every board', () => {
    for (const [name, signals] of Object.entries(boards)) {
      const container = expand(signals);
      expect(container.textContent, `${name} has no sizing`).toMatch(/position|qty|lot/i);
    }
  });
});

describe('each engine states its own provenance', () => {
  // The one slot on the row that is deliberately NOT shared. Four engines,
  // four different answers to "where did this come from" — a badge that said
  // the same thing everywhere would be decoration.
  it('gives every engine a badge in its own vocabulary', () => {
    const labels = Object.entries(boards).map(([name, signals]) => [name, signals[0].origin?.label] as const);
    for (const [name, label] of labels) expect(label, `${name} has no origin`).toBeTruthy();
    const distinct = new Set(labels.map(([, l]) => l));
    expect(distinct.size, 'engines share a badge label').toBe(labels.length);
  });

  it('explains each one in more than a restatement of its label', () => {
    for (const [name, signals] of Object.entries(boards)) {
      const origin = signals[0].origin!;
      expect(origin.hint.length, `${name} hint too short`).toBeGreaterThan(30);
      expect(origin.hint.toLowerCase()).not.toBe(origin.label.toLowerCase());
    }
  });

  it('reads SuperTrend’s as the scan that found it', () => {
    expect(boards.supertrend[0].origin!.label).toBe('SPOT');
  });

  it('reads ORB’s as the feed behind the numbers', () => {
    // ORB is configurable between Kite and TrueData, and the two disagree.
    expect(boards.orb[0].origin!.label).toBe('KITE');
  });

  it('reads Adaptive Edge’s as which model produced it', () => {
    expect(boards.adaptive_edge[0].origin!.label).toBe('SPOT SCAN');
  });
});

/**
 * The two tables' shared presentation tokens.
 *
 * SuperTrend's table is a bespoke implementation and Adaptive Edge's is the
 * shared `SignalBoard`. They already render against one column spec and one set
 * of geometry — `ROW_METRICS` — but SuperTrend held the numbers as literals, so
 * they agreed by coincidence rather than by construction and could drift on the
 * next edit to either.
 */
describe('ROW_METRICS is the single type and geometry scale', () => {
  it('carries the type scale both tables use', () => {
    // If a value moves here it moves in both tables. That is the point.
    expect(ROW_METRICS.instrumentFontSize).toBe(13);
    expect(ROW_METRICS.cellFontSize).toBe(11);
    expect(ROW_METRICS.parentFontSize).toBe(12);
  });

  it('carries the row geometry both tables use', () => {
    expect(ROW_METRICS.legHeight).toBe(41);
    expect(ROW_METRICS.gap).toBe(16);
    expect(ROW_METRICS.legPadding).toBe('0 16px');
    expect(ROW_METRICS.parentPadding).toBe('10px 12px');
  });
});
