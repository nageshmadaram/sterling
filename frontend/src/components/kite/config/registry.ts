import type {
  EngineConfigModel, ExitMode, Moneyness, ScanSource, TrailTarget,
} from '../../../types/kiteEngine';

/**
 * ONE description of every Kite trade setting.
 *
 * Before this module the same field was described independently by each surface
 * that rendered it, and the descriptions had already drifted: `scan_source` was
 * "Derivatives" on the SuperTrend page and "Options" on the shared page, and
 * whether changing a field triggered a rescan depended on which panel you
 * happened to click it in (EngineConfigurationPanel defaulted rescan=false,
 * SharedScanSetupPanel defaulted rescan=true) — so `exit_mode` rescanned from
 * the settings page but not from the board header.
 *
 * Rescan policy, label, help text and ownership are properties of the FIELD, so
 * they live here and every surface reads them. That is what keeps the settings
 * page, the board header shortcuts and the signal-table controls in sync: not a
 * convention, but the absence of a second copy.
 */

// ── Sections of the control center ──────────────────────────────────────────
// Exported so the board and other panes can deep-link without duplicating the
// string literals (they used to pass unvalidated bare strings).
export type SectionId =
  | 'account' | 'truedata' | 'diagnostics' | 'mode' | 'manualRules' | 'autoRules'
  | 'engine' | 'navigator' | 'adaptiveEdge' | 'orbOptions' | 'atmPremiumImbalance'
  | 'gammaMove' | 'oiWallFlow' | 'bearToBearish'
  | 'markets' | 'notifications'
  | 'experience' | 'dataLake';

/** Where an order came from. The axis the user asked to see settings split by. */
export type Applies = 'manual' | 'auto' | 'both';

/** Which layer genuinely owns a setting — see the design doc's ownership table. */
export type Owner =
  | 'market'      // what gets scanned / which contracts. BOTH engines read these.
  | 'supertrend'  // only exists because the strategy has three SuperTrend lines.
  | 'execution';  // engine-independent: how an order is sized, guarded, protected.

/** Trade lifecycle stage, used to order the Trade Rules page. */
export type Stage =
  | 'universe' | 'discovery' | 'entry' | 'size'
  | 'stop' | 'trail' | 'target' | 'exit' | 'protection' | 'guard';

export interface FieldDef {
  key: keyof EngineConfigModel;
  /** The one name this setting has, on every surface. */
  label: string;
  help: string;
  owner: Owner;
  applies: Applies;
  stage: Stage;
  /** True when changing it makes the current board rows stale. */
  rescan: boolean;
  /** The section whose page holds the editable control. */
  home: SectionId;
  /**
   * Backend evidence for the `applies` tag. Surfaced in the UI as the chip's
   * tooltip so a claim about real-money behaviour is never unsourced.
   */
  evidence: string;
}

/*
 * There is deliberately no per-field "does Navigator read this too?" tag here.
 *
 * One existed, to correct a blanket "both engines read every setting here"
 * claim on the shared Market & Contracts page. That page is gone — each engine
 * now owns its own scan settings — and the tag was never rendered by anything.
 * It had already gone stale: it marked `strike_moneyness` and the expiry lists
 * as shared unconditionally, which stopped being true once Navigator got its
 * own contract coverage (navigator/runtime prefers its own value and falls back
 * to the engine's). Unread metadata cannot be kept honest, so the statement now
 * lives where it is true and visible: Navigator's own page shows, per group,
 * whether it is following SuperTrend and what it is following.
 */

const F = <T extends Record<string, FieldDef>>(defs: T) => defs;

export const FIELDS = F({
  // ── Engine power ──────────────────────────────────────────────────────────
  engine_enabled: {
    key: 'engine_enabled',
    label: 'SuperTrend engine',
    help: 'Whether the SuperTrend engine scans and produces signals at all.',
    owner: 'supertrend', applies: 'both', stage: 'discovery', rescan: false, home: 'engine',
    evidence: 'service.scan_user returns early when it is off; Navigator is unaffected and can run on its own.',
  },

  // ── What an engine scans ──────────────────────────────────────────────────
  scan_source: {
    key: 'scan_source',
    label: 'Chart source',
    help: 'Which price series SuperTrend reads. Navigator keeps its own separate setting.',
    owner: 'market', applies: 'both', stage: 'discovery', rescan: true, home: 'engine',
    evidence: 'service.scan_user builds the spot/premium/confluence universes from it, and service._make_place_cb uses it for the both-mode cross guard.',
  },
  strike_moneyness: {
    key: 'strike_moneyness',
    label: 'Strike range',
    help: 'Which strikes are resolved for each setup. Also decides which contract an automatic BUY hits.',
    owner: 'market', applies: 'both', stage: 'discovery', rescan: true, home: 'engine',
    evidence: 'scanner.option_order_args picks the automatic leg from exactly these strikes. Navigator falls back to this list only when it has no ladder of its own.',
  },
  scan_expiries_indices: {
    key: 'scan_expiries_indices',
    label: 'Index expiries',
    help: 'Contract cycles scanned for indices.',
    owner: 'market', applies: 'both', stage: 'discovery', rescan: true, home: 'engine',
    evidence: 'Handed to the scanner. Navigator falls back to it only when it has no expiry cycles of its own.',
  },
  scan_indices: {
    key: 'scan_indices',
    label: 'Indices',
    help: 'The indices included in every scan.',
    owner: 'market', applies: 'both', stage: 'universe', rescan: true, home: 'engine',
    evidence: 'Applied to both the spot and derivatives scans, and to Navigator when its scan scope is shared.',
  },
  scan_stocks: {
    key: 'scan_stocks',
    label: 'F&O stocks',
    help: 'The individual stocks included in every scan.',
    owner: 'market', applies: 'both', stage: 'universe', rescan: true, home: 'engine',
    evidence: 'Applied to both the spot and derivatives scans, and to Navigator when its scan scope is shared.',
  },
  scan_stock_contracts: {
    key: 'scan_stock_contracts',
    label: 'Scan stock contracts',
    help: 'Off leaves single-stock underlyings out of the scan entirely — no stock contracts are resolved and no stock rows appear. Indices are unaffected.',
    owner: 'market', applies: 'both', stage: 'universe', rescan: true, home: 'engine',
    evidence: 'universe.select_scan_universe drops every single-stock item, so nothing downstream ever sees one.',
  },
  scan_all_stocks: {
    key: 'scan_all_stocks',
    label: 'All F&O stocks',
    help: 'Use the full eligible universe instead of a curated list.',
    owner: 'market', applies: 'both', stage: 'universe', rescan: true, home: 'engine',
    evidence: 'Same scan boundary as scan_stocks.',
  },

  // ── SuperTrend strategy mechanics ─────────────────────────────────────────
  allow_short: {
    key: 'allow_short',
    label: 'Short entries',
    help: 'Whether a bear alignment may open a trade. Measured over 7.5 years on all '
      + 'four indices, the short book netted about zero across 1102 trades while the '
      + 'long book earned the entire return — so it is off, and turning it on doubles '
      + 'the fills and widens the drawdown for no measured gain.',
    owner: 'supertrend', applies: 'both', stage: 'entry', rescan: true, home: 'engine',
    evidence: 'Gates the entry masks in scanner.evaluate_item and engine.generate. The '
      + 'exit path keeps the full shorts mask — the red counter needs it to close a long.',
  },
  trail_target: {
    key: 'trail_target',
    label: 'Trail tightness',
    help: 'Which SuperTrend line the stop follows. Tight is the most out-of-sample robust in the 7.5y sweep.',
    owner: 'supertrend', applies: 'both', stage: 'trail', rescan: true, home: 'engine',
    evidence: 'Computes the board plan that protection.plan_for_symbol hands to a hand-placed arm, as well as the automatic one.',
  },
  exit_mode: {
    key: 'exit_mode',
    label: 'Exit rule',
    help: 'How many SuperTrend lines must turn red to close a trade. Entry always needs three green lines and a fresh signal.',
    owner: 'supertrend', applies: 'both', stage: 'exit', rescan: true, home: 'engine',
    evidence: 'protection.arm_position freezes it onto the position at arm time, for manual arms too; the monitor reads it from there.',
  },
  exit_aligned_trail: {
    key: 'exit_aligned_trail',
    label: 'Stop anchor',
    help: 'Anchor the stop to the exit counter instead of the tightest line.',
    owner: 'supertrend', applies: 'both', stage: 'trail', rescan: true, home: 'engine',
    evidence: 'Changes the computed stop, so it changes what a manual arm is given.',
  },
  price_stop_exit: {
    key: 'price_stop_exit',
    label: 'Trail can close the trade',
    help: 'On: a trade is closed the first bar price trades through its trail. Off restores the old rule where only the exit counter could close it.',
    owner: 'supertrend', applies: 'both', stage: 'exit', rescan: true, home: 'engine',
    evidence: 'Governs exits.trail_exit_index — the board’s exit_state/exit_reason. The live tick monitor enforces the trail regardless.',
  },

  // ── Trade Rules — engine-independent execution ────────────────────────────
  stop_mode: {
    key: 'stop_mode',
    label: 'Place stop at',
    help: 'Where the stop-loss lives after a fill.',
    owner: 'execution', applies: 'both', stage: 'stop', rescan: false, home: 'autoRules',
    evidence: 'service.arm_manual_option_buy passes it to protection.arm_position, exactly as the automatic path does.',
  },
  protect_manual_orders: {
    key: 'protect_manual_orders',
    label: 'Add stop when I buy',
    help: 'After your BUY fills, place a stop-loss and watch the position.',
    owner: 'execution', applies: 'manual', stage: 'protection', rescan: false, home: 'manualRules',
    evidence: 'service.place_manual_order / arm_manual_option_buy — consulted only on the hand-placed order path.',
  },
  expiry_square_off_days: {
    key: 'expiry_square_off_days',
    label: 'Exit before expiry',
    help: 'Days before expiry to close. 0 = off.',
    owner: 'execution', applies: 'both', stage: 'exit', rescan: false, home: 'autoRules',
    evidence: 'service._square_off_expiring iterates positions.open_positions — every registered position, hand-placed ones included.',
  },
  time_stop_bars: {
    key: 'time_stop_bars',
    label: 'Max hold time',
    help: 'Hours on the chart before forced exit. 0 = off.',
    owner: 'execution', applies: 'both', stage: 'exit', rescan: false, home: 'autoRules',
    evidence: 'service._time_stop_positions iterates positions.open_positions — every registered position, hand-placed ones included.',
  },
  risk_sizing: {
    key: 'risk_sizing',
    label: 'Size by risk',
    help: 'Size automatic orders so the premium at risk stays within a set share of available capital.',
    owner: 'execution', applies: 'auto', stage: 'size', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb only — the automatic placement path.',
  },
  risk_pct: {
    key: 'risk_pct',
    label: 'Risk % per trade',
    help: 'Percent of available F&O capital risked on one automatic entry.',
    owner: 'execution', applies: 'auto', stage: 'size', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb only — the automatic placement path.',
  },
  max_contract_staleness_bars: {
    key: 'max_contract_staleness_bars',
    label: 'Max contract lag',
    help: 'How many hours a contract’s own last 1H bar may lag the underlying’s and still be auto-executed. 0 means it must be current. The row is shown either way; only the automatic order is held.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'scanner.contract_bar_is_current gates the place_cb call in the derivatives pass. Display and the board are untouched.',
  },
  allow_min_lot_over_risk: {
    key: 'allow_min_lot_over_risk',
    label: 'Allow 1 lot over risk',
    help: 'When even one lot would exceed the risk cap, take it anyway instead of skipping the entry. Off keeps the cap binding.',
    owner: 'execution', applies: 'auto', stage: 'size', rescan: false, home: 'autoRules',
    evidence: 'sizing.size_position / size_future_position return blocked=True and service._make_place_cb returns without ordering. Automatic placement path only.',
  },
  max_lots: {
    key: 'max_lots',
    label: 'Maximum lots',
    help: 'Hard ceiling on the lots one automatic order may place.',
    owner: 'execution', applies: 'auto', stage: 'size', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb / sizing.py, inside the automatic placement path only.',
  },
  adx_min: {
    key: 'adx_min',
    label: 'Minimum ADX',
    help: 'Skip an automatic entry when trend strength is below this. Blank disables it.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb skips an automatic entry only. The board still shows ADX on every row.',
  },
  atr_pct_min: {
    key: 'atr_pct_min',
    label: 'Minimum ATR percentile',
    help: 'Skip an automatic entry when volatility is below this percentile. Blank disables it.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb skips an automatic entry only.',
  },
  block_entry_minutes_before_close: {
    key: 'block_entry_minutes_before_close',
    label: 'Block late entries',
    help: 'Refuse new automatic entries in the last N minutes before 15:30, so a late signal cannot enter straight into an overnight gap. 0 disables it.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb gates the automatic entry only.',
  },
  max_spread_pct: {
    key: 'max_spread_pct',
    label: 'Max spread %',
    help: 'Skip an automatic entry whose bid-ask spread is wider than this share of mid. Blank disables it.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb makes one quote call at automatic entry.',
  },
  min_oi: {
    key: 'min_oi',
    label: 'Min open interest',
    help: 'Skip an automatic entry into a strike thinner than this. Blank disables it.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb makes one quote call at automatic entry.',
  },
  max_daily_loss_pct: {
    key: 'max_daily_loss_pct',
    label: 'Daily loss limit',
    help: 'Halt new automatic entries once the day’s realised losses reach this share of F&O capital. Never force-closes. Blank disables it.',
    owner: 'execution', applies: 'auto', stage: 'guard', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb blocks new automatic entries only. It never force-closes.',
  },
  wire_risk_infra: {
    key: 'wire_risk_infra',
    label: 'Portfolio risk guards',
    help: 'Feed the drawdown circuit breaker and correlation penalty into automatic sizing.',
    owner: 'execution', applies: 'auto', stage: 'guard', rescan: false, home: 'autoRules',
    evidence: 'service._make_place_cb — consulted during automatic sizing only.',
  },
  directional_mode: {
    key: 'directional_mode',
    label: 'Order vehicle',
    help: 'Monetise a signal through a chosen vehicle instead of the default option leg.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'Selects the contract the engine buys; a manual trader picks their own contract.',
  },
  vehicle: {
    key: 'vehicle',
    label: 'What to buy',
    help: 'What an automatic order buys: an option leg, a deep in-the-money option, or an index future.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'Read by the automatic placement callback when choosing the instrument.',
  },
  target_delta: {
    key: 'target_delta',
    label: 'Target delta',
    help: 'Pick the strike nearest this delta. Takes precedence over a fixed depth.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'Consulted when the automatic path resolves a deep-ITM leg.',
  },
  itm_depth: {
    key: 'itm_depth',
    label: 'ITM depth',
    help: 'How many strike steps into the money. Only used when no target delta is set.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'Only consulted when no target delta is set — a target delta overrides it.',
  },
  futures_expiry: {
    key: 'futures_expiry',
    label: 'Futures contract',
    help: 'Which futures series an automatic order trades.',
    owner: 'execution', applies: 'auto', stage: 'entry', rescan: false, home: 'autoRules',
    evidence: 'Read by the automatic placement callback when the vehicle is futures.',
  },
});

export type FieldKey = keyof typeof FIELDS;

/** Look a field up without narrowing to a literal key at every call site. */
export function fieldDef(key: FieldKey): FieldDef {
  return FIELDS[key];
}

/** True when changing this field leaves the current board rows stale. */
export function needsRescan(key: FieldKey): boolean {
  return FIELDS[key].rescan;
}

// ── Option sets ─────────────────────────────────────────────────────────────
// Previously each panel declared its own copy, and they had already diverged —
// scan_source was "Derivatives" in one panel and "Options" in another. One copy
// makes that class of drift impossible rather than merely discouraged.

export const SCAN_SOURCE_OPTIONS: Array<{ value: ScanSource; label: string; hint: string }> = [
  {
    value: 'spot', label: 'Spot',
    hint: 'Read the underlying’s own chart. Option strikes are attached as candidates to buy.',
  },
  {
    value: 'derivatives', label: 'Derivatives',
    hint: 'Read each selected contract’s own premium chart, and buy when that premium turns up.',
  },
  {
    value: 'both', label: 'Both',
    hint: 'Run both scans side by side. Every signal is tagged Spot or DERIV.',
  },
  {
    value: 'confluence', label: 'Confluence',
    hint: 'Strictest: emit a strike only when the underlying fires a fresh entry and that option’s own premium confirms it.',
  },
];

export const EXIT_MODE_OPTIONS: Array<{ value: ExitMode; label: string; hint: string }> = [
  { value: 'one_red', label: '1 Red', hint: 'Any one line turns red — tightest, and the measured best over 7.5 years.' },
  { value: 'two_red', label: '2 Red', hint: 'Any two lines turn red.' },
  { value: 'three_red', label: '3 Red', hint: 'All three lines turn red — a full reversal.' },
  { value: 'three_red_signal', label: '3R + Signal', hint: 'All three red and a fresh counter-arrow — loosest.' },
];

export const TRAIL_OPTIONS: Array<{ value: TrailTarget; label: string; hint: string }> = [
  { value: 'fast', label: 'Tight', hint: 'Follow the fast line. Most out-of-sample robust, and bleeds the least theta.' },
  { value: 'mid', label: 'Balanced', hint: 'Follow the middle line.' },
  { value: 'slow', label: 'Loose', hint: 'Follow the slow line — the widest stop.' },
];

export const STOP_MODE_OPTIONS: Array<{ value: EngineConfigModel['stop_mode']; label: string; hint: string }> = [
  {
    value: 'both', label: 'Zerodha + Sterling',
    hint: 'Stop at Zerodha, and Sterling watches too. Best for live.',
  },
  {
    value: 'broker', label: 'Zerodha',
    hint: 'Stop only at Zerodha. Works if the app is offline.',
  },
  {
    value: 'monitor', label: 'Sterling',
    hint: 'Sterling watches price and exits. Needs the app online.',
  },
];

export const STRIKE_GROUPS: Array<{ label: string; hint: string; values: Moneyness[] }> = [
  { label: 'Deep ITM', hint: 'δ ≈ 0.80+', values: ['ITM5', 'ITM4'] },
  { label: 'ITM', hint: 'δ ≈ 0.60–0.80', values: ['ITM3', 'ITM2', 'ITM1'] },
  { label: 'ATM', hint: 'δ ≈ 0.50', values: ['ATM'] },
  { label: 'OTM', hint: 'δ ≈ 0.30–0.45', values: ['OTM1', 'OTM2'] },
  { label: 'Far OTM', hint: 'δ ≲ 0.25', values: ['OTM3', 'OTM4', 'OTM5'] },
];

export const INDEX_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'NIFTY 50', label: 'NIFTY' },
  { value: 'NIFTY BANK', label: 'BANKNIFTY' },
  { value: 'NIFTY FIN SERVICE', label: 'FINNIFTY' },
  { value: 'SENSEX', label: 'SENSEX' },
];

/** The label shown for a scan source, from the one canonical list. */
export function scanSourceLabel(source: ScanSource): string {
  return SCAN_SOURCE_OPTIONS.find((option) => option.value === source)?.label ?? source;
}

export function exitModeLabel(mode: ExitMode): string {
  return EXIT_MODE_OPTIONS.find((option) => option.value === mode)?.label ?? mode;
}

// ── Section navigation ──────────────────────────────────────────────────────

const LEGACY_SECTIONS: Record<string, SectionId> = {
  // 2026-08-08: the shared market page was dissolved into each engine's own
  // scan settings, and the one Trade Rules page was split into Manual/Automatic.
  market: 'engine',
  rules: 'manualRules',
  // The rail was reorganised on 2026-08-07; keep old deep links and stored
  // preferences pointing somewhere sensible instead of silently resetting.
  sharedScan: 'engine',
  orderSelection: 'autoRules',
  // Older still: the nested tab bar that preceded the rail.
  strategy: 'engine',
  tools: 'markets',
  settings: 'experience',
};

// Must list every SectionId. A missing entry makes `isSectionId` false for it,
// which silently turns `openSettingsSection` into a no-op and stops the section
// from being restored on reload -- 'diagnostics' was absent and had both bugs.
export const SECTION_IDS: SectionId[] = [
  'account', 'truedata', 'diagnostics', 'mode', 'manualRules', 'autoRules', 'engine',
  'navigator', 'adaptiveEdge', 'orbOptions', 'atmPremiumImbalance', 'gammaMove', 'bearToBearish', 'oiWallFlow', 'markets',
  'notifications', 'experience',
  'dataLake',
];

export function isSectionId(value: unknown): value is SectionId {
  return typeof value === 'string' && (SECTION_IDS as string[]).includes(value);
}

/** Resolve a stored or externally supplied section id, following renames. */
export function resolveSectionId(value: string | null | undefined): SectionId | null {
  if (!value) return null;
  if (isSectionId(value)) return value;
  return LEGACY_SECTIONS[value] ?? null;
}

/**
 * Open a settings section from anywhere in the app.
 *
 * There used to be two incompatible channels: in-pane jumps dispatched
 * `kite-connect-section`, while the signal board wrote localStorage and
 * dispatched `kite-nav-click`. The first was a no-op when the pane was
 * unmounted, the second a no-op when it was already mounted. This does both,
 * and validates the id so a typo cannot persist a bad value.
 */
export function openSettingsSection(section: SectionId): void {
  if (!isSectionId(section)) return;
  try {
    localStorage.setItem('kite_connect_section', section);
  } catch {
    // Private-mode storage failure must not stop navigation.
  }
  window.dispatchEvent(new CustomEvent('kite-connect-section', { detail: section }));
  window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: 'connect' }));
}
