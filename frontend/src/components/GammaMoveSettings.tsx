import React from 'react';
import {
  useGammaMoveConfig,
  useUpdateGammaMove,
  type ExitPolicy,
  type GammaMoveConfig,
  type LevelTimeframe,
  type ExpirySelection,
  type StopMode,
  type SizingMode,
  type StopBasis,
  type TriggerTimeframe,
} from '../hooks/useGammaMove';
import {
  ChoiceRow, Field, NumberField, Section, Switch, DIM,
} from './kite/kiteSettingsPrimitives';
import { AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar } from './kite/config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { InstrumentsGroup } from './kite/config/ScanSettings';
import { OptionContractsPicker } from './kite/config/OptionContractsPicker';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';

/**
 * Gamma Move settings.
 *
 * Same order as the other engine panels — draft bar → power → universe →
 * levels → trigger → regime → exit → risk → advanced — so the settings hub
 * reads as one product.
 *
 * What this panel does that no other engine's does: **every threshold that was
 * measured says what the measurement was**, inline, next to the control. Seven
 * of these numbers came from a calibration run over 193,135 real bars, and one
 * of them (the regime multiplier) is set to an unconventional value precisely
 * because the conventional one inverted the gate. A number chosen for a reason
 * that lives only in a document will be changed by the next person to open this
 * page.
 *
 * The panel also states the finding that matters before any control: the entry
 * trigger on its own showed no edge. The level filter is where it was.
 */
const LEVEL_TF_OPTIONS: Array<{ value: LevelTimeframe; label: string; hint: string }> = [
  { value: 'day', label: 'Daily', hint: 'Swing levels off the daily chart. What the calibration used.' },
  { value: '60minute', label: 'Hourly', hint: 'More levels, weaker ones. Untested.' },
  { value: '15minute', label: '15 minute', hint: 'Intraday structure. Far more candidates and no measured edge.' },
];

const TRIGGER_TF_OPTIONS: Array<{ value: TriggerTimeframe; label: string; hint: string }> = [
  { value: '15minute', label: '15 minute', hint: 'What the source uses and what the thresholds were measured on.' },
  { value: '5minute', label: '5 minute', hint: 'Faster, noisier. The calibrated thresholds do not transfer.' },
  { value: '30minute', label: '30 minute', hint: 'Slower. Fewer signals and later entries.' },
];

const EXIT_OPTIONS: Array<{ value: ExitPolicy; label: string; hint: string }> = [
  { value: 'TIME_STOP', label: 'Time stop', hint: 'Exit after the holding limit. The only exit the source supports — it says a gamma move arrives in one day, two at most.' },
  { value: 'PERCENT_TARGET', label: 'Percent target', hint: 'Exit at a fixed percentage gain. Ours, not the source\'s: its 2x and 3x figures are outcomes, not a rule.' },
  { value: 'TRAILING_STOP', label: 'Trailing stop', hint: 'A stop that only moves up. Ours. No target ceiling.' },
];

const STOP_BASIS_OPTIONS: Array<{ value: StopBasis; label: string; hint: string }> = [
  { value: 'PERCENT', label: 'Percent', hint: 'A share of the entry premium. Safer here: these contracts trade from ₹10 to ₹600, so one number of points is a 5% risk at one end and 100% at the other.' },
  { value: 'POINTS', label: 'Points', hint: 'Absolute rupees, whatever the premium.' },
];

const SIZING_OPTIONS: Array<{ value: SizingMode; label: string; hint: string }> = [
  { value: 'RISK_PCT', label: 'Risk percent', hint: 'Size so the distance to the stop costs a set share of capital. Lots are derived and always whole.' },
  { value: 'LOTS', label: 'Fixed lots', hint: 'The same number of lots every time, regardless of how far the stop is.' },
];

const EXPIRY_SELECTION_OPTIONS: Array<{ value: ExpirySelection; label: string; hint: string }> = [
  { value: 'nearest', label: 'Nearest', hint: 'The soonest eligible contract.' },
  { value: 'weekly', label: 'Weekly', hint: 'Weekly series only. Indices have them; NSE lists no weekly stock options.' },
  { value: 'monthly', label: 'Monthly', hint: 'Monthly series only — the only series single stocks have.' },
  { value: 'any', label: 'Any', hint: 'No preference beyond the days-to-expiry window.' },
];

const STOP_MODE_OPTIONS: Array<{ value: StopMode; label: string; hint: string }> = [
  { value: 'both', label: 'Broker + monitor', hint: 'A GTT at Zerodha that survives this process dying, plus our own tick loop for intrabar exits. The production answer.' },
  { value: 'broker', label: 'Broker only', hint: 'A GTT and nothing else. Survives a crash, but only exits on a completed trigger — no intrabar exit.' },
  { value: 'monitor', label: 'Monitor only', hint: 'Our tick loop and nothing at the broker. If this process dies while holding, the position is unprotected.' },
];

const ADVANCED_SETTING_COUNT = 7;

export function GammaMoveSettings() {
  const { data, isLoading } = useGammaMoveConfig();
  const setCfg = useUpdateGammaMove();

  const server = data?.config;
  const defaults = data?.defaults;
  const strategy = data?.strategy;
  const researchOnly = data?.research_only;
  // Published by the engine, so the selectable set can never drift from the
  // set the scanner actually accepts.
  const eligible = data?.vocabularies?.scan_stocks ?? [];
  const warnings = data?.warnings ?? [];

  const [draft, setDraft] = React.useState<GammaMoveConfig | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const cfg = draft ?? server ?? null;
  const dirty = draft != null && server != null
    && (Object.keys(draft) as (keyof GammaMoveConfig)[])
      .some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));

  useUnsavedDraftGuard('gammaMove', dirty);

  const patch = React.useCallback((next: Partial<GammaMoveConfig>) => {
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

  /** The measurement behind a default, appended to that control's own hint.
   *  Published by the server, so it cannot drift from the engine. */
  const measured = (key: string, hint: string): string => {
    const note = strategy.calibration?.[key];
    return note ? `${hint} Measured: ${note}.` : hint;
  };

  const exitOptions = EXIT_OPTIONS.map((o) => ({
    ...o,
    hint: researchOnly?.exit_policy.includes(o.value) ? `${o.hint} Cannot run live.` : o.hint,
  }));

  return (
    <>
      <EnginePowerHeader
        name={strategy.name}
        tagline={strategy.tagline}
        on={cfg.enabled}
        liveOn={server.enabled}
        busy={setCfg.isPending}
        onToggle={() => patch({ enabled: !cfg.enabled })}
        runningNote="Scanning F&O stocks at support and resistance for open-interest unwinding."
        offNote="Off. Nothing is scanned and no orders can be placed."
      />

      {/* A settings page is read carefully, so the evidence is shown in full
          here rather than hidden on a hover as it is on the board.
          This used to end "stays paper-only until the readiness gate passes",
          which stopped being true when the per-strategy paper lock was removed —
          paper/live is the account's setting. A page that claims a safeguard the
          code no longer has is worse than one that claims none. */}
      <ConfigNote>
        <span>
          <strong>Not validated.</strong> {strategy.headline_finding}
          {strategy.what_to_do ? ` ${strategy.what_to_do}` : ''}
        </span>
      </ConfigNote>
      {strategy.evidence && (
        <ConfigNote><span>{strategy.evidence}</span></ConfigNote>
      )}
      <ConfigNote>
        <span>
          Every threshold below was measured over{' '}
          {strategy.calibration?.sample ?? 'a calibration run'}. Whether to trade an
          unproven edge is your call — paper or live is the account&rsquo;s Trading Mode
          setting, not this page&rsquo;s.
        </span>
      </ConfigNote>
      <ConfigNote>
        <span>{strategy.how_it_works} {strategy.provenance}</span>
      </ConfigNote>

      {/* Paper/live and manual/auto are not here on purpose — they are the
          account's and the engine's, shared with every Kite strategy. Saying so
          beats a duplicate switch that could disagree with the one that counts. */}
      <ConfigNote>
        <span>
          <strong>Paper or live, manual or auto</strong> are set in{' '}
          <strong>Trading Mode</strong> and apply to every Kite strategy. This page
          does not carry its own copy: two switches for one setting is how an engine
          ends up believing it is papering while the broker is not.
        </span>
      </ConfigNote>

      {warnings.map((w) => (
        <ConfigNote key={w}><span>{w}</span></ConfigNote>
      ))}

      <Section
        title="Chart source"
        description="Which price series Gamma Move reads a setup from."
        summary="Zerodha Kite"
        persistKey="gamma-source"
        defaultOpen
      >
        <Field label="Market data" hint="Order execution stays on Zerodha Kite." wide>
          <ChoiceRow
            value="kite"
            options={[{ value: 'kite', label: 'Zerodha Kite', hint: 'Broker candles and option chain snapshots.' }]}
            onChange={() => {}}
          />
        </Field>
      </Section>

      <Section
        title="Instruments"
        description="The indices and F&amp;O stocks this engine watches."
        summary={cfg.stock_contracts
          ? (cfg.scan_all_stocks
            ? `all ${eligible.length} high-liquidity stocks`
            : `${cfg.scan_stocks.length} of ${eligible.length} stocks`)
          : 'indices only'}
        // NOT "gamma-universe". That was this section's key when it was called
        // "Universe", and Section persists open/closed under the key: a reader
        // who had collapsed Universe would find Instruments collapsed too, and
        // `defaultOpen` cannot override a stored choice. A renamed section is a
        // different section and must not inherit the old one's state.
        persistKey="gamma-instruments"
        defaultOpen
      >
        <ConfigNote>
          <span>
            The eligible set is the same curated high-liquidity registry every other
            engine here scans. Thin or arbitrary F&amp;O names are not selectable:
            a 2&times; on a contract you cannot exit is not a 2&times;.
          </span>
        </ConfigNote>

        {/* The same control the ORB page uses, not a lookalike. It reads the
            registry itself, so the selectable set cannot drift from the one the
            scanner accepts, and the two pages cannot diverge as either changes. */}
        <InstrumentsGroup
          idPrefix="gamma"
          indices={cfg.scan_indices}
          stocks={cfg.scan_stocks}
          allStocks={cfg.scan_all_stocks}
          stockContracts={cfg.stock_contracts}
          allowEmptyIndices
          onChange={(next) => patch({
            ...(next.scan_indices !== undefined ? { scan_indices: next.scan_indices } : {}),
            ...(next.scan_stocks !== undefined ? { scan_stocks: next.scan_stocks } : {}),
            ...(next.scan_all_stocks !== undefined
              ? { scan_all_stocks: next.scan_all_stocks } : {}),
            // ORB calls this `scan_stock_contracts`; this engine calls it
            // `stock_contracts`. Mapped here rather than renaming a field on a
            // working engine — the control is shared, the storage stays each
            // engine's own.
            ...(next.scan_stock_contracts !== undefined
              ? { stock_contracts: next.scan_stock_contracts } : {}),
          })}
        />
      </Section>

      <Section
        title="Contracts"
        description="Which strike and expiry the signal is expressed through."
        summary={`${cfg.strike_window_pct}% of the level · ${cfg.expiry_selection}`}
        persistKey="gamma-contracts"
        defaultOpen
      >
        <NumberField
          label="Strike range & legs"
          hint="How far from the level the heaviest strike may sit. The source allows the strike to be a couple up or down from the exact level."
          value={cfg.strike_window_pct} defaultValue={defaults.strike_window_pct}
          onChange={(v) => patch({ strike_window_pct: v })} min={0.1} max={10} step={0.1} suffix="%"
        />
        <NumberField
          label="Minimum premium"
          hint={measured('min_option_premium', 'Contracts cheaper than this are skipped.')}
          value={cfg.min_option_premium} defaultValue={defaults.min_option_premium}
          onChange={(v) => patch({ min_option_premium: v })} min={0} max={200} step={1} suffix="₹"
        />
        <NumberField
          label="Minimum open interest" hint="A contract with thin open interest has no writers to squeeze."
          value={cfg.min_option_oi} defaultValue={defaults.min_option_oi}
          onChange={(v) => patch({ min_option_oi: v })} min={0} step={10000}
        />
        <NumberField
          label="Contracts watched" hint="The cap on the trigger pass. Each one costs a paced historical request every bar, which is what keeps the scan inside the rate limit."
          value={cfg.max_candidates} defaultValue={defaults.max_candidates}
          onChange={(v) => patch({ max_candidates: v })} min={1} max={100} step={1}
        />
        <Field
          label="Chain wall"
          hint={`Source rule, not calibrated. ${strategy.source_gates?.require_chain_max_oi ?? 'The strike must be the highest open interest on that expiry and option type.'}`}
        >
          <Switch
            checked={cfg.require_chain_max_oi !== false}
            label="Require chain-max open interest"
            onChange={() => patch({ require_chain_max_oi: cfg.require_chain_max_oi === false })}
          />
        </Field>
        <Field
          label="Spot through the wall"
          hint={`Source rule, not calibrated. ${strategy.source_gates?.require_spot_through_strike ?? 'Spot must have broken through, or sit at, the wall.'}`}
        >
          <Switch
            checked={cfg.require_spot_through_strike !== false}
            label="Require spot through the wall"
            onChange={() => patch({ require_spot_through_strike: cfg.require_spot_through_strike === false })}
          />
        </Field>

        {/* The same picker every other engine uses, reading and writing THIS
            strategy's selection. Edits join the draft like any other field, so
            one Apply saves the whole page. */}
        <OptionContractsPicker
          title="Option contracts"
          config={{
            scan_indices: cfg.scan_indices,
            scan_stocks: cfg.scan_stocks,
            scan_all_stocks: cfg.scan_all_stocks,
            scan_weekly_series_indices: cfg.scan_weekly_series_indices,
            scan_monthly_series_indices: cfg.scan_monthly_series_indices,
            scan_monthly_series_stocks: cfg.scan_monthly_series_stocks,
          }}
          saving={setCfg.isPending}
          onSave={(p) => patch(p as Partial<GammaMoveConfig>)}
        />
      </Section>

      <Section
        title="Expiry"
        description="Rules governing contract days-to-expiry and settlement dates."
        summary={`${cfg.expiry_dte_min}–${cfg.expiry_dte_max} DTE · ${cfg.expiry_selection}${cfg.avoid_expiry_day ? ' · avoid expiry day' : ''}`}
        persistKey="gamma-expiry"
        defaultOpen
      >
        <Field label="Expiry">
          <ChoiceRow
            value={cfg.expiry_selection}
            options={EXPIRY_SELECTION_OPTIONS}
            onChange={(v) => patch({ expiry_selection: v })}
          />
        </Field>
        <NumberField
          label="Minimum days to expiry"
          hint="Contracts closer than this are not eligible."
          value={cfg.expiry_dte_min} defaultValue={defaults.expiry_dte_min}
          onChange={(v) => patch({ expiry_dte_min: v })} min={0} max={60} step={1}
        />
        <NumberField
          label="Maximum days to expiry"
          hint="The source trades only the last week or two of a contract — earlier in the cycle it says open interest does not behave this way. NSE stock options are monthly-only, so this window is roughly the 15th onward."
          value={cfg.expiry_dte_max} defaultValue={defaults.expiry_dte_max}
          onChange={(v) => patch({ expiry_dte_max: v })} min={0} max={90} step={1}
        />
        <Field label="Expiry day" hint="On expiry day the open-interest signal degenerates into settlement mechanics and the premium is nearly all gamma already — a different trade wearing this one's name.">
          <Switch
            checked={cfg.avoid_expiry_day}
            label="Avoid expiry-day entries"
            onChange={() => patch({ avoid_expiry_day: !cfg.avoid_expiry_day })}
          />
        </Field>
      </Section>

      <Section
        title="Levels"
        description="Where support and resistance are, and how close spot has to be."
        summary={`within ${cfg.level_proximity_pct}% of a ${cfg.level_timeframe} level`}
        persistKey="gamma-levels"
        defaultOpen
      >
        <Field label="Chart" hint="Which series the swing levels are found on.">
          <ChoiceRow value={cfg.level_timeframe} options={LEVEL_TF_OPTIONS}
                     onChange={(v) => patch({ level_timeframe: v })} />
        </Field>
        <NumberField
          label="Proximity to level"
          hint={measured('level_proximity_pct',
            'How close spot must be to a confirmed level. This is the load-bearing setting in this engine — the one filter that separated from baseline. Widening it does not add signals of the same quality, it adds signals of baseline quality.')}
          value={cfg.level_proximity_pct} defaultValue={defaults.level_proximity_pct}
          onChange={(v) => patch({ level_proximity_pct: v })}
          min={0.1} max={10} step={0.1} suffix="%"
        />
        <NumberField
          label="Pivot lookback" hint="Bars either side that a swing high or low must dominate. A pivot is only counted once those bars have printed, so a level never appears earlier than it could have been known."
          value={cfg.pivot_lookback} defaultValue={defaults.pivot_lookback}
          onChange={(v) => patch({ pivot_lookback: v })} min={2} max={20} step={1}
        />
        <NumberField
          label="Minimum touches" hint="How many swing pivots must cluster before it counts as a level. The source's 'rejection, then rejection again'."
          value={cfg.min_level_touches} defaultValue={defaults.min_level_touches}
          onChange={(v) => patch({ min_level_touches: v })} min={1} max={10} step={1}
        />
      </Section>

      <Section
        title="Trigger"
        description="The three conditions that must hold on the same bar."
        summary={`OI −${cfg.min_oi_drop_pct}% · vol ${cfg.volume_spike_mult}× · price +${cfg.min_price_gain_pct}%`}
        persistKey="gamma-trigger"
        defaultOpen
      >
        <Field label="Chart" hint="The option contract's own candles — not the underlying's.">
          <ChoiceRow value={cfg.trigger_timeframe} options={TRIGGER_TF_OPTIONS}
                     onChange={(v) => patch({ trigger_timeframe: v })} />
        </Field>
        <NumberField
          label="Open interest drop"
          hint={measured('min_oi_drop_pct',
            'How far open interest must fall bar on bar for writers to count as covering. Never differenced across a session boundary.')}
          value={cfg.min_oi_drop_pct} defaultValue={defaults.min_oi_drop_pct}
          onChange={(v) => patch({ min_oi_drop_pct: v })} min={0.1} max={50} step={0.5} suffix="%"
        />
        <NumberField
          label="Volume spike"
          hint={measured('volume_spike_mult',
            `How far above its own recent average this bar's volume must run.`)}
          value={cfg.volume_spike_mult} defaultValue={defaults.volume_spike_mult}
          onChange={(v) => patch({ volume_spike_mult: v })} min={1.1} max={20} step={0.1} suffix="×"
        />
        <NumberField
          label="Premium rise"
          hint={measured('min_price_gain_pct', 'How far the option itself must move on the bar.')}
          value={cfg.min_price_gain_pct} defaultValue={defaults.min_price_gain_pct}
          onChange={(v) => patch({ min_price_gain_pct: v })} min={0.1} max={50} step={0.5} suffix="%"
        />
        <NumberField
          label="Bars to confirm" hint="Consecutive bars on which all three must hold. The source's 'confirms within 45 minutes' read at three bars; one is the default because its worked examples enter on the first."
          value={cfg.confirm_bars} defaultValue={defaults.confirm_bars}
          onChange={(v) => patch({ confirm_bars: v })} min={1} max={3} step={1}
        />
      </Section>

      <Section
        title="Trend gate"
        description="The direction filter the source names as the fix for its own flaw."
        summary={cfg.regime_enabled
          ? `SuperTrend ${cfg.regime_period} × ${cfg.regime_multiplier}`
          : 'off'}
        persistKey="gamma-regime"
      >
        <Field label="Trend gate" hint="Calls need an uptrend, puts a downtrend. The source names a corrective market as what broke this strategy, so switching this off ships the known bug.">
          <Switch
            checked={cfg.regime_enabled}
            label={cfg.regime_enabled ? 'On' : 'Off'}
            onChange={() => patch({ regime_enabled: !cfg.regime_enabled })}
          />
        </Field>
        <NumberField
          label="SuperTrend period" hint="ATR length. Every period tested behaved the same; this one matches the rest of the platform."
          value={cfg.regime_period} defaultValue={defaults.regime_period}
          onChange={(v) => patch({ regime_period: v })} min={2} max={50} step={1}
          disabled={!cfg.regime_enabled}
        />
        <NumberField
          label="SuperTrend multiplier"
          hint={measured('regime_multiplier',
            'Band width. Read the measurement before changing this: at the conventional 3.0 the gate pointed the wrong way.')}
          value={cfg.regime_multiplier} defaultValue={defaults.regime_multiplier}
          onChange={(v) => patch({ regime_multiplier: v })} min={0.5} max={6} step={0.1} suffix="×"
          disabled={!cfg.regime_enabled}
        />
      </Section>

      <Section
        title="Exit and stop"
        description="Where the trade stops and how long it is held."
        summary={`${cfg.exit_policy.replace(/_/g, ' ').toLowerCase()} · ${cfg.max_hold_days}d · stop ${cfg.stop_percent}%`}
        persistKey="gamma-exit"
      >
        <Field label="Exit policy" hint="The source gives no exit rule at all — its 2x and 3x are outcomes of discretionary exits. Only the time stop is supported by evidence.">
          <ChoiceRow value={cfg.exit_policy} options={exitOptions}
                     onChange={(v) => patch({ exit_policy: v })} />
        </Field>
        <NumberField
          label="Maximum hold" hint="Trading sessions, not calendar days — a weekend must not age a position by two. The source: one day, two at most."
          value={cfg.max_hold_days} defaultValue={defaults.max_hold_days}
          onChange={(v) => patch({ max_hold_days: v })} min={1} max={10} step={1} suffix=" sessions"
        />
        <Field label="Stop basis" hint="How every stop distance here is expressed.">
          <ChoiceRow value={cfg.stop_basis} options={STOP_BASIS_OPTIONS}
                     onChange={(v) => patch({ stop_basis: v })} />
        </Field>
        <NumberField
          label="Stop distance"
          hint={measured('stop_percent',
            'A floor under the swing-low stop. Whichever is tighter wins, so a swing low far below entry cannot become a stop in name only.')}
          value={cfg.stop_percent} defaultValue={defaults.stop_percent}
          onChange={(v) => patch({ stop_percent: v })} min={1} max={95} step={1} suffix="%"
          disabled={cfg.stop_basis !== 'PERCENT'}
        />
        <NumberField
          label="Swing lookback" hint="Bars of the option's own chart searched for the swing low. The source is explicit that this is the option chart, not the underlying's."
          value={cfg.swing_lookback} defaultValue={defaults.swing_lookback}
          onChange={(v) => patch({ swing_lookback: v })} min={2} max={30} step={1}
        />
        <Field label="Where the stop lives" hint="What watches the position if this process dies. Same vocabulary as the SuperTrend engine's stop mode.">
          <ChoiceRow value={cfg.stop_mode} options={STOP_MODE_OPTIONS}
                     onChange={(v) => patch({ stop_mode: v })} />
        </Field>
      </Section>

      <Section
        title="Size and risk"
        description="How large each trade is, and what stops it trading."
        summary={cfg.sizing_mode === 'LOTS'
          ? `${cfg.lots} lots` : `${cfg.risk_per_trade_pct}% of ₹${cfg.capital_inr.toLocaleString('en-IN')}`}
        persistKey="gamma-risk"
      >
        <Field label="Sizing" hint="How the trade size is stated.">
          <ChoiceRow value={cfg.sizing_mode} options={SIZING_OPTIONS}
                     onChange={(v) => patch({ sizing_mode: v })} />
        </Field>
        {cfg.sizing_mode === 'RISK_PCT' ? (
          <>
            <NumberField
              label="Risk per trade" hint="Share of capital lost if the stop is honoured."
              value={cfg.risk_per_trade_pct} defaultValue={defaults.risk_per_trade_pct}
              onChange={(v) => patch({ risk_per_trade_pct: v })} min={0.1} max={10} step={0.1} suffix="%"
            />
            <NumberField
              label="Capital" hint="The base the risk percentage is taken from."
              value={cfg.capital_inr} defaultValue={defaults.capital_inr}
              onChange={(v) => patch({ capital_inr: v })} min={10000} step={50000} suffix="₹"
            />
          </>
        ) : (
          <NumberField
            label="Lots" hint="Exchange lots per trade. The lot size comes from the contract."
            value={cfg.lots} defaultValue={defaults.lots}
            onChange={(v) => patch({ lots: v })} min={0} max={100} step={1}
          />
        )}
        <NumberField
          label="Premium outlay cap" hint="The most that may be spent on one position. A bought option's whole premium is at risk if the stop gaps, so this caps the outlay, not the stop distance. Set below one lot's cost and every trade is silently refused."
          value={cfg.max_premium_at_risk_inr} defaultValue={defaults.max_premium_at_risk_inr}
          onChange={(v) => patch({ max_premium_at_risk_inr: v })} min={1000} step={5000} suffix="₹"
        />
        <NumberField
          label="Concurrent positions" hint="How many contracts may be held at once."
          value={cfg.max_concurrent_positions} defaultValue={defaults.max_concurrent_positions}
          onChange={(v) => patch({ max_concurrent_positions: v })} min={1} max={20} step={1}
        />
        <NumberField
          label="Daily loss limit" hint="Realised losses past this halt the strategy for the day."
          value={cfg.daily_loss_limit_inr} defaultValue={defaults.daily_loss_limit_inr}
          onChange={(v) => patch({ daily_loss_limit_inr: v })} min={1000} step={2500} suffix="₹"
        />
        <NumberField
          label="De-scale after losses" hint="The source's own risk rule: after this many losing trades in a row, cut the size."
          value={cfg.descale_after_losses} defaultValue={defaults.descale_after_losses}
          onChange={(v) => patch({ descale_after_losses: v })} min={1} max={10} step={1}
        />
      </Section>

      <AdvancedSection count={ADVANCED_SETTING_COUNT}>
        <NumberField
          label="Volume baseline" hint="Bars averaged for 'normal' volume. Reaches back across sessions on purpose — a within-session window is undefined until 13:15."
          value={cfg.volume_lookback} defaultValue={defaults.volume_lookback}
          onChange={(v) => patch({ volume_lookback: v })} min={5} max={100} step={1}
        />
        <NumberField
          label="Level cluster width" hint="How close two swing pivots must be to count as the same level."
          value={cfg.level_cluster_pct} defaultValue={defaults.level_cluster_pct}
          onChange={(v) => patch({ level_cluster_pct: v })} min={0.1} max={5} step={0.05} suffix="%"
        />
        <NumberField
          label="Level history" hint="Bars searched for pivots."
          value={cfg.level_lookback_days} defaultValue={defaults.level_lookback_days}
          onChange={(v) => patch({ level_lookback_days: v })} min={30} max={500} step={10}
        />
        <NumberField
          label="Scan interval" hint="How often the background scan runs."
          value={cfg.scan_interval_seconds} defaultValue={defaults.scan_interval_seconds}
          onChange={(v) => patch({ scan_interval_seconds: v })} min={60} max={3600} step={60} suffix="s"
        />
        <NumberField
          label="New trades per day" hint="Entries allowed in one session."
          value={cfg.max_new_trades_per_day} defaultValue={defaults.max_new_trades_per_day}
          onChange={(v) => patch({ max_new_trades_per_day: v })} min={1} max={20} step={1}
        />
        <Field label="Session start" hint="Not 09:15: the first bar of a session has no prior bar inside it, and differencing open interest across the boundary produces a phantom unwind every morning.">
          <input
            type="time" value={cfg.session_start}
            onChange={(e) => patch({ session_start: e.target.value })}
            style={{ background: 'transparent', border: '1px solid var(--k-border)',
                     borderRadius: 6, color: 'var(--k-text)', padding: '4px 8px', fontSize: 12 }}
          />
        </Field>
        <Field label="Session end" hint="Scanning stops here; open positions are still watched out.">
          <input
            type="time" value={cfg.session_end}
            onChange={(e) => patch({ session_end: e.target.value })}
            style={{ background: 'transparent', border: '1px solid var(--k-border)',
                     borderRadius: 6, color: 'var(--k-text)', padding: '4px 8px', fontSize: 12 }}
          />
        </Field>
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

export default GammaMoveSettings;
