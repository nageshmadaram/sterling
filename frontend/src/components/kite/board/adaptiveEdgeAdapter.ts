/**
 * Adaptive Edge rows -> BoardSignal.
 *
 * Adaptive Edge is the only engine that reasons in two price frames at once: it
 * forms a thesis on the underlying (spot entry, spot stop, POC, session VWAP,
 * cumulative delta) and then expresses it through an option. Both frames are
 * real and a trader needs both, so the option ladder fills the board's columns
 * — those are the prices an order is placed at — and the spot frame becomes its
 * own detail section rather than being flattened into the same columns.
 *
 * That split is the whole reason `sections` exists on BoardSignal. Forcing spot
 * levels into the shared SL column would put two different instruments' prices
 * under one heading.
 */
import type { AdaptiveEdgeRow } from '../AdaptiveEdgePanel';
import type { BoardOrigin, BoardSection, BoardSignal, BoardStatus } from './boardTypes';
import { parseTimestampMs } from './boardTypes';

/**
 * A tradable price, or nothing.
 *
 * Zero and negative premiums are not levels. Several feeds emit `0` for "no
 * stop set", which renders as a "0.00" stop indistinguishable from a real one
 * — on a bought option that is the difference between a protected position and
 * an unprotected one.
 */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

/**
 * Options trade on the derivatives venue, not the cash one.
 *
 * The rows carry the underlying's exchange, so an NFO contract arrives tagged
 * NSE. Printing that on an order-facing board is wrong: you cannot buy
 * KOTAKBANK26AUG385CE on NSE.
 */
const DERIVATIVE_VENUE: Record<string, string> = { NSE: 'NFO', BSE: 'BFO' };
const venueFor = (exchange: string, kind: string) =>
  kind === 'option' ? (DERIVATIVE_VENUE[exchange] ?? exchange) : exchange;

/** Whether the microstructure model found this, or a plain directional scan did. */
function originOf(row: AdaptiveEdgeRow): BoardOrigin {
  const stratVer = row.strategy_version ? String(row.strategy_version).toLowerCase() : null;
  const ver = stratVer ? (stratVer.includes('v2') ? 'V2' : 'V1') : null;

  if (row.origin === 'spot_scan') {
    return {
      label: 'SPOT SCAN',
      tone: 'blue',
      hint: ver
        ? `Direction came from a SuperTrend spot scan; managed under Adaptive Edge ${ver === 'V2' ? 'V2 Hardened' : 'V1 Baseline'}.`
        : 'Direction came from a SuperTrend spot scan; Adaptive Edge chose the contract and manages the exit.',
    };
  }

  return ver
    ? {
        label: `AE ${ver}`,
        tone: 'brand',
        hint:
          ver === 'V2'
            ? 'Found by Adaptive Edge V2 Hardened (confluence + toxic lockout + rejection filter).'
            : 'Found by Adaptive Edge V1 Baseline (single anchor relaxed).',
      }
    : {
        label: 'AE MODEL',
        tone: 'brand',
        hint: "Found by Adaptive Edge's own microstructure and order-flow model.",
      };
}

const ms = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
};

/**
 * `open` says a position exists; `decision` says what the model wants to do
 * with it. An open row the model has already called EXIT is not "running" —
 * it is a position being withdrawn, and that is the moment worth surfacing.
 */
function status(row: AdaptiveEdgeRow): BoardStatus {
  if (!row.open) return 'ended';
  if (row.decision === 'EXIT') return 'weakening';
  return row.entryTime ? 'running' : 'armed';
}

/** The underlying thesis, in the underlying's own prices. */
function spotSection(row: AdaptiveEdgeRow): BoardSection | null {
  const any = [row.spotEntry, row.spotSl, row.spotTsl, row.poc, row.vwap, row.cvd].some((v) => v != null);
  if (!any) return null;
  const rupees = (v: number | null | undefined, dp = 0) => (v == null ? undefined : `₹${v.toFixed(dp)}`);
  return {
    title: 'Spot microstructure & order flow',
    layout: 'tiles',
    summary: row.horizon ? String(row.horizon) : undefined,
    stats: [
      { label: 'Spot entry', value: rupees(row.spotEntry), hint: 'Underlying level the thesis is anchored to' },
      { label: 'Spot SL', value: rupees(row.spotSl), hint: 'Underlying stop — not the option stop in the SL column' },
      { label: 'Spot TSL', value: rupees(row.spotTsl), hint: 'Where the underlying trail has ratcheted to' },
      { label: 'Spot exit', value: rupees(row.spotExit), hint: 'Underlying level the position closed at' },
      { label: 'POC anchor', value: rupees(row.poc), hint: 'Point of control — the most-traded price of the session' },
      { label: 'Session VWAP', value: rupees(row.vwap, 1), hint: 'Volume-weighted average price so far today' },
      {
        label: 'Order flow CVD',
        value: row.cvd == null ? undefined : `${row.cvd > 0 ? '+' : ''}${Math.round(row.cvd).toLocaleString('en-IN')}`,
        hint: 'Cumulative volume delta — buying minus selling pressure',
      },
      { label: 'Model score', value: row.score == null ? undefined : row.score.toFixed(2), hint: 'Adaptive Edge conviction. Not comparable with another engine’s score' },
      { label: 'Horizon', value: row.horizon ? String(row.horizon) : undefined, hint: 'How long the model expects the move to take' },
    ],
  };
}

/**
 * How the position was classified and whether that changed.
 *
 * A mode that was upgraded mid-trade means the model grew more confident after
 * entry — worth seeing, because the exit rule follows the current mode, not the
 * one the trade was opened under.
 */
function modeSection(row: AdaptiveEdgeRow): BoardSection | null {
  if (!row.entryMode && !row.currentMode) return null;
  const drift = row.modeUpgraded ? 'upgraded' : row.modeDowngraded ? 'downgraded' : undefined;
  return {
    title: 'Mode & lifecycle',
    layout: 'rows',
    summary: drift,
    stats: [
      { label: 'Origin', value: row.origin ?? '—', hint: 'Which scan surfaced this row' },
      { label: 'Entry mode', value: row.entryMode ? String(row.entryMode) : '—' },
      { label: 'Current mode', value: row.currentMode ? String(row.currentMode) : '—', hint: 'The exit rule follows this, not the entry mode' },
      { label: 'Peak mode', value: row.peakMode ? String(row.peakMode) : '—' },
      { label: 'Decision', value: row.decision, hint: 'What the model wants to do with the position right now' },
      { label: 'Feature quality', value: row.featureQuality, hint: 'FLAT means the inputs went stale, so the decision is degraded' },
      { label: 'Why closed', value: row.whyClosed ?? row.resolutionReason ?? '—' },
    ],
  };
}

/**
 * Per-lot economics.
 *
 * Deliberately "per lot" and not "at risk": the engine sizes nothing, so the
 * only honest statement is what one lot would cost and risk.
 */
function lotSection(row: AdaptiveEdgeRow): BoardSection | null {
  if (!row.lotSize) return null;
  const entry = price(row.entry);
  const stop = price(row.sl);
  const perLotRisk = entry != null && stop != null ? Math.max(0, (entry - stop) * row.lotSize) : null;
  const rupees = (v: number | null) => (v == null ? undefined : `₹${Math.round(v).toLocaleString('en-IN')}`);
  return {
    title: 'Per lot',
    layout: 'rows',
    summary: `${row.lotSize} per lot`,
    stats: [
      { label: 'Lot size', value: row.lotSize },
      { label: 'Cost of one lot', value: rupees(entry != null ? entry * row.lotSize : null), hint: 'Premium outlay for a single lot at the entry price' },
      { label: 'Risk on one lot', value: rupees(perLotRisk), hint: 'Entry to stop, for one lot. The engine does not choose a position size.' },
    ],
  };
}

export function adaptiveEdgeLegToBoard(row: AdaptiveEdgeRow): BoardSignal {
  const sections = [spotSection(row), lotSection(row), modeSection(row)].filter(Boolean) as BoardSection[];
  // A SELL side is a short even when the contract is a call, so the option type
  // alone cannot tell you which way the position leans.
  const direction = row.side === 'SELL' ? 'short' : 'long';

  return {
    id: row.id,
    engine: 'adaptive_edge',
    underlying: row.underlying,
    instrument: {
      symbol: row.instrument,
      exchange: venueFor(row.exchange, row.kind === 'spot' ? 'equity' : 'option'),
      kind: row.kind === 'spot' ? 'equity' : 'option',
      optionType: row.kind === 'option' ? row.optionType : undefined,
      strike: row.strike ?? null,
      expiry: row.expiry ?? null,
      lotSize: row.lotSize ?? null,
      moneyness: row.moneyness ?? null,
      quoteKey: row.instrument ? `${row.exchange}:${row.instrument}` : null,
    },
    direction,
    status: status(row),
    atMs: parseTimestampMs(
      row.entryTime ?? row.sessionDate ?? (row as any).session_date ?? row.observationTime ?? (row as any).timestamp_ms ?? (row as any).timestamp ?? (row as any).created_at
    ),
    levels: {
      ltp: price(row.ltp),
      entry: price(row.entry),
      stop: price(row.sl),
      trail: price(row.tsl),
      // Adaptive Edge quotes one exit level, which is the planned exit until
      // the row closes and it becomes the realised one.
      target: row.open ? price(row.exit) : null,
      exit: row.open ? null : price(row.exit),
    },
    // Adaptive Edge rows carry a lot size but no position size — the panel has
    // never had a Qty column, because the engine does not decide one. Putting
    // lot size under "Qty" would claim a position this app never sized, which
    // is the exact class of bug that once showed 2,400 units of an 18-rupee
    // option labelled "risk Rs 3,000". The columns stay empty and the per-lot
    // figures go in the detail, where they are labelled for what they are.
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: row.score,
    origin: originOf(row),
    reason: row.whyClosed ?? row.resolutionReason ?? null,
    sections,
  };
}

/** Most-actionable first, so a group takes its liveliest leg's status. */
const STATUS_ORDER: readonly BoardStatus[] = ['armed', 'running', 'weakening', 'watching', 'ended', 'error'];

/**
 * Group the legs of one signal under the idea they express.
 *
 * The board was showing five consecutive KOTAKBANK rows, then five FINNIFTY,
 * differing only by strike — the same unreadability that would follow
 * SuperTrend across if its signals were flattened. `parentId` already records
 * which legs belong together; this just honours it.
 *
 * A signal that produced a single leg stays a single row. Wrapping one leg in
 * a parent adds a disclosure that hides one thing behind one click.
 */
export function adaptiveEdgeToBoard(rows: readonly AdaptiveEdgeRow[]): BoardSignal[] {
  const groups = new Map<string, AdaptiveEdgeRow[]>();
  for (const row of rows) {
    const list = groups.get(row.parentId);
    if (list) list.push(row);
    else groups.set(row.parentId, [row]);
  }

  return [...groups.values()].map((members) => {
    const legs = members.map(adaptiveEdgeLegToBoard);
    if (legs.length === 1) return legs[0];

    const head = members[0];
    const status = legs.reduce<BoardStatus>(
      (best, leg) => (STATUS_ORDER.indexOf(leg.status) < STATUS_ORDER.indexOf(best) ? leg.status : best),
      legs[0].status,
    );
    return {
      ...legs[0],
      id: `ae-group-${head.parentId}`,
      instrument: {
        symbol: head.underlying,
        exchange: head.exchange,
        kind: 'index' as const,
        strike: null,
        expiry: null,
        lotSize: null,
        quoteKey: null,
      },
      status,
      // The idea is about the underlying, so the header shows its price.
      underlyingPrice: head.spotEntry ?? null,
      // The thesis has no premium of its own; the legs carry those.
      levels: { ltp: null, entry: null, stop: null, trail: null, target: null, exit: null },
      sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
      // Spot microstructure belongs to the idea, so it stays on the parent.
      // Per-lot economics belong to a contract, so they go with the legs.
      sections: legs[0].sections.filter((x) => x.title !== 'Per lot'),
      children: legs,
    };
  });
}
