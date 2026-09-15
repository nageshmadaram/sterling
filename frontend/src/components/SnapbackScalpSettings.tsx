import React from 'react';
import type { SnapbackConfig } from '../hooks/useSnapback';
import { ChoiceRow, Field, NumberField, Section, Switch } from './kite/kiteSettingsPrimitives';
import { ConfigNote } from './kite/config/ConfigPrimitives';

type NumericKey = {
  [K in keyof SnapbackConfig]-?: NonNullable<SnapbackConfig[K]> extends number ? K : never
}[keyof SnapbackConfig];
type Control = {
  key: NumericKey; label: string; fallback: number; hint: string;
  min: number; max: number; step?: number; suffix?: string;
};

const PLAN: Control[] = [
  { key: 'scalp_target_points', label: 'Requested target', fallback: 5, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'Option premium gain per unit. The effective target may rise to cover costs and the minimum net reward/risk.' },
  { key: 'scalp_stop_points', label: 'Initial stop distance', fallback: 4, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'Option premium loss per unit before costs. A stop is a planned exit; a gap or poor fill can lose more.' },
  { key: 'scalp_trail_points', label: 'Runner trailing distance', fallback: 2, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'After runner activation, trail the best premium by this distance. The stop must only tighten.' },
  { key: 'scalp_lock_points', label: 'Runner profit lock', fallback: 1, min: 0, max: 1000, step: 0.05, suffix: 'pts', hint: 'Desired gain above estimated break-even when a completed strong candle qualifies for a runner. Costs and execution still affect the realised result.' },
  { key: 'scalp_max_hold_bars', label: 'Initial holding limit', fallback: 20, min: 1, max: 375, suffix: 'bars', hint: 'Exit an unextended plan at this age, even if the initial target has not been reached.' },
  { key: 'scalp_runner_max_bars', label: 'Runner holding limit', fallback: 60, min: 1, max: 375, suffix: 'bars', hint: 'Maximum total age including the initial hold. Session square-off takes precedence.' },
];

const RISK: Control[] = [
  { key: 'capital_inr', label: 'Capital', fallback: 100000, min: 1000, max: 1e10, step: 1000, suffix: '₹', hint: 'Account capital in rupees. The plan sizes whole lots within risk and premium budgets.' },
  { key: 'scalp_risk_pct', label: 'Risk per trade', fallback: 0.5, min: 0.01, max: 5, step: 0.05, suffix: '%', hint: 'Budget for the planned stop plus estimated costs. This does not cap losses if execution fails.' },
  { key: 'premium_pct_of_capital', label: 'Premium budget', fallback: 2, min: 0.01, max: 100, step: 0.5, suffix: '%', hint: 'Allocation cap when premium-budget sizing is selected. A single lot that exceeds the budget is skipped.' },
  { key: 'lots', label: 'Fixed lots requested', fallback: 1, min: 1, max: 100, hint: 'Requested cap in fixed-lot mode. Cash and stop-risk budgets can reduce this quantity or reject the plan.' },
  { key: 'max_lots', label: 'Maximum lots', fallback: 5, min: 1, max: 100, hint: 'Quantity increases costs and losses as well as gains; it does not improve the edge.' },
  { key: 'scalp_daily_loss_pct', label: 'Daily loss limit', fallback: 2, min: 0.01, max: 20, step: 0.1, suffix: '%', hint: 'Replay and plan risk limit. New entries stop once the daily loss budget is spent.' },
  { key: 'scalp_max_trades_per_day', label: 'Daily trade limit', fallback: 6, min: 1, max: 100, hint: 'Bounds repeat entries in the replay. A scan alone is not an account-wide trade counter.' },
  { key: 'scalp_cooldown_bars', label: 'Entry cooldown', fallback: 5, min: 0, max: 375, suffix: 'bars', hint: 'Completed bars to wait before another entry in the same instrument.' },
  { key: 'scalp_round_trip_cost_points', label: 'Variable round-trip costs', fallback: 1, min: 0, max: 1000, step: 0.05, suffix: 'pts', hint: 'All-in spread, variable fees and slippage per option unit across entry and exit. The quoted spread is also checked.' },
  { key: 'scalp_fixed_cost_inr', label: 'Fixed round-trip costs', fallback: 40, min: 0, max: 100000, step: 1, suffix: '₹', hint: 'Fixed rupee costs per completed trade, additional to variable points. Small trades must recover both.' },
  { key: 'scalp_min_net_rr', label: 'Minimum net reward/risk', fallback: 1, min: 0.1, max: 10, step: 0.1, suffix: 'x', hint: 'Target gain after estimated costs divided by stop loss including estimated costs. Larger quantity cannot repair a negative per-unit edge.' },
];

const FILTERS: Control[] = [
  { key: 'scalp_max_adx', label: 'Maximum entry ADX', fallback: 25, min: 1, max: 100, step: 1, hint: 'Avoid fading an established strong trend. This is an entry filter, not proof the next trade will win.' },
  { key: 'scalp_min_relative_volume', label: 'Minimum relative volume', fallback: 0, min: 0, max: 20, step: 0.1, suffix: 'x', hint: 'Optional participation filter; 0 disables it. Index volume can be unavailable.' },
  { key: 'target_delta', label: 'Target delta', fallback: 0.7, min: 0.05, max: 0.95, step: 0.05, hint: 'Absolute option delta for contract selection. Swing calibration does not validate this intraday choice.' },
  { key: 'min_dte', label: 'Minimum days to expiry', fallback: 40, min: 1, max: 365, suffix: 'd', hint: 'Contract expiry filter. This is independent of the minute-bar holding limit.' },
  { key: 'max_dte', label: 'Maximum days to expiry', fallback: 60, min: 1, max: 365, suffix: 'd', hint: 'Upper expiry bound. Keep this at or above the minimum expiry.' },
  { key: 'min_option_premium', label: 'Minimum premium', fallback: 10, min: 0, max: 10000, suffix: '₹', hint: 'Exclude very low premiums where tick rounding can dominate the move.' },
  { key: 'max_spread_pct', label: 'Maximum quoted spread', fallback: 2, min: 0, max: 100, step: 0.1, suffix: '%', hint: 'Liquidity gate on the selected option. Even an eligible spread must fit the net target economics.' },
  { key: 'min_option_oi', label: 'Minimum option open interest', fallback: 0, min: 0, max: 1e9, step: 100, hint: 'Liquidity filter only. Open interest by itself does not predict trade direction.' },
];

const timeLabel = (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;

export function SnapbackScalpSettings({ cfg, defaults, patch }: {
  cfg: SnapbackConfig; defaults: SnapbackConfig; patch: (next: Partial<SnapbackConfig>) => void;
}) {
  const controls = (items: Control[]) => items.map(({ key, fallback, ...props }) => (
    <NumberField key={key} {...props} value={cfg[key] ?? defaults[key] ?? fallback}
      disabled={(key === 'premium_pct_of_capital' && cfg.sizing_mode !== 'PREMIUM_PCT')
        || (key === 'lots' && cfg.sizing_mode !== 'LOTS')}
      defaultValue={defaults[key]} onChange={(value) => patch({ [key]: value })} />
  ));
  const times = [
    ['scalp_entry_start_minute', 'Entry start', 575],
    ['scalp_entry_end_minute', 'Last entry', 885],
    ['scalp_square_off_minute', 'Square-off', 915],
  ] as const;
  return <>
    <ConfigNote><span>
      <strong>Research plans only.</strong> Daily swing results do not validate scalp or intraday trading.
      All target, stop and trail points refer to <strong>option premium</strong>, not the underlying index.
      Small targets can lose money after costs; no entry is guaranteed profitable.
      Runner upgrades and trailing stops are simulated plans here; this mode does not place or manage broker orders.
    </span></ConfigNote>
    <Section title="Target, runner and timing" description="Start with a small target; a qualifying move can extend into a runner with a tighter stop."
      summary="Premium points and completed bars" defaultOpen persistKey="snapback-scalp-plan">
      <Field label="Candle interval" hint="Use completed candles. Scalp starts at 1 minute; intraday starts at 5 minutes.">
        <ChoiceRow value={String(cfg.scalp_timeframe_minutes ?? defaults.scalp_timeframe_minutes ?? 1)}
          options={['1', '3', '5'].map((value) => ({ value, label: `${value} min` }))}
          onChange={(value) => patch({ scalp_timeframe_minutes: Number(value) })} />
      </Field>
      {controls(PLAN)}
      {times.map(([key, label, fallback]) => <Field key={key} label={`${label} (IST)`} hint="Start must precede last entry, then square-off; all times are exchange-local.">
        <input type="time" aria-label={`${label} (IST)`} min="09:15" max="15:25" value={timeLabel(cfg[key] ?? defaults[key] ?? fallback)}
          onChange={(e) => {
            if (!e.target.value) return;
            const [hour, minute] = e.target.value.split(':').map(Number);
            patch({ [key]: hour * 60 + minute });
          }} />
      </Field>)}
    </Section>
    <Section title="Risk and trading costs" description="Skip unaffordable lots and price the target after costs."
      summary="Per-trade budget and daily limits" defaultOpen persistKey="snapback-scalp-risk">
      <Field label="Sizing" hint="Both choices remain constrained by cash, maximum lots and the estimated stop-risk budget.">
        <ChoiceRow value={cfg.sizing_mode} options={[
          { value: 'PREMIUM_PCT', label: 'Premium budget' }, { value: 'LOTS', label: 'Fixed lots' },
        ]} onChange={(sizing_mode) => patch({ sizing_mode })} />
      </Field>
      {controls(RISK)}
    </Section>
    <Section title="Entry and contract filters" description="Reversal and trend filters select candidates; liquidity determines whether the premium plan is usable."
      summary="ADX, volume, delta and liquidity" persistKey="snapback-scalp-filters">
      {controls(FILTERS)}
      <Field label="Fade weakness too" hint="Allow bullish call plans after downside exhaustion as well as bearish put plans after upside exhaustion.">
        <Switch checked={cfg.allow_fade_down} label={cfg.allow_fade_down ? 'Both sides' : 'Puts into strength only'}
          onChange={() => patch({ allow_fade_down: !cfg.allow_fade_down })} />
      </Field>
    </Section>
    <Section title="Ablation indicator experiments" description="Test each indicator addition independently against the frozen baseline on chronological validation datasets. All disabled by default."
      summary="EMA9, ADX, PCR, OI, VWAP toggles" persistKey="snapback-scalp-experiments">
      <Field label="EMA9 reversal confirmation" hint="Require candle close to confirm direction across EMA9 before entry.">
        <Switch checked={!!cfg.use_ema_confirmation} label={cfg.use_ema_confirmation ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_ema_confirmation: !cfg.use_ema_confirmation })} />
      </Field>
      <Field label="ADX14 trend filter" hint="Refuse entries when ADX exceeds max ADX threshold (avoids strong trends).">
        <Switch checked={!!cfg.use_adx_filter} label={cfg.use_adx_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_adx_filter: !cfg.use_adx_filter })} />
      </Field>
      <Field label="PCR option chain filter" hint="Optional synchronized put/call ratio threshold check.">
        <Switch checked={!!cfg.use_pcr_filter} label={cfg.use_pcr_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_pcr_filter: !cfg.use_pcr_filter })} />
      </Field>
      <Field label="OI change filter" hint="Optional contract open interest change filter.">
        <Switch checked={!!cfg.use_oi_filter} label={cfg.use_oi_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_oi_filter: !cfg.use_oi_filter })} />
      </Field>
      <Field label="VWAP context filter" hint="Optional Volume Weighted Average Price context check.">
        <Switch checked={!!cfg.use_vwap_filter} label={cfg.use_vwap_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_vwap_filter: !cfg.use_vwap_filter })} />
      </Field>
    </Section>
  </>;
}
