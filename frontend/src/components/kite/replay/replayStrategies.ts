/**
 * Strategy identity for the replay dock.
 *
 * One registry so a strategy is the same colour in the timeline heatmap, the
 * signals table, the filter list and the summary breakdown. The surface this
 * replaced identified strategies by emoji, which do not inherit `currentColor`,
 * do not respond to the theme, and break the tabular alignment the tables rely
 * on.
 *
 * NOTE ON IDS: these must match the strings the backend emits on
 * `SimSignalEvent.strategy`. A mismatch does not error — it silently filters
 * everything out — so `strategyTone` falls back to a neutral colour and
 * `strategyLabel` falls back to the raw id rather than rendering nothing.
 */

export type ReplayStrategy = {
  id: string;
  label: string;
  tone: string;
};

export const REPLAY_STRATEGIES: readonly ReplayStrategy[] = [
  { id: 'supertrend', label: 'SuperTrend', tone: 'var(--k-blue)' },
  { id: 'vcp', label: 'VCP Squeeze', tone: 'var(--k-violet)' },
  { id: 'adaptive_edge', label: 'Adaptive Edge', tone: 'var(--k-cyan)' },
  { id: 'navigator', label: 'Navigator', tone: 'var(--k-emerald)' },
  { id: 'gamma_move', label: 'Gamma Move', tone: 'var(--k-violet)' },
] as const;

const BY_ID = new Map(REPLAY_STRATEGIES.map((s) => [s.id, s]));

/** Strategy names arrive in mixed case from different engines. */
function normalise(id: string | null | undefined): string {
  return (id || '').trim().toLowerCase();
}

export function strategyLabel(id: string | null | undefined): string {
  return BY_ID.get(normalise(id))?.label ?? (id || 'Unknown');
}

export function strategyTone(id: string | null | undefined): string {
  return BY_ID.get(normalise(id))?.tone ?? 'var(--k-dim)';
}

/** Key used to merge rows across the signals and trades ledgers. */
export function strategyKey(id: string | null | undefined): string {
  return normalise(id) || 'unknown';
}

export const MONEYNESS_LEGS = [
  { id: 'ATM', label: 'ATM', hint: 'At the money' },
  { id: 'ITM1', label: 'ITM1', hint: 'One strike in the money' },
  { id: 'ITM2', label: 'ITM2', hint: 'Two strikes in the money' },
  { id: 'OTM1', label: 'OTM1', hint: 'One strike out of the money' },
  { id: 'OTM2', label: 'OTM2', hint: 'Two strikes out of the money' },
] as const;

export const CORE_INDICES = [
  { id: 'NIFTY', label: 'Nifty 50' },
  { id: 'BANKNIFTY', label: 'Bank Nifty' },
  { id: 'SENSEX', label: 'Sensex' },
  { id: 'FINNIFTY', label: 'Fin Nifty' },
  { id: 'MIDCPNIFTY', label: 'Midcap Nifty' },
] as const;

export const CORE_STOCKS = [
  { id: 'RELIANCE', label: 'Reliance' },
  { id: 'TCS', label: 'TCS' },
  { id: 'INFY', label: 'Infosys' },
  { id: 'HDFCBANK', label: 'HDFC Bank' },
  { id: 'ICICIBANK', label: 'ICICI Bank' },
  { id: 'SBIN', label: 'SBI' },
  { id: 'BHARTIARTL', label: 'Bharti Airtel' },
  { id: 'BAJAJFINSV', label: 'Bajaj Finserv' },
  { id: 'BAJFINANCE', label: 'Bajaj Finance' },
  { id: 'LT', label: 'L&T' },
] as const;
