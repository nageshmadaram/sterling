import React from 'react';
import {
  useIntradayConfig, useUpdateIntraday,
  type IntradayConfig, type PivotPeriod, type PivotType,
  type SizingMode, type StopMode, type StopSource, type Timeframe,
  type TrailMode,
} from '../hooks/useIntraday';
import {
  ChoiceRow, Field, NumberField, Section, Switch, DIM,
} from './kite/kiteSettingsPrimitives';
import { AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar } from './kite/config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { InstrumentsGroup } from './kite/config/ScanSettings';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';

/**
 * Intraday pack settings — three strategies, one page.
 *
 * Same order as the other engine panels (power → universe → session → each
 * strategy → contracts → advanced) so the settings hub reads as one product.
 *
 * What this panel does that Gamma Move's does not: it says **nothing here was
 * measured**. Gamma Move publishes a calibration note beside seven of its
 * numbers; this pack publishes an empty `calibrated_fields`, and the honest
 * rendering of that is to say so once, loudly, rather than let a reader assume
 * the defaults came from a study.
 *
 * Each strategy's own switch lives in its own section. Turning one off removes
 * it from the scan and from the board's filter row, and the other two are
 * unaffected — which is the whole reason these three share one config object
 * rather than three.
 */
const TIMEFRAME_OPTIONS: Array<{ value: Timeframe; label: string; hint: string }> = [
  { value: '5m', label: '5 min', hint: 'What all three strategies were specified on.' },
  { value: '3m', label: '3 min', hint: 'Faster and noisier. The body and spread filters were chosen for 5m.' },
  { value: '15m', label: '15 min', hint: 'Slower. Fewer signals, later entries, wider stops.' },
];

const PIVOT_TYPE_OPTIONS: Array<{ value: PivotType; label: string; hint: string }> = [
  { value: 'fibonacci', label: 'Fibonacci', hint: 'Bands at 0.382 / 0.618 / 1.000 of the prior range. The specified type.' },
  { value: 'classic', label: 'Classic', hint: 'The conventional bands. Same pivot, wider R1/S1.' },
];

const PIVOT_PERIOD_OPTIONS: Array<{ value: PivotPeriod; label: string; hint: string }> = [
  { value: 'day', label: 'Daily', hint: 'Levels from the prior session. Standard for an intraday break.' },
  { value: 'week', label: 'Weekly', hint: 'Levels from the prior week. Far fewer, far heavier.' },
];

const TRAIL_OPTIONS: Array<{ value: TrailMode; label: string; hint: string }> = [
  { value: 'structure', label: 'Structure', hint: 'The stop follows the swing that made the move. Tightest that still respects the chart.' },
  { value: 'atr', label: 'ATR', hint: 'The stop follows the peak by a multiple of ATR. Volatility-aware, structure-blind.' },
  { value: 'breakeven', label: 'Breakeven only', hint: 'Stop to entry at 1R, then nothing. The runner keeps its whole range.' },
  { value: 'none', label: 'None', hint: 'The stop never moves. Every trade is a full 1R risk to the end.' },
];

const STOP_SOURCE_OPTIONS: Array<{ value: StopSource; label: string; hint: string }> = [
  { value: 'vwap', label: 'VWAP', hint: 'The specified stop. Where the trade is wrong is where price reclaims VWAP.' },
  { value: 'supertrend', label: 'SuperTrend', hint: 'The band itself. Usually further away, so a 20-point target pays for less of it.' },
  { value: 'wider', label: 'Whichever is wider', hint: 'The safer of the two. Fewer stop-outs, worse reward to risk.' },
];

const SIZING_OPTIONS: Array<{ value: SizingMode; label: string; hint: string }> = [
  { value: 'RISK_PCT', label: 'Risk percent', hint: 'Size so the distance to the stop costs a set share of capital. A signal that cannot be sized inside the cap is SKIPPED, not squeezed in at one lot.' },
  { value: 'LOTS', label: 'Fixed lots', hint: 'The same number of lots every time, however far the stop is.' },
];

const STOP_MODE_OPTIONS: Array<{ value: StopMode; label: string; hint: string }> = [
  { value: 'both', label: 'Broker + monitor', hint: 'A GTT at Zerodha that survives this process dying, plus our own tick loop for intrabar exits. The production answer.' },
  { value: 'broker', label: 'Broker only', hint: 'A GTT and nothing else. Survives a crash, but only exits on a completed trigger.' },
  { value: 'monitor', label: 'Monitor only', hint: 'Our tick loop and nothing at the broker. If this process dies while holding, the position is unprotected.' },
];

const ADVANCED_SETTING_COUNT = 8;

export function IntradaySettings() {
  const { data, isLoading } = useIntradayConfig();
  const setCfg = useUpdateIntraday();

  const server = data?.config;
  const defaults = data?.defaults;
  const strategy = data?.strategy;
  const warnings = data?.warnings ?? [];

  const [draft, setDraft] = React.useState<IntradayConfig | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const cfg = draft ?? server ?? null;
  const dirty = draft != null && server != null
    && (Object.keys(draft) as (keyof IntradayConfig)[])
      .some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));

  useUnsavedDraftGuard('intraday', dirty);

  const patch = React.useCallback((next: Partial<IntradayConfig>) => {
    setDraft((prev) => ({ ...(prev ?? server!), ...next }));
  }, [server]);

  const handleApply = React.useCallback(() => {
    if (draft) setCfg.mutate(draft, { onSuccess: () => setDraft(null) });
  }, [draft, setCfg]);
  const handleDiscard = React.useCallback(() => setDraft(null), []);
  const handleReset = React.useCallback(() => {
    if (!resetConfirm) { setResetConfirm(true); return; }
    if (defaults) setDraft({ ...defaults });
    setResetConfirm(false);
  }, [defaults, resetConfirm]);

  if (isLoading || !cfg || !server || !defaults || !strategy) {
    return <PanelCard><div style={{ color: DIM, fontSize: 12 }}>Loading strategy settings…</div></PanelCard>;
  }

  const meta = (id: string) => strategy.strategies.find((s) => s.id === id);
  /** Whether single stocks are scanned at all. Derived from the selection
   *  rather than stored twice — a second field for the same fact is how a
   *  setting ends up shown in one place and honoured in another. */
  const stockContracts = cfg.scan_all_stocks || cfg.scan_stocks.length > 0;

  return (
    <>
      <EnginePowerHeader
        name={strategy.name}
        tagline={strategy.tagline}
        on={cfg.enabled}
        liveOn={server.enabled}
        busy={setCfg.isPending}
        onToggle={() => patch({ enabled: !cfg.enabled })}
        runningNote="Scanning 5-minute candles for pivot breaks, ribbon crosses and VWAP/SuperTrend flips."
        offNote="Off. None of the three scans and no signals are produced."
      />

      <ConfigNote>
        <span>
          <strong>Nothing here was measured.</strong> No walk-forward run has been done
          on any of these three, so every threshold below is a judgement call — not a
          calibrated value. Whether to trade an unproven edge is your call; paper or
          live is the account&rsquo;s Trading Mode setting, not this page&rsquo;s.
        </span>
      </ConfigNote>
      <ConfigNote>
        <span>
          All levels these strategies produce are in the <strong>underlying&rsquo;s
          points</strong>, not premium. A 5-minute stop read as an hourly one is a
          trade sized several times too large.
        </span>
      </ConfigNote>
      {warnings.map((w) => <ConfigNote key={w}><span>{w}</span></ConfigNote>)}

      <Section
        title="Universe"
        description="What the three strategies scan."
        summary={`${cfg.scan_indices.length} indices · ${cfg.scan_all_stocks ? 'all stocks' : `${cfg.scan_stocks.length} stocks`}`}
        persistKey="intraday-universe"
        defaultOpen
      >
        <Field label="Candle interval" hint="Every rule is evaluated on a CLOSE of this bar." wide>
          <ChoiceRow
            value={cfg.timeframe}
            options={TIMEFRAME_OPTIONS}
            onChange={(timeframe) => patch({ timeframe })}
          />
        </Field>
        <InstrumentsGroup
          idPrefix="intraday"
          indices={cfg.scan_indices}
          stocks={cfg.scan_stocks}
          allStocks={cfg.scan_all_stocks}
          stockContracts={stockContracts}
          allowEmptyIndices
          onChange={(next) => patch({
            ...(next.scan_indices !== undefined ? { scan_indices: next.scan_indices } : {}),
            ...(next.scan_stocks !== undefined ? { scan_stocks: next.scan_stocks } : {}),
            ...(next.scan_all_stocks !== undefined ? { scan_all_stocks: next.scan_all_stocks } : {}),
            // "Indices only" is expressed by having no stocks selected, not by a
            // separate flag this engine would then have to keep in step.
            ...(next.scan_stock_contracts === false
              ? { scan_stocks: [], scan_all_stocks: false } : {}),
          })}
        />
      </Section>

      <Section
        title="Session"
        description="When a new entry may be taken, and how often."
        summary={`${cfg.session_start}–${cfg.no_entry_after} · max ${cfg.max_signals_per_symbol_per_day}/symbol`}
        persistKey="intraday-session"
      >
        <Field
          label="No entries before"
          hint="The opening auction. A pivot break and a ribbon cross are both noise in it."
        >
          <input
            type="time"
            value={cfg.session_start}
            onChange={(e) => patch({ session_start: e.target.value })}
            style={{ background: 'transparent', color: 'inherit', border: '1px solid var(--k-border)', borderRadius: 2, padding: '3px 6px', fontSize: 11.5 }}
          />
        </Field>
        <Field
          label="No new entries after"
          hint="Exits are never gated by this — only new positions."
        >
          <input
            type="time"
            value={cfg.no_entry_after}
            onChange={(e) => patch({ no_entry_after: e.target.value })}
            style={{ background: 'transparent', color: 'inherit', border: '1px solid var(--k-border)', borderRadius: 2, padding: '3px 6px', fontSize: 11.5 }}
          />
        </Field>
        <NumberField
          label="Cooldown"
          hint="Bars a symbol waits before the SAME strategy may fire on it again. Without one, a break that oscillates across its level fires on every recross."
          value={cfg.cooldown_bars}
          defaultValue={defaults.cooldown_bars}
          onChange={(cooldown_bars) => patch({ cooldown_bars })}
          min={0} max={60} suffix="bars"
        />
        <NumberField
          label="Signals per symbol per day"
          hint="A hard cap, counted per strategy."
          value={cfg.max_signals_per_symbol_per_day}
          defaultValue={defaults.max_signals_per_symbol_per_day}
          onChange={(v) => patch({ max_signals_per_symbol_per_day: v })}
          min={1} max={20}
        />
      </Section>

      {/* ── 1. Pivot Break ─────────────────────────────────────────────── */}
      <Section
        title="Pivot Break"
        description={meta('pivot_break')?.how_it_works ?? ''}
        summary={cfg.pb_enabled ? `EMA${cfg.pb_ema_length} · ${cfg.pb_pivot_type} · 1:${cfg.pb_target_r}/1:${cfg.pb_target2_r}` : 'Off'}
        persistKey="intraday-pb"
      >
        <Field label="Strategy" hint="Off removes it from the scan and from the board.">
          <Switch
            checked={cfg.pb_enabled}
            label="Pivot Break"
            onChange={() => patch({ pb_enabled: !cfg.pb_enabled })}
          />
        </Field>
        <NumberField
          label="EMA length" hint="The moving average the candle must close through."
          value={cfg.pb_ema_length} defaultValue={defaults.pb_ema_length}
          onChange={(pb_ema_length) => patch({ pb_ema_length })} min={2} max={200}
        />
        <Field label="Pivot type" hint="Standard pivots; the bands differ." wide>
          <ChoiceRow value={cfg.pb_pivot_type} options={PIVOT_TYPE_OPTIONS}
            onChange={(pb_pivot_type) => patch({ pb_pivot_type })} />
        </Field>
        <Field label="Pivot period" hint="Which prior period the levels come from." wide>
          <ChoiceRow value={cfg.pb_pivot_period} options={PIVOT_PERIOD_OPTIONS}
            onChange={(pb_pivot_period) => patch({ pb_pivot_period })} />
        </Field>
        <NumberField
          label="Minimum body"
          hint="Body as a share of the candle's whole range. This is what makes 'strong' testable — a doji that pokes through a pivot and closes above it is a break on the letter of the rule and a coin flip in fact."
          value={cfg.pb_min_body_pct} defaultValue={defaults.pb_min_body_pct}
          onChange={(pb_min_body_pct) => patch({ pb_min_body_pct })}
          min={0} max={100} step={5} suffix="% of range"
        />
        <NumberField
          label="Minimum body vs ATR"
          hint="And strong relative to what this instrument has been doing. Body-percent alone passes a tiny bar in a dead tape."
          value={cfg.pb_min_body_atr} defaultValue={defaults.pb_min_body_atr}
          onChange={(pb_min_body_atr) => patch({ pb_min_body_atr })}
          min={0} max={5} step={0.1} suffix="× ATR"
        />
        <Field
          label="Fresh breaks only"
          hint="The prior bar must have closed on the other side. Without this every later bar above the level is also a 'break', and the engine re-enters an old move at a worse price."
        >
          <Switch
            checked={cfg.pb_require_fresh_break}
            label="Fresh breaks only"
            onChange={() => patch({ pb_require_fresh_break: !cfg.pb_require_fresh_break })}
          />
        </Field>
        <NumberField
          label="First target" hint="Multiple of the risk — the distance to the signal candle's own low or high."
          value={cfg.pb_target_r} defaultValue={defaults.pb_target_r}
          onChange={(pb_target_r) => patch({ pb_target_r })} min={0.5} max={10} step={0.5} suffix="R"
        />
        <NumberField
          label="Runner target" hint="Taken after the first target banks and the stop moves to entry."
          value={cfg.pb_target2_r} defaultValue={defaults.pb_target2_r}
          onChange={(pb_target2_r) => patch({ pb_target2_r })} min={1} max={15} step={0.5} suffix="R"
        />
        <Field label="Trail" hint="How the stop follows a working trade." wide>
          <ChoiceRow value={cfg.pb_trail_mode} options={TRAIL_OPTIONS}
            onChange={(pb_trail_mode) => patch({ pb_trail_mode })} />
        </Field>
        <NumberField
          label="Maximum stop"
          hint="Reject a setup whose own candle is so wide the stop is unusable. A filter, not a stop override: shrinking the stop to fit would make the engine claim a risk it is not taking."
          value={cfg.pb_max_stop_pct} defaultValue={defaults.pb_max_stop_pct}
          onChange={(pb_max_stop_pct) => patch({ pb_max_stop_pct })}
          min={0.1} max={10} step={0.1} suffix="% of price"
        />
      </Section>

      {/* ── 2. MA Ribbon ───────────────────────────────────────────────── */}
      <Section
        title="MA Ribbon"
        description={meta('ma_ribbon')?.how_it_works ?? ''}
        summary={cfg.rb_enabled
          ? `${cfg.rb_ema_fast}/${cfg.rb_ema_1}/${cfg.rb_ema_2}/${cfg.rb_ema_slow}`
          : 'Off'}
        persistKey="intraday-rb"
      >
        <Field label="Strategy" hint="Off removes it from the scan and from the board.">
          <Switch checked={cfg.rb_enabled} label="MA Ribbon"
            onChange={() => patch({ rb_enabled: !cfg.rb_enabled })} />
        </Field>
        <NumberField label="Fast EMA" hint="The blue line."
          value={cfg.rb_ema_fast} defaultValue={defaults.rb_ema_fast}
          onChange={(rb_ema_fast) => patch({ rb_ema_fast })} min={2} max={100} />
        <NumberField label="Second EMA" hint="The green line."
          value={cfg.rb_ema_1} defaultValue={defaults.rb_ema_1}
          onChange={(rb_ema_1) => patch({ rb_ema_1 })} min={3} max={150} />
        <NumberField label="Third EMA" hint="The yellow line."
          value={cfg.rb_ema_2} defaultValue={defaults.rb_ema_2}
          onChange={(rb_ema_2) => patch({ rb_ema_2 })} min={4} max={200} />
        <NumberField label="Slow EMA" hint="The red line — the one whose position against the other three IS the signal."
          value={cfg.rb_ema_slow} defaultValue={defaults.rb_ema_slow}
          onChange={(rb_ema_slow) => patch({ rb_ema_slow })} min={5} max={400} />
        <Field
          label="Full cross only"
          hint="The slow line must clear EVERY other line. A cross of one line is explicitly not a signal — this is the rule most implementations of this strategy get wrong."
        >
          <Switch checked={cfg.rb_require_full_cross} label="Full cross only"
            onChange={() => patch({ rb_require_full_cross: !cfg.rb_require_full_cross })} />
        </Field>
        <NumberField
          label="Minimum ribbon spread"
          hint="Outermost lines apart, as a share of price. In a flat tape the four EMAs braid and a 'full cross' happens several times an hour with no trend behind it."
          value={cfg.rb_min_spread_pct} defaultValue={defaults.rb_min_spread_pct}
          onChange={(rb_min_spread_pct) => patch({ rb_min_spread_pct })}
          min={0} max={5} step={0.01} suffix="% of price"
        />
        <NumberField
          label="Confirm bars" hint="Closes the full cross must hold for before it counts."
          value={cfg.rb_confirm_bars} defaultValue={defaults.rb_confirm_bars}
          onChange={(rb_confirm_bars) => patch({ rb_confirm_bars })} min={1} max={10} suffix="bars"
        />
        <NumberField
          label="Stop distance from the slow line"
          hint="The stated strategy has no price stop at all — it holds until the opposite cross, which can be days. This adds one. Zero puts the stop exactly on the slow line."
          value={cfg.rb_stop_atr_mult} defaultValue={defaults.rb_stop_atr_mult}
          onChange={(rb_stop_atr_mult) => patch({ rb_stop_atr_mult })}
          min={0} max={6} step={0.1} suffix="× ATR"
        />
      </Section>

      {/* ── 3. VWAP SuperTrend ─────────────────────────────────────────── */}
      <Section
        title="VWAP SuperTrend"
        description={meta('vwap_supertrend')?.how_it_works ?? ''}
        summary={cfg.vs_enabled
          ? `ST(${cfg.vs_atr_length}, ${cfg.vs_factor}) · ${cfg.vs_target_points} pts`
          : 'Off'}
        persistKey="intraday-vs"
      >
        <Field label="Strategy" hint="Off removes it from the scan and from the board.">
          <Switch checked={cfg.vs_enabled} label="VWAP SuperTrend"
            onChange={() => patch({ vs_enabled: !cfg.vs_enabled })} />
        </Field>
        <NumberField label="SuperTrend ATR length" hint="The specified length."
          value={cfg.vs_atr_length} defaultValue={defaults.vs_atr_length}
          onChange={(vs_atr_length) => patch({ vs_atr_length })} min={2} max={100} />
        <NumberField label="SuperTrend factor" hint="The specified multiplier."
          value={cfg.vs_factor} defaultValue={defaults.vs_factor}
          onChange={(vs_factor) => patch({ vs_factor })} min={0.1} max={10} step={0.01} />
        <NumberField
          label="Target" hint="A fixed objective in the underlying's points, as specified."
          value={cfg.vs_target_points} defaultValue={defaults.vs_target_points}
          onChange={(vs_target_points) => patch({ vs_target_points })}
          min={1} max={500} step={1} suffix="points"
        />
        <Field label="Stop" hint="Where this trade is wrong." wide>
          <ChoiceRow value={cfg.vs_stop_source} options={STOP_SOURCE_OPTIONS}
            onChange={(vs_stop_source) => patch({ vs_stop_source })} />
        </Field>
        <NumberField
          label="Maximum stop distance"
          hint="Beyond this the setup is SKIPPED rather than resized: a VWAP stop is whatever distance VWAP happens to be, and on a wide bar that is a 1:0.2 trade."
          value={cfg.vs_max_stop_points} defaultValue={defaults.vs_max_stop_points}
          onChange={(vs_max_stop_points) => patch({ vs_max_stop_points })}
          min={1} max={1000} step={1} suffix="points"
        />
        <NumberField
          label="Trail after"
          hint="Once this much is banked the stop follows VWAP instead of sitting at the entry VWAP."
          value={cfg.vs_trail_after_points} defaultValue={defaults.vs_trail_after_points}
          onChange={(vs_trail_after_points) => patch({ vs_trail_after_points })}
          min={0} max={500} step={1} suffix="points"
        />
        <Field
          label="Require real volume"
          hint="Kite reports no volume on index spot, and a zero-weight VWAP collapses onto the typical price — which makes 'closed below VWAP' true or false essentially at random. This exact failure silenced an earlier engine here for its whole life."
        >
          <Switch checked={cfg.vs_require_volume_vwap} label="Require real volume"
            onChange={() => patch({ vs_require_volume_vwap: !cfg.vs_require_volume_vwap })} />
        </Field>
      </Section>

      <Section
        title="Size"
        description="How much of the account one signal is allowed to cost."
        summary={cfg.sizing_mode === 'LOTS'
          ? `${cfg.lots} lot(s), max ${cfg.max_lots}`
          : `${cfg.risk_per_trade_pct}% of ₹${cfg.capital_inr.toLocaleString('en-IN')}`}
        persistKey="intraday-size"
      >
        <Field label="Sizing" hint="Which number decides how many lots." wide>
          <ChoiceRow
            value={cfg.sizing_mode}
            options={SIZING_OPTIONS}
            onChange={(sizing_mode) => patch({ sizing_mode })}
          />
        </Field>
        {cfg.sizing_mode === 'RISK_PCT' ? (
          <>
            <NumberField
              label="Risk per trade"
              hint="Of capital, against the distance from entry to the premium stop."
              value={cfg.risk_per_trade_pct} defaultValue={defaults.risk_per_trade_pct}
              onChange={(risk_per_trade_pct) => patch({ risk_per_trade_pct })}
              min={0.1} max={20} step={0.1} suffix="%"
            />
            <NumberField
              label="Capital"
              hint="What the risk percentage is a percentage OF. This engine's own figure — it does not read the account balance, so a number you have not updated is a number you are still sizing against."
              value={cfg.capital_inr} defaultValue={defaults.capital_inr}
              onChange={(capital_inr) => patch({ capital_inr })}
              min={1000} max={100000000} step={10000} suffix="₹"
            />
          </>
        ) : (
          <NumberField
            label="Lots per trade" hint="Exchange lots, every time."
            value={cfg.lots} defaultValue={defaults.lots}
            onChange={(lots) => patch({ lots })} min={1} max={100}
          />
        )}
        <NumberField
          label="Maximum lots"
          hint="The ceiling whichever sizing mode is on. Measured elsewhere in this repo: max_lots is the real sizer and the risk percentage is a drawdown dial — they do different jobs, which is why both are here."
          value={cfg.max_lots} defaultValue={defaults.max_lots}
          onChange={(max_lots) => patch({ max_lots })} min={1} max={200}
        />
        <Field
          label="Take one lot over the risk cap"
          hint="Off: a signal that cannot be sized inside the cap is skipped. On: it is taken at the minimum size anyway, which turns the risk percentage into a suggestion."
        >
          <Switch
            checked={cfg.allow_min_lot_over_risk}
            label="Take one lot over the risk cap"
            onChange={() => patch({ allow_min_lot_over_risk: !cfg.allow_min_lot_over_risk })}
          />
        </Field>
      </Section>

      <Section
        title="Protection"
        description="Where the stop lives, and how it follows a working trade."
        summary={`${cfg.stop_mode} · trail ${cfg.premium_trail_pct}%`}
        persistKey="intraday-protection"
        defaultOpen
      >
        <Field label="Stop lives" hint="A stop that only exists in this process is not protection." wide>
          <ChoiceRow
            value={cfg.stop_mode}
            options={STOP_MODE_OPTIONS}
            onChange={(stop_mode) => patch({ stop_mode })}
          />
        </Field>
        <NumberField
          label="Premium stop"
          hint="How far below the premium paid the stop sits when no delta is quoted. A spot stop of 20 points is NOT a premium stop of 20 points — an option does not move one for one with its underlying."
          value={cfg.premium_stop_pct} defaultValue={defaults.premium_stop_pct}
          onChange={(premium_stop_pct) => patch({ premium_stop_pct })}
          min={1} max={90} step={1} suffix="% below entry"
        />
        <NumberField
          label="Trailing stop"
          hint="Give back at most this much of the best premium seen. This is the trail that actually protects a bought option: the spot rule can still be intact while the premium has round-tripped, and that gap is where an open drawdown builds."
          value={cfg.premium_trail_pct} defaultValue={defaults.premium_trail_pct}
          onChange={(premium_trail_pct) => patch({ premium_trail_pct })}
          min={1} max={90} step={1} suffix="% off the peak"
        />
        <NumberField
          label="Trail starts at"
          hint="R banked before the stop moves to what was paid and the ratchet starts. Below this the trade has not earned a tighter stop."
          value={cfg.trail_activate_r} defaultValue={defaults.trail_activate_r}
          onChange={(trail_activate_r) => patch({ trail_activate_r })}
          min={0.1} max={5} step={0.1} suffix="R"
        />
        <Field
          label="Flatten at the close"
          hint="An intraday strategy holding overnight is a different strategy, with gap risk none of these three was written for."
        >
          <Switch
            checked={cfg.close_at_session_end}
            label="Flatten at the close"
            onChange={() => patch({ close_at_session_end: !cfg.close_at_session_end })}
          />
        </Field>
      </Section>

      <Section
        title="Dynamic stops and targets"
        description="What volatility is allowed to change about a level."
        summary={cfg.dynamic_stops || cfg.dynamic_targets
          ? `floor ${cfg.stop_atr_floor_mult}×ATR · target ${cfg.target_atr_mult}×ATR`
          : 'Off'}
        persistKey="intraday-dynamic"
      >
        <ConfigNote>
          <span>
            Both adjustments move a level <strong>away</strong> from entry and never
            towards it. Tightening a rule&rsquo;s own stop would be trading a different
            strategy than the one on the board.
          </span>
        </ConfigNote>
        <Field
          label="Widen a stop inside the noise"
          hint="A structural stop — a candle's low, the slow EMA, VWAP — can land two ticks from the close on a quiet bar. That is not a stop, it is a fee."
        >
          <Switch
            checked={cfg.dynamic_stops}
            label="Widen a stop inside the noise"
            onChange={() => patch({ dynamic_stops: !cfg.dynamic_stops })}
          />
        </Field>
        <NumberField
          label="Minimum stop distance"
          hint="The floor, in ATR. A stop nearer than this is pushed out to it."
          value={cfg.stop_atr_floor_mult} defaultValue={defaults.stop_atr_floor_mult}
          onChange={(stop_atr_floor_mult) => patch({ stop_atr_floor_mult })}
          min={0} max={3} step={0.1} suffix="× ATR"
        />
        <Field
          label="Extend a target volatility has overtaken"
          hint="A fixed target can sit inside one bar's range on a fast day, which turns a trend strategy into a scalp without anyone deciding to."
        >
          <Switch
            checked={cfg.dynamic_targets}
            label="Extend a target volatility has overtaken"
            onChange={() => patch({ dynamic_targets: !cfg.dynamic_targets })}
          />
        </Field>
        <NumberField
          label="Minimum target distance"
          hint="In ATR. A target nearer than this is lifted to it, never cut to it. 0 disables."
          value={cfg.target_atr_mult} defaultValue={defaults.target_atr_mult}
          onChange={(target_atr_mult) => patch({ target_atr_mult })}
          min={0} max={10} step={0.5} suffix="× ATR"
        />
      </Section>

      <Section
        title="Limits"
        description="When this engine stops opening positions."
        summary={`${cfg.max_concurrent_positions} open · ${cfg.max_new_trades_per_day}/day`}
        persistKey="intraday-limits"
      >
        <NumberField
          label="Concurrent positions" hint="Across all three strategies."
          value={cfg.max_concurrent_positions} defaultValue={defaults.max_concurrent_positions}
          onChange={(v) => patch({ max_concurrent_positions: v })} min={1} max={50}
        />
        <NumberField
          label="New trades per day" hint="A hard cap on entries, counted across the pack."
          value={cfg.max_new_trades_per_day} defaultValue={defaults.max_new_trades_per_day}
          onChange={(v) => patch({ max_new_trades_per_day: v })} min={1} max={100}
        />
        <NumberField
          label="Daily loss limit"
          hint="This engine's OWN stop for the day, on its own realised P&L. Separate from the account-wide breaker on purpose: without it, one strategy's bad morning is funded by another's good one until the account-wide number finally trips. 0 = off."
          value={cfg.daily_loss_limit_inr} defaultValue={defaults.daily_loss_limit_inr}
          onChange={(v) => patch({ daily_loss_limit_inr: v })}
          min={0} max={10000000} step={1000} suffix="₹"
        />
        <NumberField
          label="De-scale after losses"
          hint="Halve the lot ceiling after this many consecutive losing trades. 0 = off."
          value={cfg.descale_after_losses} defaultValue={defaults.descale_after_losses}
          onChange={(v) => patch({ descale_after_losses: v })} min={0} max={20}
        />
      </Section>

      <Section
        title="Contracts"
        description="Which option a signal buys."
        summary={`${cfg.moneyness} · ${cfg.expiry_series_indices.join('/')}`}
        persistKey="intraday-contracts"
      >
        <Field label="Moneyness" hint="Where the strike sits against the money." wide>
          <ChoiceRow
            value={cfg.moneyness}
            options={[
              { value: 'ATM', label: 'ATM', hint: 'At the money. The most liquid and the most premium.' },
              { value: 'ITM', label: 'ITM', hint: 'In the money. More delta, more capital.' },
              { value: 'OTM', label: 'OTM', hint: 'Out of the money. Cheap, and the least likely to follow a 20-point move.' },
            ]}
            onChange={(moneyness) => patch({ moneyness })}
          />
        </Field>
      </Section>

      <AdvancedSection count={ADVANCED_SETTING_COUNT}>
        <NumberField
          label="Warmup bars"
          hint="Bars of history before any strategy may fire. The slowest input is the slow EMA, which is not merely undefined before its length — it is wrong, and a seeded EMA reads plausible while it is still wrong."
          value={cfg.warmup_bars} defaultValue={defaults.warmup_bars}
          onChange={(warmup_bars) => patch({ warmup_bars })} min={20} max={1000} suffix="bars"
        />
        <NumberField
          label="Break buffer"
          hint="How far past a pivot the close must be before it counts as a break. Zero means a single tick through the level is enough."
          value={cfg.pb_break_buffer_atr} defaultValue={defaults.pb_break_buffer_atr}
          onChange={(pb_break_buffer_atr) => patch({ pb_break_buffer_atr })}
          min={0} max={2} step={0.05} suffix="× ATR"
        />
        <NumberField
          label="Breakeven at"
          hint="Where the stop moves to entry on a working Pivot Break trade."
          value={cfg.pb_breakeven_at_r} defaultValue={defaults.pb_breakeven_at_r}
          onChange={(pb_breakeven_at_r) => patch({ pb_breakeven_at_r })}
          min={0.1} max={5} step={0.1} suffix="R"
        />
        <NumberField
          label="ATR trail multiple"
          hint="Used only when the Pivot Break trail is set to ATR."
          value={cfg.pb_trail_atr_mult} defaultValue={defaults.pb_trail_atr_mult}
          onChange={(pb_trail_atr_mult) => patch({ pb_trail_atr_mult })}
          min={0} max={10} step={0.1} suffix="× ATR"
        />
        <NumberField
          label="Ribbon target"
          hint="MA Ribbon's target, as a multiple of its stop distance. The stated exit is the opposite cross; this is the ceiling on top of it."
          value={cfg.rb_target_r} defaultValue={defaults.rb_target_r}
          onChange={(rb_target_r) => patch({ rb_target_r })} min={0.5} max={20} step={0.5} suffix="R"
        />
        <NumberField
          label="Minimum stop distance"
          hint="Below this a VWAP SuperTrend stop is inside the noise and the next tick takes it out."
          value={cfg.vs_min_stop_points} defaultValue={defaults.vs_min_stop_points}
          onChange={(vs_min_stop_points) => patch({ vs_min_stop_points })}
          min={0.5} max={200} step={0.5} suffix="points"
        />
        <Field
          label="Fresh SuperTrend flip only"
          hint="'Whenever SuperTrend changes to red' is an event, not a state. Off makes any red bar eligible."
        >
          <Switch checked={cfg.vs_require_fresh_flip} label="Fresh SuperTrend flip only"
            onChange={() => patch({ vs_require_fresh_flip: !cfg.vs_require_fresh_flip })} />
        </Field>
        <NumberField
          label="Confirm flip within"
          hint="Bars the close may lag the flip and still count, for a flip whose own bar closed on the wrong side of VWAP."
          value={cfg.vs_confirm_within_bars} defaultValue={defaults.vs_confirm_within_bars}
          onChange={(vs_confirm_within_bars) => patch({ vs_confirm_within_bars })}
          min={0} max={10} suffix="bars"
        />
      </AdvancedSection>

      <SettingsDraftBar
        dirty={dirty}
        saving={setCfg.isPending}
        onApply={handleApply}
        onDiscard={handleDiscard}
        onReset={handleReset}
        resetConfirm={resetConfirm}
      />
    </>
  );
}

export default IntradaySettings;
