/**
 * The signal board every engine renders through.
 *
 * Rows are flex, with every column but the instrument name at a fixed pixel
 * width taken from signalRowSpec — the same table SuperTrend's own rows use, so
 * a cell lands in the same place whichever engine you are looking at. Fixed
 * widths are the point: a column of tabular numbers only reads as a column if
 * every row agrees where it starts.
 *
 * Rows are grouped by trading day. Sections stick to the top while their day is
 * on screen, which is the only part of a long scroll that tells you where you
 * are.
 */
import React from 'react';
import { k, tint } from '../../../styles/kiteUI';
import {
  ACTIONABLE, ENGINE_TAG, LIVE_BUCKET, STATUS_LABEL, STATUS_RANK, flattenSignals, groupByDay,
  isDayExpandedByDefault, isLiveDayKey, markLegs, parentStamp, sessionDayDate, sessionDayKey, sessionDayLabel,
  shiftSessionDay, stamp, trailBreached,
} from './boardTypes';
import type { BoardDayMove, BoardOrigin, BoardSignal, BoardStatus, EngineId } from './boardTypes';
import { StatCard, StatCardGrid } from './StatCard';
import {
  DAY_HEAD_METRICS, EDGE_METRICS, HEAD_METRICS, LEG_BG, LEG_INDENT, PARENT_METRICS, ROW_METRICS,
  SIGNAL_LEFT_COLUMNS, SIGNAL_RIGHT_COLUMNS, instrumentFlex,
} from './signalRowSpec';
import { DraggableColHeader, makeHscrollSync } from './tableMechanics';
import { useKiteSettings } from '../../../store/useKiteSettings';
import { fitColumns } from './columnFit';
import { Tip } from '../InfoTooltip';
import { InstrumentLabel, parseInstrument } from '../InstrumentLabel';

/**
 * Determines whether a signal or contract leg belongs to a Weekly option series.
 */
export function isWeeklySignal(signal: BoardSignal): boolean {
  if (signal.instrument?.symbol) {
    const parsed = parseInstrument(signal.instrument.symbol);
    if (parsed?.isWeekly !== undefined) return parsed.isWeekly;
  }
  if (signal.instrument?.expiry) {
    const exp = signal.instrument.expiry.toLowerCase();
    if (exp.includes('weekly') || exp.includes('w')) return true;
  }
  if (signal.children && signal.children.length > 0) {
    for (const child of signal.children) {
      if (child.instrument?.symbol) {
        const parsed = parseInstrument(child.instrument.symbol);
        if (parsed?.isWeekly !== undefined) return parsed.isWeekly;
      }
      if (child.instrument?.expiry) {
        const exp = child.instrument.expiry.toLowerCase();
        if (exp.includes('weekly') || exp.includes('w')) return true;
      }
    }
  }
  return false;
}


export type ColumnId =
  | 'instrument' | 'engine' | 'status' | 'exchange' | 'leg'
  | 'ltp' | 'entry' | 'stop' | 'trail' | 'target' | 'exit'
  | 'qty' | 'risk' | 'score' | 'time'
  // Today's move. SuperTrend has shown these three since long before this board
  // existed; they were the columns a swap onto it would silently have deleted.
  | 'chg' | 'chgPct' | 'dir'
  // Actions, as columns the picker can switch off — see the note in signalRowSpec.
  | 'trade' | 'chart'
  // Progress toward the closing RULE, which is not the price it got out at.
  | 'exitState';

interface ColumnDef {
  id: ColumnId;
  label: string;
  /** Fixed pixels, so a decimal point lands in the same place on every row.
   *  Zero means the instrument cell, which is the only one that flexes. */
  width: number;
  align: 'left' | 'right';
  hint?: string;
}

/**
 * Every column the board can show, in reading order: what it is, then what it
 * is worth now, then where it gets out, then how big, then when.
 */
/**
 * Width a row spends on things that are not columns — the inline action
 * buttons. Counted so the fit does not hand every pixel to columns and push
 * the buttons off the edge.
 */
const ACTION_RESERVE = 96;

export /*
 * Labels and widths come from `signalRowSpec` — SuperTrend's own table — ON
 * PURPOSE, so a cell sits in the same place with the same name whichever board
 * you are on. `columnParity.test.tsx` pins it.
 *
 * I briefly decoupled these, on the reading that SuperTrend "should have its own
 * column names". That reverses a deliberate decision whose whole point is the
 * cross-tab consistency asked for earlier in the same conversation, and it makes
 * the two tables show DIFFERENT words for the same number. Reverted, because the
 * thing actually wrong was elsewhere: `SuperTrendSharedBoard` was requesting the
 * shared column SET and appending its own, which handed SuperTrend five columns
 * it has never had (Engine, Status, Qty, At risk, Score).
 *
 * If the words themselves should diverge, that is a call to make deliberately —
 * the mechanism is a `label` per column def here, and the cost is that switching
 * tabs renames columns.
 */
const COLUMNS: readonly ColumnDef[] = [
  // Widths, labels and alignment come from SuperTrend's table (see
  // signalRowSpec) so a cell sits in the same place on every board. The extras
  // it does not have are sized to the same rhythm.
  { id: 'instrument', label: 'Instrument', width: 0, align: 'left' },
  { id: 'engine', label: 'Engine', width: 42, align: 'left', hint: 'Which engine produced this signal' },
  { id: 'status', label: 'Status', width: 66, align: 'left', hint: 'Armed = valid setup, not yet entered' },
  { id: 'exchange', label: SIGNAL_LEFT_COLUMNS.exc.label, width: SIGNAL_LEFT_COLUMNS.exc.width, align: 'left', hint: 'Exchange the contract trades on' },
  { id: 'leg', label: SIGNAL_LEFT_COLUMNS.leg.label, width: SIGNAL_LEFT_COLUMNS.leg.width, align: 'right', hint: 'The contract, its moneyness and its delta' },
  { id: 'entry', label: SIGNAL_LEFT_COLUMNS.entry.label, width: SIGNAL_LEFT_COLUMNS.entry.width, align: 'right', hint: 'Price the position was taken at, and how far it has moved since' },
  { id: 'stop', label: SIGNAL_LEFT_COLUMNS.sl.label, width: SIGNAL_LEFT_COLUMNS.sl.width, align: 'right', hint: 'Hard stop set at entry — the original risk' },
  { id: 'trail', label: SIGNAL_LEFT_COLUMNS.tsl.label, width: SIGNAL_LEFT_COLUMNS.tsl.width, align: 'right', hint: 'Where the trailing stop has ratcheted to' },
  { id: 'target', label: SIGNAL_LEFT_COLUMNS.target.label, width: 58, align: 'right', hint: 'Where the plan gets out, for an engine that quotes one' },
  { id: 'exit', label: 'Exited', width: 58, align: 'right', hint: 'Where it actually got out, once it has' },
  // A counter, not a price. Kept apart from `exit` because they answer different
  // questions and sharing one column made SuperTrend's counter vanish behind a
  // realised price it does not have.
  { id: 'exitState', label: SIGNAL_LEFT_COLUMNS.exit.label, width: SIGNAL_LEFT_COLUMNS.exit.width, align: 'right', hint: 'Progress toward the rule that closes the position' },
  { id: 'qty', label: 'Qty', width: 52, align: 'right', hint: 'Units, not lots' },
  { id: 'risk', label: 'At risk', width: 70, align: 'right', hint: 'Rupees lost if the stop is honoured' },
  { id: 'score', label: 'Score', width: 44, align: 'right', hint: 'Engine conviction. Not comparable across engines' },
  { id: 'ltp', label: SIGNAL_RIGHT_COLUMNS.ltp.label, width: SIGNAL_RIGHT_COLUMNS.ltp.width, align: 'right', hint: 'Last traded price of the instrument' },
  { id: 'chg', label: SIGNAL_RIGHT_COLUMNS.chg.label, width: SIGNAL_RIGHT_COLUMNS.chg.width, align: 'right', hint: "Rupees the instrument has moved today" },
  { id: 'chgPct', label: SIGNAL_RIGHT_COLUMNS.chgPct.label, width: SIGNAL_RIGHT_COLUMNS.chgPct.width, align: 'right', hint: 'Percent the instrument has moved today' },
  { id: 'dir', label: SIGNAL_RIGHT_COLUMNS.dir.label || 'Direction', width: SIGNAL_RIGHT_COLUMNS.dir.width, align: 'right', hint: 'Which way today’s move is going' },
  { id: 'time', label: SIGNAL_RIGHT_COLUMNS.time.label, width: SIGNAL_RIGHT_COLUMNS.time.width, align: 'right', hint: 'When the signal fired. Marked stale when the quote behind it has aged out' },
  { id: 'trade', label: SIGNAL_RIGHT_COLUMNS.trade.label, width: SIGNAL_RIGHT_COLUMNS.trade.width, align: 'right', hint: 'Buy and Sell this contract. Switch the column off to put the order buttons away' },
  { id: 'chart', label: SIGNAL_RIGHT_COLUMNS.chart.label, width: SIGNAL_RIGHT_COLUMNS.chart.width, align: 'right', hint: "Open this instrument's chart" },
];

export interface SortState {
  column: ColumnId;
  direction: 'asc' | 'desc';
}

/** The board's default: most recent first, which is what a scan produces. */
export const DEFAULT_SORT: SortState = { column: 'time', direction: 'desc' };

/**
 * What each column sorts on.
 *
 * Returning a string sorts alphabetically, a number numerically, and null
 * always sinks to the bottom regardless of direction — a row with no stop is
 * not "the smallest stop", it is a row that has nothing to compare.
 */
function sortKey(signal: BoardSignal, column: ColumnId): string | number | null {
  switch (column) {
    case 'instrument': return signal.underlying;
    case 'engine': return signal.engine;
    case 'status': return STATUS_RANK[signal.status];
    case 'exchange': return signal.instrument.exchange;
    case 'leg': return signal.instrument.symbol;
    case 'ltp': return signal.levels.ltp;
    case 'entry': return signal.levels.entry;
    case 'stop': return signal.levels.stop;
    case 'trail': return signal.levels.trail;
    case 'target': return signal.levels.target;
    case 'exit': return signal.levels.exit;
    // Sorted as text: "0/3 red" has no numeric order worth inventing.
    case 'exitState': return signal.exitProgress ?? null;
    case 'qty': return signal.sizing.quantity;
    case 'risk': return signal.sizing.atRiskInr;
    case 'score': return signal.score;
    case 'time': return signal.atMs;
    case 'chg': return signal.dayMove?.abs ?? null;
    // Sorted by its OWN value, not by the absolute move: ordering a percent
    // column by rupees puts a 400-point index above a 3% stock move.
    case 'chgPct': return signal.dayMove?.pct ?? null;
    case 'dir': return signal.dayMove?.abs ?? null;
    default: return null;
  }
}

/**
 * Orders rows within one day.
 *
 * Sorting deliberately does NOT cross day boundaries: the day grouping is the
 * board's primary organisation, and a sort that reshuffled rows out of their
 * session would answer a question nobody asked. Sort by risk and you get the
 * largest risk of today, then the largest of yesterday.
 */
export function sortSignals(signals: readonly BoardSignal[], sort: SortState): BoardSignal[] {
  const factor = sort.direction === 'asc' ? 1 : -1;
  return [...signals].sort((a, b) => {
    const ka = sortKey(a, sort.column);
    const kb = sortKey(b, sort.column);
    // Missing values sink, both ways, so flipping direction never promotes a
    // row that has nothing to say to the top of the board.
    if (ka == null && kb == null) return 0;
    if (ka == null) return 1;
    if (kb == null) return -1;
    if (typeof ka === 'string' || typeof kb === 'string') {
      return String(ka).localeCompare(String(kb)) * factor;
    }
    return (ka - kb) * factor;
  });
}

/**
 * Clicking a column sorts it; clicking the same one again flips it.
 *
 * A new column starts descending for numbers and ascending for names, because
 * "biggest first" and "A first" are what each is usually reached for.
 */
export function nextSort(current: SortState, column: ColumnId): SortState {
  if (current.column === column) {
    return { column, direction: current.direction === 'asc' ? 'desc' : 'asc' };
  }
  const alphabetical = column === 'instrument' || column === 'leg' || column === 'exchange' || column === 'engine';
  return { column, direction: alphabetical ? 'asc' : 'desc' };
}

function SortMark({ direction }: { direction: 'asc' | 'desc' | null }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-flex', flexDirection: 'column', gap: 1, marginLeft: 3,
        verticalAlign: 'middle', opacity: direction ? 1 : 0, transition: 'opacity .12s ease',
      }}
    >
      <span style={{
        width: 0, height: 0, borderLeft: '3px solid transparent', borderRight: '3px solid transparent',
        borderBottom: `3px solid ${direction === 'asc' ? k.text : 'transparent'}`,
      }} />
      <span style={{
        width: 0, height: 0, borderLeft: '3px solid transparent', borderRight: '3px solid transparent',
        borderTop: `3px solid ${direction === 'desc' ? k.text : 'transparent'}`,
      }} />
    </span>
  );
}

/** Statuses that earn a coloured pill. The rest are the board's normal state. */
/** How far a leg sits in from the signal that owns it. */
/** @see LEG_INDENT - kept as a local alias so the call sites below read short. */
const INDENT = LEG_INDENT;

/**
 * Keeps every scrolling row and the header in step.
 *
 * Module scope, like SuperTrend's: each row is its own component instance, so a
 * per-instance handler would have nothing to sync against.
 */
const SB_HSCROLL = makeHscrollSync('.sb-head-row, .sb-row-scroll');

const NOTABLE_STATUS = new Set<BoardStatus>(['armed', 'weakening', 'error']);

/** Engine-accent names resolved to the theme's tokens. */
const ORIGIN_TONE: Record<NonNullable<BoardSignal['origin']>['tone'], string> = {
  brand: 'var(--k-brand)', blue: k.blue, green: k.green,
  purple: k.purple, amber: k.amber, dim: k.dim,
};

const STATUS_TONE: Record<BoardStatus, string> = {
  armed: k.blue,
  running: k.green,
  weakening: k.amber,
  ended: k.dim,
  watching: k.dim,
  error: k.red,
};

const num = (v: number | null | undefined, dp = 2) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

export const inr = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '—' : `₹${Math.round(v).toLocaleString('en-IN')}`;

/** Older than this and a quote is not describing the market any more. */
const STALE_AFTER_S = 15;

const hhmm = (ms: number | null) =>
  ms == null || !Number.isFinite(ms) ? '—'
    : new Date(ms).toLocaleTimeString('en-IN', {
      hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata',
    });

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"
      aria-hidden style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .14s ease', flexShrink: 0 }}>
      <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Pill({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} style={{
      fontSize: 8.5, fontWeight: 700, letterSpacing: '.04em', color: tone,
      background: tint(tone, 12), border: `1px solid ${tint(tone, 35)}`,
      borderRadius: 3, padding: '1px 4px', whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}

/**
 * What kind of contract the row is, for the pill beside the symbol.
 *
 * A parent row has no contract of its own, so it borrows its legs' type when
 * they agree and says nothing when they do not. It must not fall back to the
 * security kind: the parent of an LT signal was reading "LTINDEX · LONG",
 * and LT is a stock.
 */
/**
 * The engine's own inline badges.
 *
 * Same shape as the origin badge beside them, deliberately: an operator should
 * not have to learn two badge vocabularies on one row. The board reads a label,
 * a hint and a tone, and knows nothing about what any of them mean.
 */
function Marks({ marks }: { marks?: readonly BoardOrigin[] }) {
  if (!marks?.length) return null;
  return (
    <>
      {marks.map((m) => (
        <Tip key={`${m.label}-${m.tone}`} text={m.hint ? `${m.label} — ${m.hint}` : m.label}>
          <span style={{
            flexShrink: 0, padding: '0 4px', borderRadius: 3, fontSize: 8, fontWeight: 700,
            letterSpacing: '.04em', whiteSpace: 'nowrap',
            color: ORIGIN_TONE[m.tone], background: tint(ORIGIN_TONE[m.tone], 10),
            border: `1px solid ${tint(ORIGIN_TONE[m.tone], 30)}`,
          }}>
            {m.label}
          </span>
        </Tip>
      ))}
    </>
  );
}

function contractLabel(signal: BoardSignal): string | null {
  if (signal.instrument.optionType) return signal.instrument.optionType;
  const legs = signal.children ?? [];
  if (legs.length) {
    const types = new Set(legs.map((l) => l.instrument.optionType).filter(Boolean));
    return types.size === 1 ? [...types][0]! : null;
  }
  return signal.instrument.kind === 'option' ? null : signal.instrument.kind.toUpperCase();
}

/** The value of one column for one signal, plus how to colour it. */
function cellContent(
  signal: BoardSignal,
  id: ColumnId,
  onOpenDetail?: (signal: BoardSignal) => void,
  marks?: ReadonlySet<'bestRR' | 'bestDelta'>,
  isLeg = false,
  // Needed only by the time column, which words a row's date the same way the
  // day header would. Passed rather than read from the clock so a re-render at
  // midnight cannot disagree with the grouping — same reason sessionDayLabel
  // takes it.
  nowMs = Date.now(),
  /** Whether the operator wants today's move tinted green/red. */
  showDayColour = true,
): { node: React.ReactNode; color?: string } {
  const dirTone = signal.direction === 'long' ? k.green : k.red;
  switch (id) {
    case 'instrument': {
      // Inside a group the header already names the instrument, states where
      // the signal came from and how it is doing. A leg repeating all three
      // buries the one thing only it can say: which contract this is.
      if (isLeg) {
        return {
          node: (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', color: k.text }}>
                <InstrumentLabel symbol={signal.instrument.symbol} />
              </span>
              {marks?.has('bestRR') && (
                <Tip text="Best reward for risk across this signal's strikes — the most the plan pays per rupee it puts at stake.">
                  <span tabIndex={0} style={{ fontSize: 12, color: k.dim, lineHeight: 1, flexShrink: 0 }}>✝</span>
                </Tip>
              )}
              {marks?.has('bestDelta') && (
                <Tip text="Highest delta across this signal's strikes — the one that moves most with the underlying.">
                  <span tabIndex={0} style={{ fontSize: 11, color: k.dim, lineHeight: 1, opacity: .75, flexShrink: 0 }}>▲</span>
                </Tip>
              )}
              {trailBreached(signal) && (
                <Tip text="Live price is at or below this leg's trailing stop, but the engine has not closed it — this is where an open drawdown builds.">
                  <span tabIndex={0} style={{
                    fontSize: 8, fontWeight: 700, color: k.red, border: `1px solid ${k.red}`,
                    borderRadius: 2, padding: '0 3px', whiteSpace: 'nowrap', flexShrink: 0,
                  }}>
                    TSL HIT
                  </span>
                </Tip>
              )}
            </span>
          ),
        };
      }
      // A standalone row has no header above it, so it carries everything: the
      // contract it trades, which way, where it came from. Naming the
      // underlying instead would leave the traded contract unnamed anywhere.
      const standaloneName = signal.instrument.kind === 'option'
        ? <InstrumentLabel symbol={signal.instrument.symbol} />
        : signal.underlying;
      return {
        node: (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
            {onOpenDetail ? (
              // A real button, not a click handler on a span: the row itself
              // expands on click, so the label needs its own focus stop and its
              // own announced action or the two are indistinguishable.
              <button
                type="button"
                className="sb-name"
                title={`Open ${signal.underlying} detail`}
                aria-label={`Open ${signal.underlying} detail`}
                onClick={(e) => { e.stopPropagation(); onOpenDetail(signal); }}
                style={{
                  border: 'none', background: 'transparent', padding: 0, font: 'inherit',
                  fontWeight: 700, color: k.text, cursor: 'pointer',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
                }}
              >
                {standaloneName}
              </button>
            ) : (
              <span style={{ fontWeight: 700, color: k.text, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {standaloneName}
              </span>
            )}
            <Pill tone={dirTone} title={`${signal.direction} ${contractLabel(signal) ?? 'position'}`}>
              {[contractLabel(signal), signal.direction.toUpperCase()].filter(Boolean).join(' · ')}
            </Pill>
            {signal.origin && (
              <Tip text={`${signal.origin.label} — ${signal.origin.hint}`}>
                <span tabIndex={0} style={{
                  fontSize: 8, fontWeight: 700, letterSpacing: '.04em',
                  color: ORIGIN_TONE[signal.origin.tone], border: `1px solid ${tint(ORIGIN_TONE[signal.origin.tone], 34)}`,
                  borderRadius: 2, padding: '0 3px', whiteSpace: 'nowrap', outlineOffset: 2,
                }}>
                  {signal.origin.label}
                </span>
              </Tip>
            )}
            {/* Whatever else this engine wants read without opening the row. */}
            <Marks marks={signal.marks} />
            {signal.flags?.map((flag) => (
              <Tip key={flag.label} text={`${flag.label} — ${flag.hint}`}>
                <span tabIndex={0} style={{
                  fontSize: 8, fontWeight: 700, letterSpacing: '.04em',
                  color: ORIGIN_TONE[flag.tone], background: tint(ORIGIN_TONE[flag.tone], 10),
                  border: `1px solid ${tint(ORIGIN_TONE[flag.tone], 30)}`,
                  borderRadius: 2, padding: '0 3px', whiteSpace: 'nowrap', outlineOffset: 2,
                }}>
                  {flag.label}
                </span>
              </Tip>
            ))}
            {marks?.has('bestRR') && (
              <Tip text="Best reward for risk across this signal's strikes — the most the plan pays per rupee it puts at stake.">
                <span tabIndex={0} style={{ fontSize: 12, color: k.dim, lineHeight: 1 }}>✝</span>
              </Tip>
            )}
            {marks?.has('bestDelta') && (
              <Tip text="Highest delta across this signal's strikes — the one that moves most with the underlying.">
                <span tabIndex={0} style={{ fontSize: 11, color: k.dim, lineHeight: 1, opacity: .75 }}>▲</span>
              </Tip>
            )}
            {trailBreached(signal) && (
              <Tip text="Live price is at or below this leg's trailing stop, but the engine has not closed it — this is where an open drawdown builds.">
                <span tabIndex={0} style={{
                  fontSize: 8, fontWeight: 700, color: k.red, border: `1px solid ${k.red}`,
                  borderRadius: 2, padding: '0 3px', whiteSpace: 'nowrap', outlineOffset: 2,
                }}>
                  TSL HIT
                </span>
              </Tip>
            )}
          </span>
        ),
      };
    }
    case 'engine':
      return { node: <Pill tone={k.dim}>{ENGINE_TAG[signal.engine]}</Pill> };
    case 'status': {
      // The group header carries the signal's status; repeating it on every
      // contract says the same thing five times.
      if (isLeg) return { node: '', color: k.dim };
      // Armed, weakening and error are exceptions and get a pill. Running,
      // watching and ended are the normal state of a board and read as quiet
      // text — if every row is badged, the badge stops meaning anything.
      if (!NOTABLE_STATUS.has(signal.status)) {
        return { node: STATUS_LABEL[signal.status], color: k.dim };
      }
      return { node: <Pill tone={STATUS_TONE[signal.status]}>{STATUS_LABEL[signal.status]}</Pill> };
    }
    case 'exchange':
      return { node: signal.instrument.exchange || '—', color: k.dim };
    case 'leg': {
      // The full contract symbol, because that is the string a trader searches
      // for, reads back to a broker, and matches against a fill. Strike and
      // expiry are the same information pre-parsed, so they go in the tooltip
      // rather than competing for the width.
      // Where the strike sits and how hard it moves. Not the contract name —
      // the instrument cell to the left already carries that, and repeating it
      // costs the width the moneyness and delta need.
      // Never the contract name: the instrument cell carries that on a leg and
      // on a standalone row alike, and repeating it here printed it twice.
      const { strike, expiry, moneyness } = signal.instrument;
      const parts = [strike ?? null, expiry ?? null].filter(Boolean).join(' · ');
      const delta = signal.delta == null ? null : `(Δ${Math.abs(signal.delta).toFixed(2)})`;
      const text = [moneyness, delta].filter(Boolean).join(' ');
      return { node: <span title={parts || undefined}>{text || '—'}</span>, color: k.dim };
    }
    // The levels are plain ink. Colouring every stop red and every target green
    // was decoration, not information — the value was the same colour whatever
    // it said, and a board where a third of the numbers are permanently red has
    // nothing left to say when something is actually wrong. The column heading
    // already names which level it is.
    case 'ltp': return { node: num(signal.levels.ltp) };
    case 'entry': {
      // The bracket is the whole point of showing entry next to LTP: what the
      // position has actually done since it was taken.
      const { entry, ltp } = signal.levels;
      if (entry == null) return { node: '—' };
      const move = ltp == null ? null : ltp - entry;
      return {
        node: (
          <>
            {entry.toFixed(2)}
            {move != null && Math.abs(move) > 0.001 && (
              <span style={{ fontSize: 9.5, marginLeft: 3, fontWeight: 600, color: move >= 0 ? k.green : k.red }}>
                ({move >= 0 ? '+' : ''}{move.toFixed(2)})
              </span>
            )}
          </>
        ),
      };
    }
    case 'stop': return { node: num(signal.levels.stop) };
    case 'trail': return { node: num(signal.levels.trail) };
    case 'target': return { node: num(signal.levels.target) };
    case 'exit': return { node: num(signal.levels.exit) };
    case 'qty': return { node: signal.sizing.quantity ?? '—' };
    case 'risk': return { node: inr(signal.sizing.atRiskInr) };
    case 'score': return { node: signal.score == null ? '—' : signal.score.toFixed(0) };
    case 'time': {
      // A stale quote says so in words as well as in colour. Colour alone is
      // not a message on a board where several columns are already coloured by
      // direction, and it is no message at all to a colour-blind reader.
      const stale = signal.quoteAgeS != null && signal.quoteAgeS > STALE_AFTER_S;
      return {
        node: stale
          ? <span title={`Quote is ${Math.round(signal.quoteAgeS!)}s old`}>{stamp(signal.atMs, nowMs, isLeg)} · stale</span>
          : stamp(signal.atMs, nowMs, isLeg),
        color: stale ? k.red : k.dim,
      };
    }
    case 'exitState':
      // Plain ink: this is a state, and colouring it would compete with the
      // status pill that already carries how the row is doing.
      return { node: signal.exitProgress || '—', color: k.dim };
    case 'chg':
    case 'chgPct': {
      const move = signal.dayMove;
      const value = id === 'chg' ? move?.abs : move?.pct;
      if (value == null) return { node: '—' };
      const text = id === 'chg' ? value.toFixed(2) : `${value.toFixed(2)}%`;
      // Tinted only when the operator has direction colours on, and by the
      // ABSOLUTE move in both columns so the two never disagree about which way
      // the day has gone -- a rounded percent can read 0.00% on a real move.
      return { node: text, color: dayTone(move, showDayColour) };
    }
    case 'trade':
    case 'chart':
      // Rendered by the caller: what an action DOES is engine business, and this
      // function knows nothing about orders. Handled in `Row`, which has the
      // render props; reaching here at all means neither was supplied.
      return { node: null };
    case 'dir': {
      const abs = signal.dayMove?.abs;
      if (abs == null) return { node: '—' };
      // A flat instrument gets a mark of its own rather than an arrow pointing
      // nowhere or, worse, a green up-arrow for a zero move.
      const glyph = abs === 0 ? '∘' : abs > 0 ? '▲' : '▼';
      return { node: glyph, color: dayTone(signal.dayMove, showDayColour) };
    }
    default: return { node: '—' };
  }
}

/**
 * Green or red by today's move, or nothing when the operator has switched the
 * direction colours off.
 *
 * The preference is PASSED IN, not read from the store here. Calling
 * `useKiteSettings.getState()` mid-render creates no subscription, so toggling
 * the setting would change nothing until something else happened to repaint the
 * board — the same silent-toggle bug this very setting had on SuperTrend's
 * table. `Row` subscribes properly and hands the value down.
 */
function dayTone(move: BoardDayMove | null | undefined, on: boolean): string | undefined {
  if (!on) return undefined;
  if (move?.abs == null || move.abs === 0) return undefined;
  return move.abs > 0 ? k.green : k.red;
}

/**
 * Which columns to show.
 *
 * A column survives if at least one signal can fill it — a board of equities
 * should not carry an empty Leg column, and an engine that quotes no target
 * should not imply it forgot to. `always` columns are the row's identity and
 * stay even when sparse.
 */
/** True when more than one engine is on the board, so the Engine tag earns its width. */
export const isMixedEngine = (signals: readonly BoardSignal[]) =>
  new Set(flattenSignals(signals).map((s) => s.engine)).size > 1;

/**
 * The column set every board shows, in one place.
 *
 * All four engines request this same list, so switching tabs never moves a
 * column or renames one. What differs between engines is what they can fill,
 * and an empty cell says "this engine does not produce that" — ORB has no
 * trailing stop, so its TSL column reads as dashes, which is true and useful.
 *
 * That is a deliberate reversal. The board used to drop any column no row
 * could fill, which meant every board showed a different set and moving
 * between them meant re-finding the stop.
 */
export const BOARD_COLUMNS: readonly ColumnId[] = [
  'instrument', 'engine', 'status', 'exchange', 'leg',
  'ltp', 'entry', 'stop', 'trail', 'target', 'exit',
  'qty', 'risk', 'score', 'time',
  // Buy/Sell and the chart, on every board rather than only the one whose
  // bespoke table happened to have them. `useBoardRowActions` builds both from a
  // `BoardSignal` alone, so no engine needs its own order plumbing — and the
  // picker can switch either off, because a board is read far more often than it
  // is traded from.
  'trade', 'chart',
];

/**
 * The same list plus today's move.
 *
 * `chg`, `chgPct` and `dir` are deliberately NOT in `BOARD_COLUMNS`. The rule
 * above — every engine requests the same list, and an unfillable cell honestly
 * reads as a dash — holds when the engine simply produces no such number. It
 * does not hold here: today's move needs a live quote, and SuperTrend's adapter
 * is the only one that is handed `quotes` at all. Adding them to the shared list
 * would give four boards three columns that can never be anything but dashes,
 * which is not "this engine does not produce that", it is dead width.
 *
 * So the engine that has the data asks for them. If another adapter is ever
 * given quotes, it can ask for them too — or they graduate into the shared list.
 */
export const BOARD_COLUMNS_WITH_DAY_MOVE: readonly ColumnId[] = [
  ...BOARD_COLUMNS, 'chg', 'chgPct', 'dir',
];

/**
 * Off unless asked for, on every board.
 *
 * Leaves the eleven a trader named as the core of a row — symbol, type,
 * exchange, leg, LTP, entry, SL, TSL, exit, time, status — and keeps the rest
 * one click away in the column picker rather than making the sidebar scroll.
 */
export const DEFAULT_HIDDEN_COLUMNS: readonly ColumnId[] = ['engine', 'qty', 'risk', 'score'];

/**
 * Which columns to render.
 *
 * Only two things remove a column: the caller did not ask for it, or the user
 * switched it off. Emptiness no longer does — see BOARD_COLUMNS.
 *
 * The engine tag is the one exception, because on a single-engine board it
 * would repeat the same three letters down every row.
 */
export function visibleColumns(signals: readonly BoardSignal[], requested: readonly ColumnId[]): ColumnDef[] {
  const wanted = new Set(requested);
  if (!isMixedEngine(signals)) wanted.delete('engine');
  return COLUMNS.filter((c) => wanted.has(c.id));
}

/**
 * A signal, as SuperTrend draws one.
 *
 * Deliberately NOT a columned row. A signal is an idea about an instrument —
 * it has no premium, no strike, no stop of its own — so rendering it in the
 * legs' columns produced a line of empty cells pretending to be data. What it
 * does have goes on two sides: what it is on the left, what you should know
 * about it on the right.
 */
function GroupHeader({ signal, legCount, expanded, onToggle, onOpenDetail, nowMs }: {
  signal: BoardSignal;
  legCount: number;
  expanded: boolean;
  onToggle: () => void;
  onOpenDetail?: (signal: BoardSignal) => void;
  /** Passed in, not read from the clock, so the relative age is deterministic. */
  nowMs: number;
}) {
  const dirTone = signal.direction === 'long' ? k.green : k.red;
  const statusTone = STATUS_TONE[signal.status];
  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      aria-label={`${signal.underlying} ${signal.direction}, ${legCount} contract${legCount === 1 ? '' : 's'}, ${STATUS_LABEL[signal.status]}`}
      onClick={onToggle}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
      className="sb-row sb-parent"
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
        padding: ROW_METRICS.parentPadding,
        borderBottom: `1px solid ${k.border}`,
        borderLeft: '3px solid transparent',
        background: k.bg, cursor: 'pointer', outlineOffset: -2,
      }}
    >
      {/* The parent's own columns.
          Laid out inline, every field sat at a different x on every row: one
          row's price began under the next row's name, and the badges landed
          wherever the count happened to end. Fixed tracks make each a column.
          (An earlier pass at reordering these also left the badges nested INSIDE
          the count's span, which is why they rendered jammed against it.) */}
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
        <span style={{ color: k.dim, display: 'inline-flex', flexShrink: 0 }}><Chevron open={expanded} /></span>

        {/* A FIXED track, not a growing one. `instrumentFlex()` grows, which
            pushed the price, the count and the badges to the far right of the
            row and left a lake of white space after the name. Fixed, they sit
            beside the label they describe — and at the same x on every row. */}
        <span style={{
          flex: `0 0 ${ROW_METRICS.instrumentMinWidth}px`,
          minWidth: 0, overflow: 'hidden',
        }}>
          {onOpenDetail ? (
            <button
              type="button"
              className="sb-name"
              aria-label={`Open ${signal.underlying} detail`}
              onClick={(e) => { e.stopPropagation(); onOpenDetail(signal); }}
              style={{
                border: 'none', background: 'transparent', padding: 0, font: 'inherit',
                fontSize: ROW_METRICS.parentFontSize, fontWeight: 600, color: dirTone,
                cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden',
                textOverflow: 'ellipsis', maxWidth: '100%',
              }}
            >
              {signal.underlying}
            </button>
          ) : (
            <span style={{
              fontSize: ROW_METRICS.parentFontSize, fontWeight: 600, color: dirTone,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'block',
            }}>
              {signal.underlying}
            </span>
          )}
        </span>

        {/* Right-aligned and tabular so the decimal points line up down the
            column, the same rule the leg cells follow. */}
        <span style={{
          width: PARENT_METRICS.priceWidth, flexShrink: 0, textAlign: 'right',
          fontSize: 11, color: dirTone, fontVariantNumeric: 'tabular-nums',
        }}>
          {signal.underlyingPrice != null
            ? signal.underlyingPrice.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : ''}
        </span>

        <span style={{
          width: PARENT_METRICS.countWidth, flexShrink: 0,
          fontSize: 10, color: k.dim, whiteSpace: 'nowrap',
        }}>
          {legCount} contract{legCount === 1 ? '' : 's'}
        </span>

        {/* A real track for the badges, so they start at the same x on every
            row instead of trailing whatever came before them. */}
        <span style={{
          width: PARENT_METRICS.badgeWidth, flexShrink: 0,
          display: 'flex', alignItems: 'center', gap: 4, overflow: 'hidden',
        }}>
          {signal.origin && (
            <Tip text={`${signal.origin.label} — ${signal.origin.hint}`}>
              <span tabIndex={0} style={{
                fontSize: 8, fontWeight: 700, letterSpacing: '.04em', flexShrink: 0,
                color: ORIGIN_TONE[signal.origin.tone], border: `1px solid ${tint(ORIGIN_TONE[signal.origin.tone], 34)}`,
                borderRadius: 2, padding: '0 3px', whiteSpace: 'nowrap', outlineOffset: 2,
              }}>
                {signal.origin.label}
              </span>
            </Tip>
          )}
          <Marks marks={signal.marks} />
        </span>

        {/* When it fired, and how long ago.
            The absolute stamp is what you quote when reconciling against the
            broker's log; the relative one is what you read while trading, since
            "17 min ago" answers "is this still worth acting on" and a wall-clock
            time does not at a glance. */}
        {(() => {
          const at = parentStamp(signal.atMs, nowMs);
          if (!at) return null;
          return (
            <span style={{
              display: 'inline-flex', alignItems: 'baseline', gap: 6, flexShrink: 0,
              fontSize: 10, color: k.dim, whiteSpace: 'nowrap',
              fontVariantNumeric: 'tabular-nums',
            }}>
              <span>{at.absolute}</span>
              {at.relative && <span style={{ opacity: 0.8 }}>{at.relative}</span>}
            </span>
          );
        })()}

        {/* Absorbs the remaining width, so everything above keeps its own track
            instead of being stretched across the row. */}
        <span style={{ flex: 1, minWidth: 0 }} />
      </span>

      <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {signal.flags?.map((flag) => (
          <Tip key={flag.label} text={`${flag.label} — ${flag.hint}`}>
            <span tabIndex={0} style={{
              fontSize: 9, fontWeight: 700, color: ORIGIN_TONE[flag.tone],
              background: tint(ORIGIN_TONE[flag.tone], 10), border: `1px solid ${tint(ORIGIN_TONE[flag.tone], 30)}`,
              borderRadius: 3, padding: '1px 4px', whiteSpace: 'nowrap', outlineOffset: 2,
            }}>
              {flag.label}
            </span>
          </Tip>
        ))}
        {NOTABLE_STATUS.has(signal.status) && (
          <span style={{
            fontSize: 9, fontWeight: 700, color: statusTone, background: tint(statusTone, 12),
            border: `1px solid ${tint(statusTone, 35)}`, borderRadius: 3, padding: '1px 4px', whiteSpace: 'nowrap',
          }}>
            {STATUS_LABEL[signal.status]}
          </span>
        )}
      </span>
    </div>
  );
}

function Row({
  signal, columns, open, onToggle, renderDetail, onOpenDetail, striped,
  depth = 0, legCount, marks, nowMs, rowScroll = false, renderRowActions,
  renderTrade, renderChart,
}: {
  signal: BoardSignal;
  columns: ColumnDef[];
  open: boolean;
  onToggle: () => void;
  renderDetail?: (signal: BoardSignal) => React.ReactNode;
  onOpenDetail?: (signal: BoardSignal) => void;
  /** Alternating row shade, which is how rows separate without hard borders. */
  striped: boolean;
  /** 1 for a leg sitting under its signal. Indents and quietens the row. */
  depth?: number;
  /** Set on a parent: how many legs it holds, for the label and the summary. */
  legCount?: number;
  /** Which of its siblings' comparisons this leg wins. */
  marks?: ReadonlySet<'bestRR' | 'bestDelta'>;
  /** The board's clock, so the time column can word a date like its day header. */
  nowMs: number;
  /** Scroll sideways rather than clip, offsets shared with every other row. */
  rowScroll?: boolean;
  /** Controls belonging to this row, rendered before the right-hand cells. */
  renderRowActions?: (signal: BoardSignal) => React.ReactNode;
  /** Buy/Sell for this row, as the `trade` column. */
  renderTrade?: (signal: BoardSignal) => React.ReactNode;
  /** The chart button, as the `chart` column. */
  renderChart?: (signal: BoardSignal) => React.ReactNode;
}) {
  const isLeg = depth > 0;
  const isParent = legCount != null;
  // A real subscription, so flipping the preference repaints the rows.
  const showDayColour = useKiteSettings((s) => s.showPriceDirection);
  return (
    <>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={
          isParent
            ? `${signal.underlying} ${signal.direction}, ${legCount} contract${legCount === 1 ? '' : 's'}, ${STATUS_LABEL[signal.status]}`
            : `${signal.underlying} ${signal.instrument.optionType ?? ''} ${STATUS_LABEL[signal.status]}`
        }
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
        className={rowScroll ? 'sb-row sb-row-scroll' : 'sb-row'}
        onScroll={rowScroll ? SB_HSCROLL : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: ROW_METRICS.gap,
          // A leg is indented under the idea it belongs to; the indent is the
          // only thing saying "this is part of that", so it has to survive
          // scrolling past the parent.
          padding: isLeg ? `0 16px 0 ${16 + INDENT}px` : ROW_METRICS.legPadding,
          minHeight: ROW_METRICS.legHeight,
          cursor: 'pointer',
          outlineOffset: -2,
          borderBottom: `1px solid ${isLeg ? 'transparent' : k.border}`,
          // The left accent marks the OPEN row only. It used to carry the
          // direction on every row, which put a saturated band down the whole
          // board and left nothing to mark the row you had actually opened —
          // direction is already stated by the pill beside the symbol.
          borderLeft: `3px solid ${open ? k.blue : 'transparent'}`,
          // Alternating shade separates rows the way the old Adaptive Edge
          // table did, without a coloured edge on each one. Legs share one
          // shade so a group reads as a block rather than a stripe pattern.
          background: open ? k.surfaceHover : isLeg ? LEG_BG : striped ? LEG_BG : k.bg,
          fontWeight: isParent ? 600 : 400,
          // An ended row is a record, not a live position. Dimming and striking
          // it keeps it readable without letting it read as actionable.
          opacity: signal.status === 'ended' ? 0.62 : 1,
          textDecoration: signal.status === 'ended' ? 'line-through' : 'none',
          // Off, the row clips and the operator hides columns to fit -- which is
          // what this board has always done. The scrollbar itself is hidden in
          // CSS; a bar under every row would out-shout the data.
          overflowX: rowScroll ? 'auto' : 'hidden',
          overflowY: 'hidden',
        }}
      >
        {/* Fixed gutter. The header reserves the same, so the first column starts
            at the same x in both. */}
        <span style={{
          color: k.dim, display: 'inline-flex', alignItems: 'center', gap: 3,
          flexShrink: 0, width: EDGE_METRICS.chevronWidth,
        }}>
          <Chevron open={open} />
          {isParent && (
            <span
              title={`${legCount} contract${legCount === 1 ? '' : 's'}`}
              style={{ fontSize: 8.5, fontWeight: 700, color: k.dim, fontVariantNumeric: 'tabular-nums' }}
            >
              {legCount}
            </span>
          )}
        </span>
        {columns.map((col) => {
          // The two action columns come from render props rather than from
          // `cellContent`: what Buy does is engine business, and that function
          // deliberately knows nothing about orders. They still travel through
          // the column grid, so hiding them works exactly like hiding any other.
          const action = col.id === 'trade' ? renderTrade?.(signal)
            : col.id === 'chart' ? renderChart?.(signal)
            : undefined;
          const { node, color } = action !== undefined && action !== null
            ? { node: action, color: undefined }
            : cellContent(signal, col.id, col.id === 'instrument' ? onOpenDetail : undefined, marks, isLeg, nowMs, showDayColour);
          const isName = col.id === 'instrument';
          return (
            <span
              key={col.id}
              // Which column this cell IS, so a test can ask for "the trail cell"
              // without knowing which renderer drew it. The bespoke table names
              // the same two cells the same way; a test that queries this passes
              // on either, which is the only way parity between two renderers can
              // actually be asserted rather than assumed.
              data-cell={col.id}
              style={{
                // The name is the only cell that flexes; every other column is
                // a fixed width so the decimal points line up down the board.
                // The basis carries the indent compensation, not minWidth: the
                // cell no longer shrinks, so a minimum bounds nothing.
                flex: isName ? instrumentFlex(isLeg) : `0 0 ${col.width}px`,
                minWidth: 0,
                width: isName ? undefined : col.width,
                fontSize: isName ? ROW_METRICS.instrumentFontSize : ROW_METRICS.cellFontSize,
                color: color ?? k.text,
                textAlign: col.align,
                fontVariantNumeric: 'tabular-nums',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {node}
            </span>
          );
        })}
        {/* The engine's own controls. After the cells so they sit at the end of
            the row, and outside the cell map so they are not mistaken for a
            column and cannot be hidden by the column picker. */}
        {renderRowActions && (
          <span
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end',
              gap: 8, flexShrink: 0, width: EDGE_METRICS.actionsWidth,
            }}
            // The row is a button; a control inside it must not also toggle it.
            onClick={(e) => e.stopPropagation()}
          >
            {renderRowActions(signal)}
          </span>
        )}
      </div>
      {open && (
        <div style={{ padding: 10, background: k.surface, borderBottom: `2px solid ${k.border}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {renderDetail?.(signal)}
          {signal.sections.length > 0 && (
            <StatCardGrid min={236}>
              {signal.sections.map((section) => (
                <StatCard
                  key={section.title}
                  title={section.title}
                  summary={section.summary}
                  layout={section.layout ?? 'tiles'}
                  stats={section.stats}
                  dense
                />
              ))}
            </StatCardGrid>
          )}
          {signal.reason && (
            <p style={{ margin: 0, fontSize: 10.5, color: k.dim, lineHeight: 1.55 }}>{signal.reason}</p>
          )}
        </div>
      )}
    </>
  );
}

export function SignalBoard({
  signals, columns: requested, openId, onToggle, renderDetail, onOpenDetail, nowMs, emptyLabel,
  sort = DEFAULT_SORT, onSortChange, hidden, collapsedGroups, onToggleGroup,
  onReorderColumn, rowScroll = false, renderRowActions,
  renderTrade, renderChart, collapseOlderDays = false, isHistoricalSim = false,
  columnLabels, columnHints,
}: {
  signals: readonly BoardSignal[];
  /**
   * Which columns this board asks for, in `COLUMNS` order.
   *
   * There used to be a `requested?` here as well — the name this function
   * destructures `columns` INTO. It was declared, never read, and four of the
   * five boards passed it. They got away with it because they each passed
   * exactly the default the fallback supplies, so the board looked correct while
   * the prop did nothing; a per-engine list passed that way would have vanished
   * silently, and six grouping tests were asking for two columns and rendering
   * sixteen. Deleted so the compiler answers the question instead.
   */
  columns?: readonly ColumnId[];
  openId: string | null;
  onToggle: (id: string) => void;
  renderDetail?: (signal: BoardSignal) => React.ReactNode;
  /** Opens the full detail page. Makes the instrument label a control. */
  onOpenDetail?: (signal: BoardSignal) => void;
  /** Column ordering, applied within each day. */
  sort?: SortState;
  /** Omit to make the header static — the board is then unsortable. */
  onSortChange?: (next: SortState) => void;
  /** Columns the user switched off. */
  hidden?: ReadonlySet<ColumnId>;
  /**
   * Signals whose contracts are FOLDED AWAY. Separate from `openId`, which is
   * a row's own detail.
   *
   * Expressed as collapsed rather than open so the default is legs visible.
   * A board exists to show tradable contracts; making every one of them cost a
   * click, on the board whose job is to show them, is worse than the repetition
   * grouping was introduced to fix — the parent row and the indent already give
   * the structure.
   */
  collapsedGroups?: ReadonlySet<string>;
  onToggleGroup?: (id: string) => void;
  /**
   * Let a heading be dragged sideways to move its column.
   *
   * Omit and the headings are sort controls only, which is what every engine but
   * SuperTrend has had. Supplying it is what allows SuperTrend's table to move
   * onto this component without losing the feature — and, having moved it here,
   * any engine can offer it.
   */
  onReorderColumn?: (fromId: ColumnId, toId: ColumnId) => void;
  /**
   * Let each row scroll sideways on its own when the board is narrower than its
   * columns, with the offsets kept in step.
   *
   * Off by default. The alternative, and the shared board's habit until now, is
   * to hide columns until the rest fit — which keeps every row aligned but makes
   * the operator choose what to stop seeing.
   */
  rowScroll?: boolean;
  /**
   * Controls that belong to a row: buy, chart, whatever the engine offers.
   *
   * Rendered inside the row, before the right-hand cells. Kept as a render prop
   * rather than a set of callbacks because what a row can do differs per engine,
   * and this component has no business knowing what an order is.
   */
  renderRowActions?: (signal: BoardSignal) => React.ReactNode;
  /**
   * Buy/Sell and the chart, as the `trade` and `chart` COLUMNS.
   *
   * Columns rather than a pinned strip, so the picker can switch them off — a
   * board is read far more often than it is traded from. `useBoardRowActions`
   * builds both from a `BoardSignal` alone, so every engine can pass them
   * without growing its own order plumbing.
   */
  renderTrade?: (signal: BoardSignal) => React.ReactNode;
  renderChart?: (signal: BoardSignal) => React.ReactNode;
  /** Passed in so day labels are deterministic and testable. */
  nowMs?: number;
  isHistoricalSim?: boolean;
  emptyLabel?: string;
  collapseOlderDays?: boolean;
  /**
   * Per-engine heading overrides. SuperTrend keeps "Entry (Δpts)"; ORB is a
   * bought option, so it names the same column "Entry ₹". Switching tabs
   * renaming a column is the cost of not lying about the unit.
   */
  columnLabels?: Partial<Record<ColumnId, string>>;
  columnHints?: Partial<Record<ColumnId, string>>;
}) {
  const wanted = requested ?? BOARD_COLUMNS;
  const chosen = hidden ? wanted.filter((c) => !hidden.has(c)) : wanted;
  const all = visibleColumns(signals, chosen);

  // Measured, not derived from the window: this board is one dock among
  // several and its width has little to do with the viewport's. offsetWidth
  // rather than getBoundingClientRect because column widths are layout pixels
  // and a rect reports device pixels — the two diverge under a viewport scale.
  const boardRef = React.useRef<HTMLDivElement>(null);
  const [boardWidth, setBoardWidth] = React.useState(0);
  React.useLayoutEffect(() => {
    const el = boardRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measure = () => setBoardWidth(el.offsetWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { columns: fitted } = fitColumns(all, boardWidth, {
    minInstrument: ROW_METRICS.instrumentMinWidth,
    gap: ROW_METRICS.gap,
    reserve: ACTION_RESERVE,
  });
  const cols = fitted.map((c) => ({
    ...c,
    label: columnLabels?.[c.id] ?? c.label,
    hint: columnHints?.[c.id] ?? c.hint,
  }));

  const [userToggledDays, setUserToggledDays] = React.useState<Map<string, boolean>>(new Map());

  const effectiveNowMs = nowMs ?? Date.now();

  const days = groupByDay(signals, { nowMs: effectiveNowMs });

  const isDayExpanded = (key: string): boolean => {
    if (userToggledDays.has(key)) {
      return userToggledDays.get(key)!;
    }
    // One shared rule for every engine's board — see isDayExpandedByDefault.
    // This used to open Today and Yesterday together, plus the first group when
    // it held an actionable row, which left most of a week's history expanded.
    return isDayExpandedByDefault(key, days.map((d) => d.key));
  };

  const toggleDay = (key: string) => {
    setUserToggledDays((prev) => {
      const next = new Map(prev);
      next.set(key, !isDayExpanded(key));
      return next;
    });
  };

  if (!signals.length) {
    return <p style={{ padding: '14px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6 }}>{emptyLabel ?? 'Nothing to show.'}</p>;
  }

  return (
    <div ref={boardRef}>
      <div
        role="row"
        className="sb-head-row"
        onScroll={rowScroll ? SB_HSCROLL : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: ROW_METRICS.gap,
          overflowX: rowScroll ? 'auto' : undefined,
          overflowY: rowScroll ? 'hidden' : undefined,
          padding: HEAD_METRICS.padding,
          borderBottom: `1px solid ${k.border}`,
          borderLeft: '3px solid transparent',
          position: 'sticky',
          top: 0,
          zIndex: 2,
          background: k.bg,
        }}
      >
        {/* The chevron gutter, reserved so the first heading starts where the
            first cell does. This was a zero-width `<span />`. */}
        <span style={{ flexShrink: 0, width: EDGE_METRICS.chevronWidth }} />
        {cols.map((col) => {
          const active = sort.column === col.id;
          const direction = active ? sort.direction : null;
          const heading = (
            <Tip
              key={col.id}
              text={col.hint ? `${col.label} — ${col.hint}. Click to sort within each day.` : `${col.label} — click to sort within each day.`}
            >
              <button
                type="button"
                className="sb-head"
                aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                onClick={() => onSortChange?.(nextSort(sort, col.id))}
                disabled={!onSortChange}
                style={{
                  border: 'none', background: 'transparent', padding: 0, font: 'inherit',
                  flex: col.id === 'instrument' ? instrumentFlex() : `0 0 ${col.width}px`,
                  width: col.id === 'instrument' ? undefined : col.width,
                  minWidth: col.id === 'instrument' ? ROW_METRICS.instrumentMinWidth : 0,
                  fontSize: HEAD_METRICS.fontSize,
                  fontWeight: HEAD_METRICS.fontWeight,
                  letterSpacing: HEAD_METRICS.letterSpacing,
                  textTransform: HEAD_METRICS.textTransform,
                  color: active ? k.text : k.dim,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  cursor: onSortChange ? 'pointer' : 'default',
                  outlineOffset: 2,
                  display: 'flex', alignItems: 'center',
                  justifyContent: col.align === 'right' ? 'flex-end' : 'flex-start',
                }}
              >
                {col.label}
                <SortMark direction={direction} />
              </button>
            </Tip>
          );
          if (!onReorderColumn) return heading;
          return (
            <DraggableColHeader
              key={col.id}
              colKey={col.id}
              group="board"
              width={col.width}
              flex={col.id === 'instrument' ? instrumentFlex() : undefined}
              minWidth={col.id === 'instrument' ? ROW_METRICS.instrumentMinWidth : undefined}
              reorder={(_group, fromKey, toKey) => onReorderColumn(fromKey as ColumnId, toKey as ColumnId)}
            >
              {heading}
            </DraggableColHeader>
          );
        })}

        {renderRowActions && (
          <span style={{ flexShrink: 0, width: EDGE_METRICS.actionsWidth }} />
        )}
      </div>

      {days.map(({ key, signals: rows }) => {
        const sorted = sortSignals(rows, sort);
        const weeklySignals = sorted.filter(isWeeklySignal);
        const monthlySignals = sorted.filter((s) => !isWeeklySignal(s));
        const hasBoth = weeklySignals.length > 0 && monthlySignals.length > 0;
        const expandedDay = isDayExpanded(key);
        // Its own dated band means every row in it is running; today and
        // yesterday hold a mix, so they are active when any row still is.
        const dayIsActive = isLiveDayKey(key)
          || rows.some((s) => s.status === 'running' || s.status === 'weakening');
        const isRecentGroup =
          key === sessionDayKey(effectiveNowMs) ||
          sessionDayLabel(key, effectiveNowMs, isHistoricalSim) === 'Today';

        const renderSignalGroup = (groupRows: BoardSignal[]) => {
          return groupRows.map((signal, i) => {
            const legs = signal.children ?? [];
            if (!legs.length) {
              const resolvedNothing = Array.isArray(signal.children) && signal.children.length === 0;
              return (
                <React.Fragment key={signal.id}>
                  <Row
                    nowMs={effectiveNowMs}
                    key={signal.id}
                    signal={signal}
                    columns={cols}
                    open={openId === signal.id}
                    onToggle={() => onToggle(signal.id)}
                    renderDetail={renderDetail}
                    onOpenDetail={onOpenDetail}
                    striped={i % 2 === 1}
                    rowScroll={rowScroll}
                    renderRowActions={renderRowActions}
                    renderTrade={renderTrade}
                    renderChart={renderChart}
                  />
                  {resolvedNothing && (
                    <div
                      style={{
                        display: 'flex', alignItems: 'center',
                        minHeight: ROW_METRICS.legHeight,
                        padding: `0 16px 0 ${16 + INDENT}px`,
                        borderLeft: '3px solid transparent',
                        background: LEG_BG,
                        fontSize: ROW_METRICS.cellFontSize, color: k.dim,
                      }}
                    >
                      {signal.reason ?? 'No listed contract matched the selected strike and expiry series.'}
                    </div>
                  )}
                </React.Fragment>
              );
            }
            // For Today and Yesterday: default expanded unless in collapsedGroups.
            // For older days: default collapsed unless in collapsedGroups as false.
            const expanded = isRecentGroup
              ? !(collapsedGroups?.has(signal.id) ?? false)
              : (collapsedGroups?.has(signal.id) === false);
            const legMarks = markLegs(legs);
            const sortedLegs = sortSignals(legs, sort);
            const weeklyLegs = sortedLegs.filter(isWeeklySignal);
            const monthlyLegs = sortedLegs.filter((l) => !isWeeklySignal(l));
            const hasBothLegs = weeklyLegs.length > 0 && monthlyLegs.length > 0;

            return (
              <React.Fragment key={signal.id}>
                <GroupHeader
                  signal={signal}
                  legCount={legs.length}
                  expanded={expanded}
                  onToggle={() => onToggleGroup?.(signal.id)}
                  onOpenDetail={onOpenDetail}
                  nowMs={effectiveNowMs}
                />
                {expanded && !hasBothLegs && sortedLegs.map((leg) => (
                  <Row
                    nowMs={effectiveNowMs}
                    key={leg.id}
                    marks={legMarks.get(leg.id)}
                    signal={leg}
                    columns={cols}
                    open={openId === leg.id}
                    onToggle={() => onToggle(leg.id)}
                    renderDetail={renderDetail}
                    onOpenDetail={onOpenDetail}
                    striped={false}
                    depth={1}
                    rowScroll={rowScroll}
                    renderRowActions={renderRowActions}
                    renderTrade={renderTrade}
                    renderChart={renderChart}
                  />
                ))}
                {expanded && hasBothLegs && (
                  <React.Fragment>
                    {weeklyLegs.map((leg) => (
                      <Row
                        nowMs={effectiveNowMs}
                        key={leg.id}
                        marks={legMarks.get(leg.id)}
                        signal={leg}
                        columns={cols}
                        open={openId === leg.id}
                        onToggle={() => onToggle(leg.id)}
                        renderDetail={renderDetail}
                        onOpenDetail={onOpenDetail}
                        striped={false}
                        depth={1}
                        rowScroll={rowScroll}
                        renderRowActions={renderRowActions}
                        renderTrade={renderTrade}
                        renderChart={renderChart}
                      />
                    ))}
                    <div style={{ height: 6, background: 'transparent' }} />
                    {monthlyLegs.map((leg) => (
                      <Row
                        nowMs={effectiveNowMs}
                        key={leg.id}
                        marks={legMarks.get(leg.id)}
                        signal={leg}
                        columns={cols}
                        open={openId === leg.id}
                        onToggle={() => onToggle(leg.id)}
                        renderDetail={renderDetail}
                        onOpenDetail={onOpenDetail}
                        striped={false}
                        depth={1}
                        rowScroll={rowScroll}
                        renderRowActions={renderRowActions}
                        renderTrade={renderTrade}
                        renderChart={renderChart}
                      />
                    ))}
                  </React.Fragment>
                )}
              </React.Fragment>
            );
          });
        };

        return (
          <section key={key}>
            <div
              className="sb-day"
              onClick={() => toggleDay(key)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggleDay(key);
                }
              }}
              aria-expanded={expandedDay}
              style={{
                position: 'sticky',
                top: 0,
                zIndex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                padding: DAY_HEAD_METRICS.padding,
                background: k.surface,
                borderBottom: `1px solid ${k.border}`,
                fontSize: DAY_HEAD_METRICS.fontSize,
                fontWeight: DAY_HEAD_METRICS.fontWeight,
                letterSpacing: DAY_HEAD_METRICS.letterSpacing,
                textTransform: DAY_HEAD_METRICS.textTransform,
                // A band holding a running position keeps its green, as
                // SuperTrend's does: that is real state, not decoration, and the
                // dot beside it would otherwise be the only sign of it.
                color: dayIsActive ? k.green : k.dim,
                cursor: 'pointer',
                userSelect: 'none',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    transform: expandedDay ? 'rotate(0deg)' : 'rotate(-90deg)',
                    transition: 'transform 0.15s ease',
                    color: k.dim,
                    flexShrink: 0,
                  }}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
                {dayIsActive && (
                  <span style={{ width: 7, height: 7, borderRadius: 4, background: k.green, flexShrink: 0 }} />
                )}
                <span>{sessionDayLabel(key, effectiveNowMs, isHistoricalSim)}</span>
              </div>
              <span style={{ fontWeight: 500, color: k.dim }}>
                {rows.length} {rows.length === 1 ? 'signal' : 'signals'}
              </span>
            </div>

            {expandedDay && (
              !hasBoth ? (
                renderSignalGroup(sorted)
              ) : (
                <React.Fragment>
                  {/* WEEKLY SIGNALS */}
                  {renderSignalGroup(weeklySignals)}

                  {/* SMALL TRANSPARENT SPACE / LINE SEPARATOR BETWEEN WEEKLY AND MONTHLY SIGNALS */}
                  <div
                    style={{
                      height: 10,
                      background: 'transparent',
                      borderTop: `1px solid ${k.border}`,
                    }}
                  />

                  {/* MONTHLY SIGNALS */}
                  {renderSignalGroup(monthlySignals)}
                </React.Fragment>
              )
            )}
          </section>
        );
      })}
    </div>
  );
}

export type { EngineId };
