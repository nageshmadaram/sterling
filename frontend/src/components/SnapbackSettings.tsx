import React from 'react';
import {
  useSnapbackConfig,
  useUpdateSnapback,
  type ExitMode,
  type SizingMode,
  type SnapbackConfig,
  type StopMode,
} from '../hooks/useSnapback';
import {
  ChoiceRow, Field, NumberField, Section, Switch, DIM,
} from './kite/kiteSettingsPrimitives';
import {
  AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar,
} from './kite/config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { InstrumentsGroup } from './kite/config/ScanSettings';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';

/**
 * Snapback settings.
 *
 * Same order as every other engine panel — draft bar → power → universe →
 * trigger → contract → exit → risk → advanced — so the settings hub reads as
 * one product.
 *
 * What this panel does that no other one does: it shows the **gate's scorecard**
 * rather than a "not validated" sentence. This engine clears six of nine
 * checks including the entry-timing permutation, and misses two facts about
 * sample size. Flattening that to a single "no" would read identically to a
 * strategy whose entries lose money, and an operator deciding whether to arm a
 * setup by hand is deciding between exactly those two situations.
 *
 * The other thing worth knowing before touching a control: two of these
 * defaults look backwards and are not. A 0.70 delta is an IN-the-money option,
 * and a 40-day contract when the trade lasts fifteen sessions. Both follow from
 * the same arithmetic — the edge is a directional drift, so the contract that
 * monetises it best is the one carrying the most delta per rupee of theta.
 * Every such control says so inline, because a number chosen for a reason that
 * lives only in a document gets changed by the next person to open this page.
 */
const EXIT_OPTIONS: Array<{ value: ExitMode; label: string; hint: string }> = [
  { value: 'horizon', label: 'Hold the horizon',
    hint: 'Close after the holding period, whatever the price is doing. The '
      + 'measured edge is a MEAN over that horizon, so this is the only exit '
      + 'that collects the distribution that was actually measured.' },
  { value: 'mean_touch', label: 'On the mean',
    hint: 'Close when the instrument returns to its own 20-session mean — the '
      + 'thesis completing. Not measured: it cuts the horizon short, which '
      + 'collects a different distribution.' },
  { value: 'either', label: 'Whichever comes first',
    hint: 'The mean, or the horizon. Also not measured.' },
];

const SIZING_OPTIONS: Array<{ value: SizingMode; label: string; hint: string }> = [
  { value: 'PREMIUM_PCT', label: 'Premium budget',
    hint: 'A bought option’s maximum loss IS its premium, so the honest '
      + 'budget is stated in premium rather than in distance to a stop. A '
      + 'signal whose single lot breaks the budget is SKIPPED, not squeezed in.' },
  { value: 'LOTS', label: 'Fixed lots',
    hint: 'The same number of lots every time. Worth knowing: at 2% of a 1 lakh '
      + 'account the premium budget cannot buy one NIFTY lot at all, so a small '
      + 'account on the budget mode will only ever trade single stocks.' },
];

const STOP_MODE_OPTIONS: Array<{ value: StopMode; label: string; hint: string }> = [
  { value: 'both', label: 'Broker + monitor',
    hint: 'A GTT at Zerodha that survives this process dying, plus our own tick '
      + 'loop for intrabar exits. The production answer.' },
  { value: 'broker', label: 'Broker only',
    hint: 'A GTT and nothing else. Survives a crash; no intrabar exit.' },
  { value: 'monitor', label: 'Monitor only',
    hint: 'Our tick loop and nothing at the broker. If this process dies while '
      + 'holding, the position is unprotected.' },
];

const UNIVERSE_OPTIONS = [
  { value: 'fno' as const, label: 'Whole F&O list',
    hint: 'Every underlying with a published lot size and strike step, from the '
      + 'live instrument dump. Liquidity is then enforced on the CONTRACT — its '
      + 'quoted spread, premium and open interest — rather than by a list of '
      + 'names. Measured between July and September 2026 this rule fired 90 '
      + 'times across the F&O list and ZERO times among the fourteen curated '
      + 'names, so the list was the binding constraint, not the rule.' },
  { value: 'curated' as const, label: 'Curated fourteen',
    hint: 'The shared high-liquidity registry plus the indices — what every '
      + 'other engine here scans. Safer spreads, and almost no signals.' },
];

const MARKET_OPTIONS = [
  { value: 'bearish' as const, label: 'Only in a weak market',
    hint: 'Fade only while the index sits BELOW its own 50-session mean. This '
      + 'is the setting that decides whether the strategy makes money: measured '
      + 'on 202 underlyings over nine years the ungated book returns -1.28% per '
      + 'entry day and the gated one +2.13%. The gate is on the put’s BETA, not '
      + 'on the signal — a bought put is a large short position in the market, '
      + 'and the signal’s own edge is about 1.5 points, nowhere near enough to '
      + 'pay for being short a bull market.' },
  { value: 'off' as const, label: 'Any market',
    hint: 'Take every signal. Measured at -1.28% per entry day.' },
  { value: 'bullish' as const, label: 'Only in a strong market',
    hint: 'The inverse, here so the claim can be checked rather than taken on '
      + 'trust. Measured excess +0.6pp against +3.1pp for the shipped setting.' },
];

const HEDGE_OPTIONS = [
  { value: 'index_futures' as const, label: 'Hedge the market out',
    hint: 'Shorts the put’s own market delta, beta-weighted, with an index '
      + 'future. The signal’s edge is RELATIVE — about +1.5pp against a '
      + 'day-matched baseline — while a bought put is a large SHORT position '
      + 'in the market, and over nine rising years the second cost more than '
      + 'the first earned. A FUTURE and not a bought index call: a call is '
      + 'convex, and measured it beat an exact hedge by almost a point, which '
      + 'means it had stopped hedging and become a second long-market bet. '
      + 'NOTE a future needs MARGIN, which the premium budget below does not '
      + 'cover.' },
  { value: 'none' as const, label: 'Directional put',
    hint: 'No hedge. Measured at -0.62% per entry day against +0.52% hedged, '
      + 'on the same trades.' },
];

const CHECK_LABEL: Record<string, string> = {
  enough_trades: 'Enough trades',
  enough_days: 'Enough distinct entry days',
  profitable: 'Profitable out of sample',
  beats_random_timing: 'Beats random entry timing',
  deflated_sharpe: 'Survives the variant count',
  priced_edge: 'Edge bigger than the option costs',
  consistent_across_years: 'Positive every calendar year',
  survivable_drawdown: 'Survivable drawdown',
  mean_proven: 'Size of the edge proven',
};

const ADVANCED_SETTING_COUNT = 6;

export function SnapbackSettings() {
  const { data, isLoading } = useSnapbackConfig();
  const setCfg = useUpdateSnapback();

  const server = data?.config;
  const defaults = data?.defaults;
  const strategy = data?.strategy;
  const warnings = data?.warnings ?? [];
  const validation = strategy?.validation ?? null;

  const [draft, setDraft] = React.useState<SnapbackConfig | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const cfg = draft ?? server ?? null;
  const dirty = draft != null && server != null
    && (Object.keys(draft) as (keyof SnapbackConfig)[])
      .some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));

  useUnsavedDraftGuard('snapback', dirty);

  const patch = React.useCallback((next: Partial<SnapbackConfig>) => {
    setDraft((prev) => ({ ...(prev ?? server!), ...next }));
  }, [server]);

  const handleApply = React.useCallback(() => {
    // Only what CHANGED is sent. A full-object write silently reverts whatever
    // moved since the cache was fetched.
    if (!draft || !server) return;
    const changed: Partial<SnapbackConfig> = {};
    (Object.keys(draft) as (keyof SnapbackConfig)[]).forEach((key) => {
      if (JSON.stringify(draft[key]) !== JSON.stringify(server[key])) {
        (changed as Record<string, unknown>)[key] = draft[key];
      }
    });
    setCfg.mutate(changed, { onSuccess: () => setDraft(null) });
  }, [draft, server, setCfg]);
  const handleDiscard = React.useCallback(() => setDraft(null), []);
  const handleReset = React.useCallback(() => {
    if (!resetConfirm) { setResetConfirm(true); return; }
    if (defaults) setDraft({ ...defaults });
    setResetConfirm(false);
  }, [defaults, resetConfirm]);

  if (isLoading || !cfg || !server || !defaults || !strategy) {
    return (
      <PanelCard>
        <div style={{ color: DIM, fontSize: 12 }}>Loading strategy settings…</div>
      </PanelCard>
    );
  }

  /** True when this default came out of a measurement rather than a judgement. */
  const measured = (key: string) => strategy.calibrated_fields.includes(key);
  const tag = (key: string, hint: string) =>
    (measured(key) ? `${hint} MEASURED.` : `${hint} Not measured — a judgement call.`);

  return (
    <>
      <SettingsDraftBar
        dirty={dirty}
        saving={setCfg.isPending}
        onApply={handleApply}
        onDiscard={handleDiscard}
        onReset={handleReset}
        resetConfirm={resetConfirm}
        hasDraft={draft != null}
      />

      <EnginePowerHeader
        name={strategy.name}
        tagline={strategy.tagline}
        on={cfg.enabled}
        liveOn={server.enabled}
        busy={setCfg.isPending}
        onToggle={() => patch({ enabled: !cfg.enabled })}
        runningNote="Scanning daily bars for instruments stretched past a 20-session extreme."
        offNote="Off. Nothing is scanned and no orders can be placed."
      />

      {/* The scorecard, in full. A settings page is read carefully, so the
          evidence belongs here rather than on a hover. */}
      {validation && (
        <ConfigNote>
          <span>
            <strong>
              {validation.promoted
                ? 'Validated.'
                : `Passes ${validation.passed} of ${validation.total_checks} gate checks.`}
            </strong>{' '}
            Out of sample: {validation.oos_trades} trades on{' '}
            {validation.oos_entry_days} distinct entry days,{' '}
            {validation.oos_mean_day_return_pct >= 0 ? '+' : ''}
            {validation.oos_mean_day_return_pct.toFixed(2)}% per entry day, Sharpe{' '}
            {validation.sharpe.toFixed(2)}, worst drawdown{' '}
            {validation.max_drawdown_pct.toFixed(1)}% at {validation.allocation_pct}%
            of capital per position. Measured {validation.measured_at} on{' '}
            {validation.span} at {validation.slippage_pct}% slippage per leg.
            <br />
            {Object.entries(validation.checks).map(([key, ok]) => (
              <span key={key} style={{ marginRight: 10 }}>
                {ok ? '✓' : '✗'} {CHECK_LABEL[key] ?? key}
              </span>
            ))}
          </span>
        </ConfigNote>
      )}
      {validation?.reasons?.length ? (
        <ConfigNote>
          <span>
            {/* Verbatim from the harness. A summary of a summary is where a
                caveat quietly becomes a slogan. */}
            {validation.reasons.map((r, i) => <div key={i}>· {r}</div>)}
          </span>
        </ConfigNote>
      ) : null}
      <ConfigNote>
        <span>
          The premium behind every measurement is <strong>modelled</strong>, not
          quoted — no store here holds option price history. So the number that
          decides whether this is real is the <strong>break-even vol multiple</strong>:
          how dear the option can be before the trade stops paying.{' '}
          {validation?.breakeven_vrp ?? '—'}x against a market that charges{' '}
          {(data.vrp_band ?? [1.15, 1.3]).join('–')}x of realised vol.
        </span>
      </ConfigNote>
      <ConfigNote><span>{strategy.provenance}</span></ConfigNote>

      {warnings.map((w) => <ConfigNote key={w}><span>{w}</span></ConfigNote>)}

      <Section
        title="Instruments"
        description="What gets scanned, on daily bars."
        summary="Indices and single stocks"
        persistKey="snapback-instruments"
      >
        <Field
          label="Universe"
          hint="Which underlyings are eligible at all."
        >
          <ChoiceRow
            value={cfg.universe_mode}
            options={UNIVERSE_OPTIONS}
            onChange={(v) => patch({ universe_mode: v })}
          />
        </Field>
        <NumberField
          label="Universe cap"
          hint="How many instruments one F&O scan covers. A pass is one
                daily-candle request plus one chain lookup per name, so this
                trades scan time against coverage. Only read in F&O mode."
          value={cfg.max_universe}
          defaultValue={defaults.max_universe}
          onChange={(v) => patch({ max_universe: v })}
          min={1} max={500}
          disabled={cfg.universe_mode !== 'fno'}
        />
        <InstrumentsGroup
          indices={cfg.scan_indices}
          stocks={cfg.scan_stocks}
          // This engine has no "all F&O" mode. The measurement has nothing to
          // say about names it never saw, and a thin single-stock option turns
          // the 0.5% modelled slippage into a fiction.
          allStocks={false}
          stockContracts={cfg.scan_stock_contracts}
          onChange={(next) => patch({
            ...(next.scan_indices ? { scan_indices: next.scan_indices } : {}),
            ...(next.scan_stocks ? { scan_stocks: next.scan_stocks } : {}),
            ...(next.scan_stock_contracts != null
              ? { scan_stock_contracts: next.scan_stock_contracts } : {}),
          })}
          idPrefix="snapback"
        />
      </Section>

      <Section
        title="What makes it fire"
        description="A close through a 20-session extreme, far enough from its own mean to be worth fading — and only while the market itself is weak."
        summary="Breakout, stretch and the market gate"
        defaultOpen
        persistKey="snapback-trigger"
      >
        <Field
          label="Market exposure"
          hint={tag('hedge_mode',
            'Whether the trade keeps its market exposure or hedges it out. '
            + 'This and the market gate address the same problem — the put’s '
            + 'beta — from opposite ends: the gate avoids the exposure, the '
            + 'hedge removes it.')}
        >
          <ChoiceRow
            value={cfg.hedge_mode}
            options={HEDGE_OPTIONS}
            onChange={(v) => patch({ hedge_mode: v })}
          />
        </Field>
        <Field
          label="Market gate"
          hint={tag('market_filter',
            'The single setting that decides whether this strategy makes '
            + 'money. It gates on the PUT’S BETA, not on the signal.')}
        >
          <ChoiceRow
            value={cfg.market_filter}
            options={MARKET_OPTIONS}
            onChange={(v) => patch({ market_filter: v })}
          />
        </Field>
        <NumberField
          label="Market mean"
          hint={tag('market_ema',
            'Sessions in the index EMA the gate compares against.')}
          value={cfg.market_ema}
          defaultValue={defaults.market_ema}
          onChange={(v) => patch({ market_ema: v })}
          min={2} max={250} suffix="d"
          disabled={cfg.market_filter === 'off'}
        />
        <NumberField
          label="Breakout lookback"
          hint={tag('lookback_days',
            'Sessions the close must exceed the high of. 20 and 60 both work; '
            + '20 gives roughly twice the sample.')}
          value={cfg.lookback_days}
          defaultValue={defaults.lookback_days}
          onChange={(v) => patch({ lookback_days: v })}
          min={2} max={250} suffix="d"
        />
        <NumberField
          label="Minimum stretch"
          hint={tag('min_stretch_atr',
            'How far past its own 20-session mean, in ATR(14). The single '
            + 'largest lever: at 0 the breakout alone is materially weaker, and '
            + 'at 3.0 the effect is larger on a sample too thin to lean on.')}
          value={cfg.min_stretch_atr}
          defaultValue={defaults.min_stretch_atr}
          onChange={(v) => patch({ min_stretch_atr: v })}
          min={0} max={10} step={0.1} suffix="ATR"
        />
        <Field
          label="Fade weakness too"
          hint={tag('allow_fade_down',
            'Buy calls into a stretched 20-session LOW as well as puts into a '
            + 'high. Off, and for a pricing reason rather than a performance '
            + 'one: realised vol RISES to 1.25x after a downside break, so the '
            + 'real call costs more than this engine models it at and every '
            + 'result on that side is overstated by an unknown amount. After an '
            + 'upside break the same ratio is 0.86–0.92.')}
        >
          <Switch
            checked={cfg.allow_fade_down}
            label={cfg.allow_fade_down ? 'Both sides' : 'Puts into strength only'}
            onChange={() => patch({ allow_fade_down: !cfg.allow_fade_down })}
          />
        </Field>
        <NumberField
          label="Cooldown"
          hint={tag('cooldown_days', 'Sessions an instrument waits before firing again. Without it a market grinding to new highs fires on every session of the grind, which turns one event into twenty observations.')}
          value={cfg.cooldown_days}
          defaultValue={defaults.cooldown_days}
          onChange={(v) => patch({ cooldown_days: v })}
          min={0} max={250} suffix="d"
        />
      </Section>

      <Section
        title="Which contract"
        description="Picked by DELTA, not by a moneyness rung — a rung is a different amount of leverage at every vol level."
        summary="Delta, tenor and liquidity"
        persistKey="snapback-contract"
      >
        <NumberField
          label="Target delta"
          hint={tag('target_delta',
            '0.70 is an IN-the-money option, and that is the arithmetic rather '
            + 'than a preference: the edge is a directional drift, so the '
            + 'contract that monetises it best carries the most delta per rupee '
            + 'of theta and vega. A cheap out-of-the-money put shows a higher '
            + 'mean return because it is cheap, and a lower break-even because '
            + 'nearly all of what you paid was time value.')}
          value={cfg.target_delta}
          defaultValue={defaults.target_delta}
          onChange={(v) => patch({ target_delta: v })}
          min={0.05} max={0.95} step={0.05}
        />
        <NumberField
          label="Minimum days to expiry"
          hint={tag('min_dte',
            'Theta per rupee of premium runs at about 1/(2T), so a short '
            + 'contract spends the edge on decay. A 14-day contract shows a '
            + 'HIGHER mean return and a LOWER break-even — mean return is the '
            + 'flattering metric here.')}
          value={cfg.min_dte}
          defaultValue={defaults.min_dte}
          onChange={(v) => patch({ min_dte: v })}
          min={1} max={365} suffix="d"
        />
        <NumberField
          label="Maximum days to expiry"
          hint={tag('max_dte', 'The far end of the window. Past about 45 days the break-even curve flattens and a longer contract only ties up more premium for the same ten-session horizon.')}
          value={cfg.max_dte}
          defaultValue={defaults.max_dte}
          onChange={(v) => patch({ max_dte: v })}
          min={1} max={365} suffix="d"
        />
        <NumberField
          label="Minimum premium"
          hint={tag('min_option_premium', 'Refuse a contract cheap enough that the 0.05 tick is a large share of its price — there, a percentage move measures rounding rather than the market.')}
          value={cfg.min_option_premium}
          defaultValue={defaults.min_option_premium}
          onChange={(v) => patch({ min_option_premium: v })}
          min={0} max={10000} suffix="₹"
        />
        <NumberField
          label="Maximum spread"
          hint={tag('max_spread_pct', 'Half of this is paid on each leg. The measured break-even holds at 1% and 2% slippage; past that the margin over what the market charges for the option disappears.')}
          value={cfg.max_spread_pct}
          defaultValue={defaults.max_spread_pct}
          onChange={(v) => patch({ max_spread_pct: v })}
          min={0} max={100} step={0.1} suffix="%"
        />
      </Section>

      <Section
        title="How it ends"
        description="The horizon is the measurement; everything else is damage control."
        summary="Horizon and premium stop"
        persistKey="snapback-exit"
      >
        <Field
          label="Exit rule"
          hint="Position management is damage control on an edge you already
                have — it reshapes the distribution and cannot add drift."
        >
          <ChoiceRow
            value={cfg.exit_mode}
            options={EXIT_OPTIONS}
            onChange={(v) => patch({ exit_mode: v })}
          />
        </Field>
        <NumberField
          label="Holding period"
          hint={tag('hold_days',
            'Sessions held. The pooled mean rises from 1 to 10 sessions and '
            + 'falls at 15. Must be shorter than the minimum days to expiry — '
            + 'a trade that outlives its contract is priced at intrinsic and '
            + 'called an exit.')}
          value={cfg.hold_days}
          defaultValue={defaults.hold_days}
          onChange={(v) => patch({ hold_days: v })}
          min={1} max={60} suffix="d"
        />
        <NumberField
          label="Premium stop"
          hint={tag('premium_stop_pct', 'Give back at most this much of what was paid. A stop on the PREMIUM and not on spot, because the premium is the thing that can go to zero while the spot thesis is technically intact.')}
          value={cfg.premium_stop_pct}
          defaultValue={defaults.premium_stop_pct}
          onChange={(v) => patch({ premium_stop_pct: v })}
          min={1} max={100} suffix="%"
        />
      </Section>

      <Section
        title="Size and protection"
        description="What one signal deploys, and what protects it."
        summary="Premium budget and where the stop lives"
        persistKey="snapback-risk"
      >
        <Field label="Sizing" hint="How many lots a signal buys.">
          <ChoiceRow
            value={cfg.sizing_mode}
            options={SIZING_OPTIONS}
            onChange={(v) => patch({ sizing_mode: v })}
          />
        </Field>
        <NumberField
          label="Premium per position"
          hint={tag('premium_pct_of_capital', 'Share of capital deployed on one signal. This is also the whole amount at risk: a bought option cannot lose more than its premium, and a gap through the stop costs exactly that and not a rupee more. The measured drawdown of -10.4% was at 2%.')}
          value={cfg.premium_pct_of_capital}
          defaultValue={defaults.premium_pct_of_capital}
          onChange={(v) => patch({ premium_pct_of_capital: v })}
          min={0.01} max={100} step={0.5} suffix="%"
          disabled={cfg.sizing_mode !== 'PREMIUM_PCT'}
        />
        <NumberField
          label="Capital"
          hint={tag('capital_inr', 'What the premium budget is a percentage of.')}
          value={cfg.capital_inr}
          defaultValue={defaults.capital_inr}
          onChange={(v) => patch({ capital_inr: v })}
          min={1000} max={1e10} step={10000} suffix="₹"
        />
        <NumberField
          label="Maximum lots"
          hint={tag('max_lots', 'Ceiling per position, whichever sizing mode is chosen.')}
          value={cfg.max_lots}
          defaultValue={defaults.max_lots}
          onChange={(v) => patch({ max_lots: v })}
          min={1} max={100}
        />
        <NumberField
          label="Maximum open positions"
          hint={tag('max_open_positions', 'These entries cluster: a broad market push stretches many names on the same session, and fifteen of those is one bet with fifteen tickets.')}
          value={cfg.max_open_positions}
          defaultValue={defaults.max_open_positions}
          onChange={(v) => patch({ max_open_positions: v })}
          min={1} max={50}
        />
        <Field label="Stop lives at" hint="Where protection is enforced.">
          <ChoiceRow
            value={cfg.stop_mode}
            options={STOP_MODE_OPTIONS}
            onChange={(v) => patch({ stop_mode: v })}
          />
        </Field>
      </Section>

      <AdvancedSection count={ADVANCED_SETTING_COUNT}>
        <NumberField
          label="Assumed vol premium"
          hint={tag('assumed_vrp', 'What the engine assumes it pays for an option when no live quote is available, as a multiple of the instrument’s own realised vol. India VIX has run 1.15–1.30x. It is shown beside every modelled premium so the assumption is never invisible.')}
          value={cfg.assumed_vrp}
          defaultValue={defaults.assumed_vrp}
          onChange={(v) => patch({ assumed_vrp: v })}
          min={0.5} max={3} step={0.01} suffix="x"
        />
        <NumberField
          label="Put skew"
          hint={tag('smile_slope',
            'How much steeper the vol gets per unit of log-moneyness BELOW '
            + 'spot. Pricing every strike at one ATM vol is the flat-vol trap: '
            + 'a 0.20-delta put measured +8.31% per entry day flat and -5.97% '
            + 'at a realistic skew, so every low-delta setting was an artefact. '
            + '0 restores the flat model, and the gap between the two is the '
            + 'honest error bar on any strike away from the money.')}
          value={cfg.smile_slope}
          defaultValue={defaults.smile_slope}
          onChange={(v) => patch({ smile_slope: v })}
          min={0} max={6} step={0.1}
        />
        <NumberField
          label="Skew above spot"
          hint={tag('smile_itm_slope',
            'The steepness on the IN-the-money side. Real equity skew is '
            + 'asymmetric — steep below spot, much flatter above — and this '
            + 'engine buys in-the-money puts, so using one slope both ways '
            + 'hands it a discount the market does not give. Measured at 0.70 '
            + 'delta: symmetric reads +4.70% per entry day, realistic +2.01%. '
            + 'That gap is the size of the assumption, not of the edge.')}
          value={cfg.smile_itm_slope}
          defaultValue={defaults.smile_itm_slope}
          onChange={(v) => patch({ smile_itm_slope: v })}
          min={0} max={6} step={0.1}
        />
        <NumberField
          label="Sold leg delta"
          hint={tag('short_leg_delta',
            'Sell a further out-of-the-money option against the one bought, '
            + 'turning the outright into a vertical spread. 0 = outright, and '
            + 'that is the measured answer: every spread tested LOST, because '
            + 'the edge is a right tail — the top 1% of trades carry 148% of '
            + 'the book\u2019s P&L — and a spread caps exactly that.')}
          value={cfg.short_leg_delta}
          defaultValue={defaults.short_leg_delta}
          onChange={(v) => patch({ short_leg_delta: v })}
          min={0} max={0.9} step={0.05}
        />
        <NumberField
          label="Realised-vol window"
          hint={tag('rv_window', 'Sessions behind the modelled premium and the strike pick.')}
          value={cfg.rv_window}
          defaultValue={defaults.rv_window}
          onChange={(v) => patch({ rv_window: v })}
          min={5} max={250} suffix="d"
        />
        <NumberField
          label="Mean window"
          hint={tag('mean_touch_ema', 'The EMA the stretch is measured from, and the level the thesis completes at.')}
          value={cfg.mean_touch_ema}
          defaultValue={defaults.mean_touch_ema}
          onChange={(v) => patch({ mean_touch_ema: v })}
          min={2} max={250} suffix="d"
        />
        <NumberField
          label="Premium trail"
          hint={tag('premium_trail_pct', 'Give back at most this much of the best premium seen. 0 is off, and off is the measured setting — a ratchet cuts the horizon the edge was measured over.')}
          value={cfg.premium_trail_pct}
          defaultValue={defaults.premium_trail_pct}
          onChange={(v) => patch({ premium_trail_pct: v })}
          min={0} max={100} suffix="%"
        />
        <NumberField
          label="Let winners run"
          hint={tag('runner_mult', 'Hold past the horizon while the trade is already worth this multiple of what it cost. 0 is off. The edge here is a right tail — the top 1% of trades carry 148% of the P&L — so a fixed horizon closes the few trades that pay for the rest.')}
          value={cfg.runner_mult}
          defaultValue={defaults.runner_mult}
          onChange={(v) => patch({ runner_mult: v })}
          min={0} max={20} step={0.1} suffix="x"
        />
        <NumberField
          label="Runner give-back"
          hint={tag('runner_trail_pct', 'Once running, close when the premium gives back this much of its best. Only read when the runner is on.')}
          value={cfg.runner_trail_pct}
          defaultValue={defaults.runner_trail_pct}
          onChange={(v) => patch({ runner_trail_pct: v })}
          min={0} max={100} suffix="%"
        />
        <NumberField
          label="Minimum ATR"
          hint={tag('min_atr_bp', 'Refuse an instrument too quiet for the trade to clear its own costs, in basis points of price. 0 is off.')}
          value={cfg.min_atr_bp}
          defaultValue={defaults.min_atr_bp}
          onChange={(v) => patch({ min_atr_bp: v })}
          min={0} max={10000} suffix="bp"
        />
        <Field
          label="One position per instrument"
          hint="Two fades of one instrument are one bet with two tickets."
        >
          <Switch
            checked={cfg.one_position_per_underlying}
            label={cfg.one_position_per_underlying ? 'Enforced' : 'Allowed to stack'}
            onChange={() => patch({
              one_position_per_underlying: !cfg.one_position_per_underlying,
            })}
          />
        </Field>
      </AdvancedSection>
    </>
  );
}

export default SnapbackSettings;
