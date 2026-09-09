/**
 * OI Wall Flow snapshot -> BoardSignal.
 *
 * What this engine puts on the board that the others have no equivalent for is
 * the **chain reading**: bias, walls, PCR, max pain. A trader has to see why
 * the row is a 3500 CE and not a PE — that is the whole thesis.
 */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import type {
  OIWallFlowPositionRow, OIWallFlowSignalRow, OIWallFlowSnapshot,
} from '../../../hooks/useOiWallFlow';

const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

const n = (v: number | null | undefined, dp = 2): string =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

const STATE_TO_STATUS: Record<string, BoardStatus> = {
  watching: 'watching', armed: 'armed', running: 'running',
  ended: 'ended', error: 'error',
};

function instrument(row: OIWallFlowSignalRow): BoardInstrument {
  const i = row.instrument ?? row.plan?.instrument;
  const plan = row.plan;
  return {
    symbol: i?.tradingsymbol || plan?.tradingsymbol || '',
    exchange: i?.exchange || 'NFO',
    kind: 'option',
    optionType: i?.option_type ?? plan?.option_type,
    strike: i?.strike ?? plan?.strike,
    expiry: i?.expiry || row.expiry,
    lotSize: i?.lot_size ?? plan?.lot_size,
    moneyness: null,
    quoteKey: (i?.tradingsymbol || plan?.tradingsymbol)
      ? `${i?.exchange || 'NFO'}:${i?.tradingsymbol || plan?.tradingsymbol}`
      : null,
  };
}

function originOf(row: OIWallFlowSignalRow): BoardOrigin | undefined {
  const b = row.bias;
  if (!b) {
    return { label: 'NO CHAIN', tone: 'dim', hint: 'No chain reading yet.' };
  }
  if (row.state === 'armed' && row.plan) {
    const side = row.plan.option_type;
    return {
      label: side === 'CE' ? 'CALL WALL' : 'PUT WALL',
      tone: 'green',
      hint: side === 'CE'
        ? `Bullish flow (score ${n(b.score)}) — buy the first-resistance CE at ${n(row.plan.strike, 0)}, not ATM ${n(b.atm_strike, 0)}.`
        : `Bearish flow (score ${n(b.score)}) — buy the first-support PE at ${n(row.plan.strike, 0)}, not ATM ${n(b.atm_strike, 0)}.`,
    };
  }
  if (b.bias === 'neutral') {
    return {
      label: 'NEUTRAL',
      tone: 'dim',
      hint: row.reason || 'Near-ATM flow does not agree yet.',
    };
  }
  return {
    label: b.bias.toUpperCase(),
    tone: 'amber',
    hint: row.reason || `Bias ${b.bias} (score ${n(b.score)}) but no trade plan.`,
  };
}

function flagsOf(row: OIWallFlowSignalRow): BoardOrigin[] {
  const out: BoardOrigin[] = [];
  const b = row.bias;
  if (b) {
    out.push({
      label: `CALL ${n(b.call_wall, 0)} / PUT ${n(b.put_wall, 0)}`,
      tone: 'dim',
      hint: `Call wall (max call OI) ${n(b.call_wall, 0)}, put wall (max put OI) ${n(b.put_wall, 0)}. ATM ${n(b.atm_strike, 0)}.`,
    });
    out.push({
      label: `PCR ${n(b.pcr_oi)}`,
      tone: 'dim',
      hint: 'Recorded, not voted. Sub-1 PCR is a call-writing ceiling, not a short signal.',
    });
  }
  if (row.days_to_expiry != null) {
    out.push({
      label: `${row.days_to_expiry}D TO EXPIRY`,
      tone: 'dim',
      hint: 'OI on expiry day is settlement, not positioning — those rows are refused.',
    });
  }
  return out;
}

function positionFlags(p: OIWallFlowPositionRow): BoardOrigin[] {
  const out: BoardOrigin[] = [];
  if (p.status === 'pending') {
    out.push({
      label: 'UNCONFIRMED', tone: 'amber',
      hint: `Order ${p.order_id || '—'} was sent but the broker has not confirmed a fill.`,
    });
  }
  if (p.gtt_id > 0) {
    out.push({
      label: 'GTT ARMED', tone: 'green',
      hint: `A broker-side stop (#${p.gtt_id}) is protecting this position.`,
    });
  } else if (p.stop_mode !== 'monitor') {
    out.push({
      label: 'NO BROKER STOP', tone: 'amber',
      hint: 'This process is the only thing watching the position.',
    });
  }
  return out;
}

function wallsSection(row: OIWallFlowSignalRow): BoardSection {
  const b = row.bias;
  return {
    title: 'Chain',
    layout: 'tiles',
    summary: b
      ? `${row.underlying} spot ${n(row.spot)} · bias ${b.bias} (${n(b.score)})`
      : undefined,
    stats: [
      { label: 'Bias', value: b?.bias ?? '—' },
      { label: 'Score', value: n(b?.score) },
      { label: 'Call wall', value: n(b?.call_wall, 0) },
      { label: 'Put wall', value: n(b?.put_wall, 0) },
      { label: 'ATM', value: n(b?.atm_strike, 0) },
      { label: 'Max pain', value: n(b?.max_pain, 0) },
      { label: 'PCR (OI)', value: n(b?.pcr_oi) },
    ],
  };
}

function contractSection(row: OIWallFlowSignalRow): BoardSection {
  const plan = row.plan;
  const inst = row.instrument ?? plan?.instrument;
  return {
    title: 'Contract',
    layout: 'tiles',
    stats: [
      { label: 'Strike', value: n(plan?.strike ?? inst?.strike, 0) },
      { label: 'Type', value: plan?.option_type ?? inst?.option_type ?? '—' },
      { label: 'Expiry', value: inst?.expiry || row.expiry || '—' },
      { label: 'Days left', value: row.days_to_expiry == null ? '—' : String(row.days_to_expiry) },
      { label: 'Lot size', value: String(plan?.lot_size ?? inst?.lot_size ?? '—') },
      { label: 'Invalidation', value: n(plan?.underlying_invalidation, 0),
        hint: 'Spot through the opposing wall kills the thesis even if premium has not caught up.' },
    ],
  };
}

function toSignal(row: OIWallFlowSignalRow, position?: OIWallFlowPositionRow): BoardSignal {
  const lv = row.levels || {
    ltp: (row as any).ltp ?? null,
    entry: (row as any).entry_premium ?? (row as any).entry ?? null,
    stop: (row as any).stop_premium ?? (row as any).stop ?? null,
    trail: null,
    target: (row as any).target_premium ?? (row as any).target ?? null,
    exit: (row as any).exit ?? null,
  };
  const plan = row.plan;
  const sz = row.sizing || {
    lots: (row as any).lots ?? null,
    quantity: (row as any).quantity ?? null,
    at_risk_inr: (row as any).at_risk_inr ?? null,
    deployed_inr: (row as any).deployed_inr ?? null,
  };
  return {
    id: row.id,
    engine: 'oi_wall_flow',
    underlying: row.underlying,
    instrument: instrument(row),
    direction: 'long',
    status: STATE_TO_STATUS[row.state] ?? 'watching',
    atMs: row.at_ms || null,
    levels: {
      ltp: price(lv?.ltp),
      entry: price(position?.effective_entry ?? position?.entry ?? lv?.entry),
      stop: price(position?.stop ?? lv?.stop),
      trail: null,
      target: price(position?.target ?? lv?.target),
      exit: price(lv?.exit),
    },
    sizing: {
      lots: position?.lots ?? sz?.lots ?? null,
      quantity: position?.quantity ?? sz?.quantity ?? null,
      atRiskInr: sz?.at_risk_inr ?? null,
      deployedInr: sz?.deployed_inr ?? null,
    },
    score: row.bias ? Math.min(100, Math.abs(row.bias.score) * 10) : null,
    reason: row.reason ?? plan?.reason ?? null,
    origin: originOf(row),
    flags: [...flagsOf(row), ...(position ? positionFlags(position) : [])],
    underlyingPrice: price(row.spot),
    sections: [wallsSection(row), contractSection(row)],
  };
}

export function oiWallFlowToBoard(snapshot?: OIWallFlowSnapshot | null): BoardSignal[] {
  if (!snapshot) return [];
  const positions = new Map((snapshot.positions ?? []).map((p) => [p.signal_id, p]));
  const rows = snapshot.candidates ?? [];
  const seen = new Set<string>();
  const out: BoardSignal[] = [];

  for (const row of rows) {
    seen.add(row.id);
    const pos = positions.get(row.id);
    const sig = toSignal(row, pos);
    out.push(pos ? { ...sig, status: sig.status === 'ended' ? 'ended' : 'running' } : sig);
  }
  for (const p of snapshot.positions ?? []) {
    if (seen.has(p.signal_id)) continue;
    out.push({
      id: p.signal_id,
      engine: 'oi_wall_flow',
      underlying: p.symbol,
      instrument: { symbol: p.symbol, exchange: 'NFO', kind: 'option', quoteKey: `NFO:${p.symbol}` },
      direction: 'long',
      status: 'running',
      atMs: p.entered_ms || null,
      levels: {
        ltp: null, entry: price(p.effective_entry ?? p.entry), stop: price(p.stop),
        trail: null, target: price(p.target), exit: null,
      },
      sizing: { lots: p.lots, quantity: p.quantity, atRiskInr: null, deployedInr: null },
      score: null,
      reason: `held since ${p.entry_day}`,
      origin: { label: 'HELD', tone: 'blue',
                hint: 'Open position; its candidate is no longer in the current scan.' },
      flags: positionFlags(p),
      sections: [],
    });
  }
  return out;
}
