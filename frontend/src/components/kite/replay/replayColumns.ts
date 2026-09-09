import type { CsvColumn } from './replayCsv';
import type { ReplaySignal, ReplayTrade } from '../../../hooks/useReplayStore';
import { rewardRisk } from './replayFormat';

/**
 * Column definitions shared by the rendered tables and the CSV export.
 *
 * They live together so the two cannot diverge — the previous surface exported
 * 20 trade columns from the dock and 17 from the summary, four of which were
 * always empty because the backend never populated them.
 */

export const SIGNAL_CSV_COLUMNS: readonly CsvColumn<ReplaySignal>[] = [
  { header: 'Time', value: (r) => r.time_iso },
  { header: 'Strategy', value: (r) => (r.strategy || '').toUpperCase() },
  { header: 'Contract', value: (r) => r.contract ?? '' },
  { header: 'Underlying', value: (r) => r.instrument },
  { header: 'Spot', value: (r) => r.spot ?? '' },
  { header: 'Direction', value: (r) => r.direction },
  { header: 'Strength', value: (r) => r.strength },
  { header: 'Entry', value: (r) => r.premium_entry ?? (r.strength === 'WATCHING' ? '' : r.entry) },
  { header: 'Stop Loss', value: (r) => r.stop },
  { header: 'Target', value: (r) => r.target },
  {
    header: 'R:R',
    value: (r) => {
      const rr = rewardRisk(r.entry, r.stop, r.target);
      return rr == null ? '' : rr.toFixed(2);
    },
  },
];

/**
 * Trade columns. The friction three are appended only when the replay actually
 * modelled friction — exporting a permanently blank `Slippage` column is the
 * paper version of showing `₹0.00` in the UI.
 */
export function tradeCsvColumns(hasFriction: boolean): readonly CsvColumn<ReplayTrade>[] {
  const base: CsvColumn<ReplayTrade>[] = [
    { header: 'Trade ID', value: (r) => r.trade_id },
    { header: 'Entry Time', value: (r) => r.entry_time_iso },
    { header: 'Exit Time', value: (r) => r.exit_time_iso },
    { header: 'Held (mins)', value: (r) => r.duration_mins },
    { header: 'Strategy', value: (r) => (r.strategy || '').toUpperCase() },
    { header: 'Contract', value: (r) => r.symbol },
    { header: 'Underlying', value: (r) => r.underlying },
    { header: 'Option Type', value: (r) => r.opt_type },
    { header: 'Strike', value: (r) => r.strike },
    { header: 'Lots', value: (r) => r.lots },
    { header: 'Quantity', value: (r) => r.quantity },
    { header: 'Entry Fill', value: (r) => r.entry_price },
    { header: 'Exit Fill', value: (r) => r.exit_price ?? '' },
    { header: 'Stop Loss', value: (r) => r.stop_loss },
    { header: 'Target', value: (r) => r.target_price },
    { header: 'Status', value: (r) => r.status },
    { header: 'Exit Reason', value: (r) => r.exit_reason ?? '' },
    {
      header: 'Invested (INR)',
      value: (r) => Number((((r.entry_price || 0) * (r.quantity || 0)) || 0).toFixed(2)),
    },
    {
      header: 'PnL (INR)',
      value: (r) => (r.pnl_usd == null ? '' : Number(r.pnl_usd.toFixed(2))),
    },
    {
      header: 'PnL (%)',
      value: (r) => (r.pnl_pct == null ? '' : Number(r.pnl_pct.toFixed(2))),
    },
  ];
  if (!hasFriction) return base;
  return [
    ...base,
    { header: 'Raw Entry', value: (r) => r.raw_entry ?? '' },
    { header: 'Raw Exit', value: (r) => r.raw_exit ?? '' },
    { header: 'Slippage (INR)', value: (r) => (r.slippage == null ? '' : r.slippage.toFixed(2)) },
  ];
}

/** True when at least one trade carries measured friction. */
export function tradesHaveFriction(trades: readonly ReplayTrade[]): boolean {
  return trades.some((t) => t.slippage != null);
}

/**
 * Stable, UNIQUE row identity.
 *
 * The natural key — time, strategy, instrument — is not unique: two signals
 * from one strategy on one instrument inside the same second collide, and
 * React then drops or duplicates rows. The position in the append-only events
 * array disambiguates them, and stays stable because the array only ever grows
 * within a session (a seek truncates it, which correctly re-derives the keys
 * of everything that survives).
 */
export function signalKey(s: ReplaySignal, index: number): string {
  return `${index}|${s.time_iso}|${s.strategy}|${s.instrument}`;
}
