/**
 * Gamma Move snapshot -> BoardSignal.
 *
 * One row per watched contract, plus one per open position. What this engine
 * puts on the board that the others have no equivalent for is the **trigger
 * arithmetic**: three conditions, each with its measured value against its
 * configured threshold. A trader has to be able to see which leg of the triple
 * is short, because "no signal" on this engine can mean three quite different
 * things and only one of them is worth waiting on.
 *
 * The origin badge answers "where did this come from" with the condition that
 * is carrying the signal, and the flags carry the two facts that decide whether
 * the setup is even the kind the calibration measured: how far spot is from the
 * level, and how many days are left on the contract.
 */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import { parseTimestampMs } from './boardTypes';
import type {
  GammaMoveSnapshot, GammaPositionRow, GammaSignalRow,
} from '../../../hooks/useGammaMove';

/** A tradable price, or nothing. Zero is not a level. */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

const n = (v: number | null | undefined, dp = 2): string =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

const STATE_TO_STATUS: Record<string, BoardStatus> = {
  watching: 'watching', armed: 'armed', running: 'running',
  weakening: 'weakening', ended: 'ended', error: 'error',
};

function listedContract(i: GammaSignalRow['instrument'] | undefined): boolean {
  return Boolean(i && i.option_type && i.expiry && i.strike != null && i.strike > 0);
}

function instrument(row: GammaSignalRow): BoardInstrument {
  const i = row.instrument || ({} as any);
  return {
    symbol: i.tradingsymbol || (row as any).symbol || '',
    exchange: i.exchange || 'NFO',
    kind: 'option',
    optionType: i.option_type || ((row as any).option_type ?? 'CE'),
    strike: i.strike ?? (row as any).strike ?? 0,
    expiry: i.expiry || (row as any).expiry || '',
    lotSize: i.lot_size ?? (row as any).lot_size ?? 1,
    moneyness: null,
    // No listed option → do not mint NFO:RELIANCE. Chart the underlying.
    quoteKey: symbol ? `${listed ? (i.exchange || 'NFO') : 'NSE'}:${symbol}` : null,
  };
}

/**
 * Which of the three conditions is carrying this row.
 *
 * Not decoration: the calibration found the open-interest condition is the one
 * that distinguishes this strategy from momentum, so a row showing volume and
 * price without it is a different setup wearing the same name.
 */
function originOf(row: GammaSignalRow): BoardOrigin | undefined {
  const m = row.metrics;
  if (!m) {
    return { label: 'NO DATA', tone: 'dim',
             hint: row.reason || 'Not enough of today’s bars to judge the trigger yet.' };
  }
  const detail = `Open interest ${m.oi_drop_pct >= 0 ? 'fell' : 'rose'} `
    + `${Math.abs(m.oi_drop_pct).toFixed(2)}% on the last bar, volume ran `
    + `${m.volume_ratio.toFixed(1)}× its recent average and the premium moved `
    + `${m.price_gain_pct >= 0 ? '+' : ''}${m.price_gain_pct.toFixed(2)}%.`;
  if (m.triggered) {
    return { label: 'OI UNWIND', tone: 'green',
             hint: `${detail} All three conditions hold — writers covering.` };
  }
  if (m.unwinding) {
    return { label: 'OI FALLING', tone: 'amber',
             hint: `${detail} Open interest is unwinding but the other conditions are not met.` };
  }
  return { label: 'QUIET', tone: 'dim',
           hint: `${detail} Open interest is not unwinding, so this is not the setup.` };
}

/**
 * The two facts that decide whether this is the kind of setup the calibration
 * measured. Distance to the level first, because that is where the edge was.
 */
function flagsOf(row: GammaSignalRow, proximityPct: number): BoardOrigin[] {
  const out: BoardOrigin[] = [];
  const levelObj = row.level || {
    distance_pct: (row as any).distance_pct ?? 0,
    kind: (row as any).level_type?.toLowerCase() === 'support' ? 'support' : 'resistance',
    price: (row as any).spot_level ?? row.spot ?? 0,
    touches: 1,
  };
  const d = levelObj.distance_pct ?? 0;
  const inside = d <= proximityPct;
  out.push({
    label: `${d.toFixed(2)}% FROM ${levelObj.kind === 'resistance' ? 'RES' : 'SUP'}`,
    tone: inside ? 'green' : 'dim',
    hint: inside
      ? `Spot ${n(row.spot)} is within ${proximityPct}% of a ${levelObj.kind} at `
        + `${n(levelObj.price)} touched ${levelObj.touches} times. This is the only `
        + 'filter that measurably separated from baseline.'
      : `Spot ${n(row.spot)} is ${d.toFixed(2)}% from the nearest ${levelObj.kind}, `
        + `outside the ${proximityPct}% band where the edge was measured.`,
  });
  if (row.days_to_expiry != null) {
    out.push({
      label: `${row.days_to_expiry}D TO EXPIRY`,
      tone: 'dim',
      hint: 'The strategy is only defined for the last week or two of a contract; '
        + 'earlier in the cycle open interest does not behave this way.',
    });
  }
  if (row.regime && row.regime !== 'unknown') {
    out.push({
      label: `TREND ${String(row.regime).toUpperCase()}`,
      tone: row.regime === 'up' ? 'green' : 'amber',
      hint: 'SuperTrend on the underlying. Calls need an uptrend, puts a downtrend. '
        + 'Measured at multiplier 2.0 — at the conventional 3.0 the gate inverted.',
    });
  }
  return out;
}

/**
 * What the broker knows about a held position.
 *
 * Two states a board must never blur. "We sent an order" is not "we hold this",
 * and "protected" is not "protected only while this process is alive" — the
 * second of each is the one that costs money at 3am.
 */
function positionFlags(p: GammaPositionRow): BoardOrigin[] {
  const out: BoardOrigin[] = [];
  if (p.status === 'pending') {
    out.push({ label: 'UNCONFIRMED', tone: 'amber',
               hint: `Order ${p.order_id || '—'} was sent but the broker has not `
                 + 'confirmed a fill. The position may or may not exist.' });
  }
  if (p.gtt_id > 0) {
    out.push({ label: 'GTT ARMED', tone: 'green',
               hint: `A broker-side stop (#${p.gtt_id}) is protecting this position. `
                 + 'It survives this process dying.' });
  } else if (p.stop_mode !== 'monitor') {
    out.push({ label: 'NO BROKER STOP', tone: 'amber',
               hint: 'This process is the only thing watching the position — the '
                 + 'broker-side stop is not in place. If it dies, nothing exits.' });
  }
  return out;
}

function triggerSection(row: GammaSignalRow, cfg?: GammaMoveSnapshot['config']): BoardSection {
  const m = row.metrics;
  const mark = (ok: boolean) => (ok ? '✓' : '✗');
  if (!m) {
    return { title: 'Trigger', layout: 'rows',
             summary: 'Not enough of today’s bars to evaluate.', stats: [] };
  }
  const minOi = cfg?.min_oi_drop_pct ?? 10;
  const volMult = cfg?.volume_spike_mult ?? 1.5;
  const volLookback = cfg?.volume_lookback ?? 20;
  const minGain = cfg?.min_price_gain_pct ?? 5;
  return {
    title: 'Trigger',
    layout: 'rows',
    summary: m.triggered
      ? `All three conditions hold, confirmed on ${m.bars_confirmed} of ${m.bars_required} bars.`
      : `Incomplete — ${row.reason ?? 'conditions not met'}.`,
    stats: [
      { label: `${mark(m.unwinding)} OI unwinding`,
        value: `${m.oi_drop_pct.toFixed(2)}%`,
        hint: `needs ≥ ${minOi}%` },
      { label: `${mark(m.abnormal)} Volume`,
        value: `${m.volume_ratio.toFixed(2)}×`,
        hint: `needs ≥ ${volMult}× the ${volLookback}-bar mean` },
      { label: `${mark(m.rising)} Premium`,
        value: `${m.price_gain_pct >= 0 ? '+' : ''}${m.price_gain_pct.toFixed(2)}%`,
        hint: `needs ≥ ${minGain}%` },
      { label: 'Bars confirmed', value: `${m.bars_confirmed}/${m.bars_required}` },
    ],
  };
}

function levelSection(row: GammaSignalRow, cfg?: GammaMoveSnapshot['config']): BoardSection {
  const prox = cfg?.level_proximity_pct ?? 1.5;
  const tf = cfg?.level_timeframe ?? 'day';
  const levelObj = row.level || {
    distance_pct: (row as any).distance_pct ?? 0,
    kind: (row as any).level_type?.toLowerCase() === 'support' ? 'support' : 'resistance',
    price: (row as any).spot_level ?? row.spot ?? 0,
    touches: 1,
  };
  return {
    title: 'Level',
    layout: 'tiles',
    summary: `${row.underlying} spot ${n(row.spot)} against a ${levelObj.kind} at `
      + `${n(levelObj.price)}.`,
    stats: [
      { label: 'Level', value: n(levelObj.price) },
      { label: 'Kind', value: levelObj.kind },
      { label: 'Touches', value: String(levelObj.touches) },
      { label: 'Distance', value: `${(levelObj.distance_pct ?? 0).toFixed(2)}%`,
        hint: `the measured edge is inside ${prox}%` },
      { label: 'Timeframe', value: tf },
    ],
  };
}

function contractSection(row: GammaSignalRow): BoardSection {
  const i = row.instrument || ({} as any);
  return {
    title: 'Contract',
    layout: 'tiles',
    stats: [
      { label: 'Strike', value: n(i.strike, 0) },
      { label: 'Type', value: i.option_type || '—' },
      { label: 'Expiry', value: i.expiry || '—' },
      { label: 'Days left', value: String(row.days_to_expiry ?? '—') },
      { label: 'Open interest', value: row.oi ? row.oi.toLocaleString('en-IN') : '—' },
      { label: 'Lot size', value: String(i.lot_size ?? '—') },
    ],
  };
}

function toSignal(row: GammaSignalRow, cfg?: GammaMoveSnapshot['config'],
                  position?: GammaPositionRow): BoardSignal {
  const lv = row.levels || {
    ltp: (row as any).ltp ?? null,
    entry: (row as any).entry_premium ?? (row as any).entry ?? null,
    stop: (row as any).stop_premium ?? (row as any).stop ?? null,
    trail: (row as any).trail ?? null,
    target: (row as any).target_premium ?? (row as any).target ?? null,
    exit: (row as any).exit ?? null,
  };
  const sz = row.sizing || {
    lots: (row as any).lots ?? null,
    quantity: (row as any).quantity ?? null,
    at_risk_inr: (row as any).at_risk_inr ?? null,
    deployed_inr: (row as any).deployed_inr ?? null,
  };
  return {
    id: row.id,
    engine: 'gamma_move',
    underlying: row.underlying,
    instrument: instrument(row),
    // This strategy only ever BUYS options — it never writes one. The constant
    // is correct here rather than a placeholder, and saying so matters: a
    // counter that inferred sell intent from a direction field once sold every
    // put in the book at entry.
    direction: 'long',
    status: STATE_TO_STATUS[row.state] ?? 'watching',
    atMs: parseTimestampMs(
      row.at_ms ?? (row as any).timestamp_ms ?? (row as any).entered_ms ?? (row as any).created_at ?? (row as any).timestamp ?? (row as any).session_date
    ),
    levels: {
      ltp: price(lv.ltp),
      // The REAL fill once there is one. Showing the intended entry beside a
      // live P&L computed from the actual fill is two numbers that disagree.
      entry: price(position?.effective_entry ?? position?.entry ?? lv.entry),
      stop: price(position?.stop ?? lv.stop),
      trail: price(position?.trail ?? lv.trail),
      target: price(position?.target ?? lv.target),
      exit: price(lv.exit),
    },
    sizing: {
      lots: position?.lots ?? sz.lots ?? null,
      quantity: position?.quantity ?? sz.quantity ?? null,
      atRiskInr: sz.at_risk_inr ?? null,
      deployedInr: sz.deployed_inr ?? null,
    },
    // This engine publishes no score, and inventing one would be a number a
    // trader could compare against engines that mean something by it.
    score: null,
    reason: row.reason ?? row.exit_reason ?? null,
    origin: originOf(row),
    flags: [...flagsOf(row, cfg?.level_proximity_pct ?? 1.5),
            ...(position ? positionFlags(position) : [])],
    underlyingPrice: price(row.spot),
    sections: [triggerSection(row, cfg), levelSection(row, cfg), contractSection(row)],
  };
}

export function gammaMoveToBoard(snapshot?: GammaMoveSnapshot | null): BoardSignal[] {
  if (!snapshot) return [];
  const cfg = snapshot.config;
  const positions = new Map((snapshot.positions ?? []).map((p) => [p.signal_id, p]));
  const rows = snapshot.candidates ?? (snapshot as any).signals ?? [];
  const seen = new Set<string>();
  const out: BoardSignal[] = [];

  for (const row of rows) {
    seen.add(row.id);
    const pos = positions.get(row.id);
    const sig = toSignal(row, cfg, pos);
    out.push(pos ? { ...sig, status: sig.status === 'ended' ? 'ended' : 'running' } : sig);
  }
  // A held position whose candidate has dropped out of the scan must not vanish
  // from the board — that is exactly the row an operator needs most.
  for (const p of snapshot.positions ?? []) {
    if (seen.has(p.signal_id)) continue;
    out.push({
      id: p.signal_id,
      engine: 'gamma_move',
      underlying: p.symbol,
      instrument: { symbol: p.symbol, exchange: 'NFO', kind: 'option', quoteKey: `NFO:${p.symbol}` },
      direction: 'long',
      status: 'running',
      atMs: p.entered_ms || null,
      levels: { ltp: null, entry: price(p.effective_entry ?? p.entry), stop: price(p.stop),
                trail: price(p.trail), target: price(p.target), exit: null },
      sizing: { lots: p.lots, quantity: p.quantity, atRiskInr: null, deployedInr: null },
      score: null,
      reason: `held since ${p.entry_day}, ${p.sessions_held} session(s)`,
      origin: { label: 'HELD', tone: 'blue',
                hint: 'Open position; its candidate is no longer in the current scan.' },
      flags: positionFlags(p),
      sections: [],
    });
  }
  return out;
}
