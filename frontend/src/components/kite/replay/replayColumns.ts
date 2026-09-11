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

/**
 * Signal columns.
 *
 * The premium ladder and the underlying ladder are separate COLUMNS, never the
 * same one. A single "Entry / Stop Loss / Target" triple that took its entry
 * from `premium_entry` and its stop from `stop` mixed an option premium with an
 * index level inside one row, and the header could not say which was which.
 */
export const SIGNAL_CSV_COLUMNS: readonly CsvColumn<ReplaySignal>[] = [
  { header: 'Time', value: (r) => r.time_iso },
  { header: 'Strategy', value: (r) => (r.strategy || '').toUpperCase() },
  { header: 'Contract', value: (r) => r.contract ?? '' },
  { header: 'Underlying', value: (r) => r.instrument },
  { header: 'Spot', value: (r) => r.spot ?? '' },
  { header: 'Direction', value: (r) => r.direction },
  { header: 'Strength', value: (r) => r.strength },
  { header: 'Premium Entry', value: (r) => r.premium_entry ?? '' },
  { header: 'Premium SL', value: (r) => r.premium_sl ?? '' },
  { header: 'Premium Target', value: (r) => r.premium_target ?? '' },
  { header: 'Underlying Entry', value: (r) => (r.strength === 'WATCHING' ? '' : r.entry) },
  { header: 'Underlying SL', value: (r) => (r.strength === 'WATCHING' ? '' : r.stop) },
  { header: 'Underlying Target', value: (r) => (r.strength === 'WATCHING' ? '' : r.target) },
  {
    header: 'R:R',
    value: (r) => {
      const rr = rewardRisk(r.entry, r.stop, r.target);
      return rr == null ? '' : rr.toFixed(2);
    },
  },
  { header: 'Level', value: (r) => (r.level_price == null ? '' : r.level_price) },
  { header: 'Level Kind', value: (r) => r.level_kind ?? '' },
  { header: 'Regime', value: (r) => r.regime ?? '' },
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

/**
 * Whether this replay MODELLED execution friction.
 *
 * Read from the session's own configuration, not from whether a sampled row
 * happened to carry a value. Row-sniffing conflated three different states:
 * friction not modelled at all, friction modelled in "ideal" mode and found to
 * be zero, and a realistic run whose first rows had not yet been filled. A
 * session in "ideal" mode is a MEASURED zero and says so; a `₹0.00` for a
 * measurement never taken is the defect this whole surface was rebuilt over.
 */
export function replayHasFriction(
  config: { friction_mode?: string } | null | undefined,
  capabilities: { friction?: boolean } | null | undefined,
  trades: readonly ReplayTrade[],
): boolean {
  if (capabilities && capabilities.friction === false) return false;
  const mode = config?.friction_mode;
  if (mode === 'realistic') return true;
  if (mode === 'ideal') return false;
  // No config echo yet (a session restored from the ledger alone): fall back to
  // the rows, which is the only evidence available.
  return trades.some((t) => t.slippage != null && t.slippage > 0);
}

/** True when at least one trade carries a non-zero measured drag. */
export function tradesHaveFriction(trades: readonly ReplayTrade[]): boolean {
  return trades.some((t) => t.slippage != null && t.slippage > 0);
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
