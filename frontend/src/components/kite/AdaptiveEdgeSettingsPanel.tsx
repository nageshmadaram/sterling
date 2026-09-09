
/* The risk controls the engine actually reads.
   The legacy sections above (Trail tightness, Exit rule, Structure, Mode rungs)
   belong to an earlier moving-average scalper and reach no engine; the API lists
   them in `inert_fields`. These are the ones that decide what a live position
   would cost, so they get their own section rather than being buried behind an
   API call an operator would have to know about. */

/* Four sections below drive nothing. They configure the moving-average scalper
   this strategy replaced, and the backend lists them in `inert_fields` for
   exactly this purpose. Saying so beats letting an operator set a stop distance,
   get a 200 back, and believe it took. */
function InertNote({ replacement }: { replacement: string }) {
  return (
    <ConfigNote>
      This section does not reach the engine. It configures the earlier
      moving-average strategy, which the Master Specification engine replaced.
      {replacement}
    </ConfigNote>
  );
}

function EngineVersionSection() {
  const { data } = useAdaptiveEdgeEngineConfig();
  const save = useSetAdaptiveEdgeEngineConfig();
  const [draft, setDraft] = React.useState<Record<string, unknown> | null>(null);

  React.useEffect(() => {
    if (data?.config && draft === null) setDraft({ ...data.config });
  }, [data, draft]);

  if (!data || !draft) return null;

  const activeVersion = String(draft.strategy_version ?? 'v2_hardened');
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.config);
  const setVersion = (v: 'v1_baseline' | 'v2_hardened') => setDraft({ ...draft, strategy_version: v });

  return (
    <Section
      title="Strategy Engine Version"
      description="Select between the V1 Legacy baseline model and the V2 Production-Hardened institutional model."
      summary={activeVersion === 'v2_hardened' ? 'V2 Production-Hardened (Active)' : 'V1 Legacy Baseline (Active)'}
      defaultOpen
      persistKey="ae-engine-version"
    >
      <div style={{ display: 'flex', gap: 10, padding: '6px 0 12px' }}>
        <button
          type="button"
          onClick={() => setVersion('v2_hardened')}
          style={{
            flex: 1,
            padding: '10px 14px',
            borderRadius: 7,
            border: `1.5px solid ${activeVersion === 'v2_hardened' ? 'var(--k-brand)' : 'var(--k-border)'}`,
            background: activeVersion === 'v2_hardened' ? 'var(--k-tint-warm-2, rgba(255, 87, 34, 0.08))' : 'var(--k-bg)',
            color: activeVersion === 'v2_hardened' ? 'var(--k-brand)' : 'var(--k-text)',
            cursor: 'pointer',
            textAlign: 'left',
            transition: 'all 0.15s ease',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 700, fontSize: 13 }}>V2 Production-Hardened</span>
            <span
              style={{
                fontSize: 10,
                padding: '2px 6px',
                borderRadius: 4,
                background: 'var(--k-green-bg, #e8f5e9)',
                color: 'var(--k-green, #2e7d32)',
                fontWeight: 750,
              }}
            >
              RECOMMENDED
            </span>
          </div>
          <div style={{ fontSize: 11.5, color: MUTED, marginTop: 4, lineHeight: 1.4 }}>
            09:15–09:28 morning lockout · 50/50 scaled 1.5R exit + BE+ ratchet · 4-bar stagnation stop · wick rejection filter · passive limit pegging.
          </div>
        </button>

        <button
          type="button"
          onClick={() => setVersion('v1_baseline')}
          style={{
            flex: 1,
            padding: '10px 14px',
            borderRadius: 7,
            border: `1.5px solid ${activeVersion === 'v1_baseline' ? 'var(--k-brand)' : 'var(--k-border)'}`,
            background: activeVersion === 'v1_baseline' ? 'var(--k-tint-warm-2, rgba(255, 87, 34, 0.08))' : 'var(--k-bg)',
            color: activeVersion === 'v1_baseline' ? 'var(--k-brand)' : 'var(--k-text)',
            cursor: 'pointer',
            textAlign: 'left',
            transition: 'all 0.15s ease',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 700, fontSize: 13 }}>V1 Legacy Baseline</span>
            <span
              style={{
                fontSize: 10,
                padding: '2px 6px',
                borderRadius: 4,
                background: 'var(--k-surface-sunken, #f5f5f5)',
                color: MUTED,
                fontWeight: 600,
              }}
            >
              LEGACY
            </span>
          </div>
          <div style={{ fontSize: 11.5, color: MUTED, marginTop: 4, lineHeight: 1.4 }}>
            Standard volume surge · all-or-nothing single targets · unrestricted morning hours · standard market slippage.
          </div>
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, paddingTop: 6 }}>
        <button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(draft, { onSuccess: () => setDraft(null) })}
          style={{ ...inputStyle, cursor: dirty ? 'pointer' : 'default', opacity: dirty ? 1 : 0.5, width: 'auto', padding: '6px 14px' }}
        >
          {save.isPending ? 'Saving…' : 'Apply Version'}
        </button>
        {dirty ? <span style={{ color: MUTED, fontSize: 11.5, alignSelf: 'center' }}>Unsaved version change</span> : null}
      </div>
    </Section>
  );
}

function EngineRiskSection() {
  const { data } = useAdaptiveEdgeEngineConfig();
  const save = useSetAdaptiveEdgeEngineConfig();
  const [draft, setDraft] = React.useState<Record<string, unknown> | null>(null);

  React.useEffect(() => {
    if (data?.config && draft === null) setDraft({ ...data.config });
  }, [data, draft]);

  if (!data || !draft) return null;

  const value = (key: string) => Number(draft[key] ?? 0);
  const patch = (next: Record<string, unknown>) => setDraft({ ...draft, ...next });
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.config);
  const num = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    patch({ [key]: Number(e.target.value) });

  return (
    <Section
      title="Risk and session"
      description="What a position costs, how many run at once, and when the day ends. These reach the engine."
      summary={`${value('lots')} lot · ${value('stop_percent')}% stop · max ${value('max_positions')} · flat ${String(draft.square_off_time)}`}
      defaultOpen
      persistKey="ae-engine-risk"
    >
      <Field label="Lots" hint="Contracts per entry, in exchange lots.">
        <input style={inputStyle} type="number" min={1} value={value('lots')} onChange={num('lots')} />
      </Field>
      <Field label="Stop (% of premium)" hint="Distance from entry at which the position is closed.">
        <input style={inputStyle} type="number" min={1} max={100} value={value('stop_percent')} onChange={num('stop_percent')} />
      </Field>
      <Field label="Target multiple" hint="Exit target as a multiple of entry premium.">
        <input style={inputStyle} type="number" step="0.1" min={0.1} value={value('target_multiple')} onChange={num('target_multiple')} />
      </Field>
      <Field label="Max positions" hint="Open at once. Risk adds up across them.">
        <input style={inputStyle} type="number" min={1} value={value('max_positions')} onChange={num('max_positions')} />
      </Field>
      <Field label="Max daily loss" hint="0 means no cap: a losing day is bounded only by per-trade stops.">
        <input style={inputStyle} type="number" min={0} value={value('max_daily_loss')} onChange={num('max_daily_loss')} />
      </Field>
      <Field label="Square off" hint="IST. The position is flat before the close rather than carried into settlement.">
        <input style={inputStyle} type="text" value={String(draft.square_off_time ?? '')} onChange={(e) => patch({ square_off_time: e.target.value })} />
      </Field>

      {data.warnings.map((warning) => (
        <ConfigNote key={warning}>{warning}</ConfigNote>
      ))}

      <div style={{ display: 'flex', gap: 8, paddingTop: 10 }}>
        <button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(draft, { onSuccess: () => setDraft(null) })}
          style={{ ...inputStyle, cursor: dirty ? 'pointer' : 'default', opacity: dirty ? 1 : 0.5, width: 'auto', padding: '6px 14px' }}
        >
          {save.isPending ? 'Saving…' : 'Apply'}
        </button>
        {dirty ? <span style={{ color: MUTED, fontSize: 11.5, alignSelf: 'center' }}>Unsaved changes</span> : null}
      </div>
    </Section>
  );
}

import React from 'react';
import {
  Field, MUTED, Section, Switch, TEXT, inputStyle,
} from './kiteSettingsPrimitives';
import { ConfigNote, PanelCard, SettingsDraftBar } from './config/ConfigPrimitives';
import { EnginePowerHeader } from './config/EnginePowerHeader';
import { ContractsGroup, InstrumentsGroup, SignalSourceGroup } from './config/ScanSettings';
import { scanSourceLabel } from './config/registry';
import {
  useAdaptiveEdgeEngineConfig, useAdaptiveEdgeSettings, useAdaptiveEdgeSnapshot,
  useSetAdaptiveEdgeEngineConfig, useSetAdaptiveEdgeSettings,
} from '../../hooks/useAdaptiveEdge';
import type { AdaptiveEdgeSettings } from '../../types/adaptiveEdge';
import type { Moneyness, ScanExpiry, ScanSource } from '../../types/kiteEngine';

const DEFAULT_STRIKES: Moneyness[] = ['ITM2', 'ITM1', 'ATM', 'OTM1', 'OTM2'];
const DEFAULT_INDICES = ['NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX'];
const DEFAULT_EXPIRIES: ScanExpiry[] = ['weekly', 'monthly'];

function withDefaults(settings: AdaptiveEdgeSettings): AdaptiveEdgeSettings {
  return {
    ...settings,
    scan_source: settings.scan_source ?? 'spot',
    scan_indices: settings.scan_indices?.length ? settings.scan_indices : DEFAULT_INDICES,
    scan_stocks: settings.scan_stocks ?? [],
    scan_all_stocks: settings.scan_all_stocks ?? false,
    scan_stock_contracts: settings.scan_stock_contracts ?? false,
    strike_moneyness: settings.strike_moneyness?.length ? settings.strike_moneyness : DEFAULT_STRIKES,
    scan_expiries: settings.scan_expiries?.length ? settings.scan_expiries : DEFAULT_EXPIRIES,
    scan_expiries_indices: settings.scan_expiries_indices?.length
      ? settings.scan_expiries_indices
      : (settings.scan_expiries?.length ? settings.scan_expiries : DEFAULT_EXPIRIES),
  };
}

function saveError(draft: AdaptiveEdgeSettings): string | null {
  if (draft.w_short >= draft.w_long) return 'W short must be less than W long.';
  if (!(draft.scalp_favorable_points <= draft.extended_favorable_points && draft.extended_favorable_points <= draft.intraday_favorable_points)) {
    return 'Mode rungs must be non-decreasing: scalp \u2264 extended \u2264 intraday.';
  }
  if (!(draft.scan_indices?.length)) return 'Select at least one index.';
  if (!(draft.strike_moneyness?.length)) return 'Select at least one strike.';
  if (!(draft.scan_expiries_indices?.length || draft.scan_expiries?.length)) return 'Select at least one expiry cycle.';
  return null;
}

export function AdaptiveEdgeSettingsPanel() {
  const { data, isLoading, error } = useAdaptiveEdgeSettings();
  const snapshot = useAdaptiveEdgeSnapshot();
  const save = useSetAdaptiveEdgeSettings();
  const [draft, setDraft] = React.useState<AdaptiveEdgeSettings | null>(null);
  const [dirty, setDirty] = React.useState(false);

  React.useEffect(() => {
    if (data?.settings && !dirty) setDraft(withDefaults(data.settings));
  }, [data?.settings, dirty]);

  if (isLoading || !draft) {
    return <div style={{ padding: 18, color: MUTED, fontSize: 12 }}>Loading Adaptive Edge settings…</div>;
  }
  if (error) {
    return <div style={{ padding: 18, color: 'var(--k-red-brick)', fontSize: 12 }}>Failed to load Adaptive Edge settings.</div>;
  }

  const patch = (partial: Partial<AdaptiveEdgeSettings>) => {
    setDraft({ ...draft, ...partial });
    setDirty(true);
  };
  const invalid = saveError(draft);
  const indexExpiries = draft.scan_expiries_indices ?? draft.scan_expiries ?? DEFAULT_EXPIRIES;
  const instrumentsSummary = !draft.scan_stock_contracts
    ? `${draft.scan_indices.length} indices · no stocks`
    : draft.scan_all_stocks
      ? `All F&O · ${draft.scan_indices.length} indices`
      : `${draft.scan_stocks.length} stocks · ${draft.scan_indices.length} indices`;

  return (
    <>
      <EnginePowerHeader
        name="Adaptive Edge"
        tagline="Score, modes, protection and structure."
        on={draft.enabled}
        liveOn={!!data?.settings.enabled}
        busy={save.isPending}
        onToggle={() => patch({ enabled: !draft.enabled })}
        runningNote="The Adaptive Edge board is on."
        offNote="The Adaptive Edge board is off. The last snapshot can still be inspected."
      />

      {invalid && (
        <div style={{ margin: '0 0 16px', padding: '10px 12px', borderRadius: 8, background: '#fff6f5', border: '1px solid #f0d2c2', color: 'var(--k-red-brick)', fontSize: 12, lineHeight: 1.5 }}>
          {invalid}
        </div>
      )}

      <PanelCard>
        <Section
          title="Chart source"
          description="Which price series Adaptive Edge reads a setup from."
          summary={`${scanSourceLabel(draft.scan_source)} · ${draft.w_short}/${draft.w_long}`}
          defaultOpen
          persistKey="ae-chart"
        >
          <SignalSourceGroup
            name="adaptive-edge-signal-source"
            value={draft.scan_source}
            onChange={(value: ScanSource) => patch({ scan_source: value })}
            fieldHint="The Adaptive Edge score is always computed on the spot tape. This only decides which contracts are attached after a signal."
          />
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', paddingTop: 8 }}>
            <Field label="W short" hint="Short volatility window on that chart.">
              <input style={inputStyle} type="number" value={draft.w_short} onChange={(e) => patch({ w_short: Number(e.target.value) })} />
            </Field>
            <Field label="W long" hint="Must be greater than W short.">
              <input style={inputStyle} type="number" value={draft.w_long} onChange={(e) => patch({ w_long: Number(e.target.value) })} />
            </Field>
          </div>
          <ConfigNote>
            Adaptive Edge does not invent a premium-chart score. Derivatives and confluence only change
            how strikes are listed after a spot signal. Live orders stay off.
          </ConfigNote>
        </Section>

        <Section
          title="Instruments"
          description="The indices and F&O stocks this engine watches."
          summary={instrumentsSummary}
          defaultOpen
          persistKey="ae-instruments"
        >
          <InstrumentsGroup
            idPrefix="Adaptive Edge"
            indices={draft.scan_indices}
            stocks={draft.scan_stocks}
            allStocks={draft.scan_all_stocks}
            stockContracts={draft.scan_stock_contracts}
            onChange={(next) => patch({
              ...(next.scan_indices !== undefined ? { scan_indices: next.scan_indices } : {}),
              ...(next.scan_stocks !== undefined ? { scan_stocks: next.scan_stocks } : {}),
              ...(next.scan_all_stocks !== undefined ? { scan_all_stocks: next.scan_all_stocks } : {}),
              ...(next.scan_stock_contracts !== undefined ? { scan_stock_contracts: next.scan_stock_contracts } : {}),
            })}
          />
          <ConfigNote>
            Only spots with Adaptive Edge tape produce scores. Other selected underlyings stay on the
            board as not scanned — they do not get invented entries.
          </ConfigNote>
        </Section>

        <Section
          title="Contracts"
          description="Which strikes and expiry cycles Adaptive Edge resolves after a signal."
          summary={`${draft.strike_moneyness.length} strikes · ${indexExpiries.join(' + ')}`}
          defaultOpen
          persistKey="ae-contracts"
        >
          <ContractsGroup
            strikes={(draft.strike_moneyness as Moneyness[])}
            indexExpiries={indexExpiries}
            dteMin={draft.expiry_dte_min ?? 0}
            dteMax={draft.expiry_dte_max ?? 400}
            avoidExpiryDay={draft.avoid_expiry_day ?? false}
            dteDefaults={{ min: 0, max: 400 }}
            onChange={(next) => patch({
              ...(next.strike_moneyness !== undefined ? { strike_moneyness: next.strike_moneyness } : {}),
              ...(next.scan_expiries_indices !== undefined ? { scan_expiries_indices: next.scan_expiries_indices } : {}),
              ...(next.expiry_dte_min !== undefined ? { expiry_dte_min: next.expiry_dte_min } : {}),
              ...(next.expiry_dte_max !== undefined ? { expiry_dte_max: next.expiry_dte_max } : {}),
              ...(next.avoid_expiry_day !== undefined ? { avoid_expiry_day: next.avoid_expiry_day } : {}),
            })}
          />
          <ConfigNote>
            Default cover after a CE/PE signal is 1 ATM + 2 ITM + 2 OTM. This uses SuperTrend’s listed-strike
            resolver. It is not F-109.
          </ConfigNote>
        </Section>

        <Section
          title="Trail tightness"
          description="How the stop follows once a trade is running."
          summary={`${draft.trail_points} pt trail · lock at ${draft.profit_lock_activation_points}`}
          defaultOpen
          persistKey="ae-trail"
        >
          <Field label="Trail points" hint="Distance behind the best price. Adaptive Edge’s own trail, not a SuperTrend line.">
            <input style={inputStyle} type="number" value={draft.trail_points} onChange={(e) => patch({ trail_points: Number(e.target.value) })} />
          </Field>
          <Field label="Lock arm" hint="Open profit before the profit-lock turns on.">
            <input style={inputStyle} type="number" value={draft.profit_lock_activation_points} onChange={(e) => patch({ profit_lock_activation_points: Number(e.target.value) })} />
          </Field>
          <Field label="Lock offset" hint="Kept off the extreme once the lock is armed.">
            <input style={inputStyle} type="number" value={draft.profit_lock_offset_points} onChange={(e) => patch({ profit_lock_offset_points: Number(e.target.value) })} />
          </Field>
        <InertNote replacement=" Use Risk and session for the live stop and target." />
          </Section>

        <Section
          title="Exit rule"
          description="What closes an Adaptive Edge trade."
          summary={`${draft.stop_points} pt stop · 14:45 flatten`}
          defaultOpen
          persistKey="ae-exit"
        >
          <Field label="Stop points" hint="Hard stop from the spot entry.">
            <input style={inputStyle} type="number" value={draft.stop_points} onChange={(e) => patch({ stop_points: Number(e.target.value) })} />
          </Field>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '4px 0 8px' }}>
            <Switch checked disabled label="Flatten at 14:45 IST" onChange={() => undefined} />
            <span style={{ color: TEXT, fontSize: 11.5 }}>14:45 IST session cutoff is required</span>
          </div>
          <ConfigNote>
            A position flattens on the first of: hard stop, trail, profit-lock, thesis invalid, economic
            collapse, or the 14:45 IST session cutoff. Entry is still one position at a time. Automatic
            Kite orders stay blocked.
          </ConfigNote>
        <InertNote replacement=" Use Risk and session for the live stop and target." />
          </Section>

        <EngineVersionSection />
        <EngineRiskSection />

        <Section
          title="Daily drawdown circuit breaker"
          description="Hard portfolio stop that halts new trade entries."
          summary={draft.drawdown_circuit_breaker_enabled ?? true ? `Active · max -${draft.max_daily_drawdown_pct ?? 3.0}%` : 'Disabled'}
          defaultOpen
          persistKey="ae-drawdown-breaker"
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0 12px' }}>
            <Switch
              checked={draft.drawdown_circuit_breaker_enabled ?? true}
              label="Enable daily drawdown circuit breaker"
              onChange={() => patch({ drawdown_circuit_breaker_enabled: !(draft.drawdown_circuit_breaker_enabled ?? true) })}
            />
            <span style={{ color: (draft.drawdown_circuit_breaker_enabled ?? true) ? 'var(--k-green-deep)' : MUTED, fontSize: 11.5, fontWeight: 500 }}>
              {(draft.drawdown_circuit_breaker_enabled ?? true) ? 'Active' : 'Disabled'}
            </span>
          </div>
          {(draft.drawdown_circuit_breaker_enabled ?? true) && (
            <Field label="Max daily loss (%)" hint="Maximum portfolio drawdown before tripping breaker (default: 3.0%).">
              <input
                style={inputStyle}
                type="number"
                step="0.5"
                min="0.5"
                max="10.0"
                value={draft.max_daily_drawdown_pct ?? 3.0}
                onChange={(e) => patch({ max_daily_drawdown_pct: Math.max(0.5, Math.min(10.0, Number(e.target.value))) })}
              />
            </Field>
          )}
          <ConfigNote>
            Trips automatically if daily realized + unrealized loss reaches -{(draft.max_daily_drawdown_pct ?? 3.0).toFixed(1)}% of portfolio equity,
            disarming all automated buy triggers to protect the &lt;4.5% institutional drawdown ceiling.
          </ConfigNote>
        </Section>
      </PanelCard>

      <Section title="Structure" description="Profile bin size and opening-range length on the Adaptive Edge chart." summary={`${draft.tick_size} pt · IB ${draft.ib_minutes}m`} persistKey="ae-structure">
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '4px 2px 10px' }}>
          <Field label="Tick size" hint="Profile bin size in points.">
            <input style={inputStyle} type="number" value={draft.tick_size} onChange={(e) => patch({ tick_size: Number(e.target.value) })} />
          </Field>
          <Field label="IB minutes" hint="Opening-range / initial balance.">
            <input style={inputStyle} type="number" value={draft.ib_minutes} onChange={(e) => patch({ ib_minutes: Number(e.target.value) })} />
          </Field>
        </div>
      <InertNote replacement="" />
      </Section>

      <Section title="Mode rungs" description="Open profit needed to step MICRO to SCALP to EXTENDED SCALP to INTRADAY." summary={`${draft.persistence_bars} bars · ${draft.scalp_favorable_points}/${draft.extended_favorable_points}/${draft.intraday_favorable_points}`} persistKey="ae-modes">
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '4px 2px 10px' }}>
          <Field label="Persistence bars" hint="Hysteresis before a mode change.">
            <input style={inputStyle} type="number" value={draft.persistence_bars} onChange={(e) => patch({ persistence_bars: Number(e.target.value) })} />
          </Field>
          <Field label="Scalp +" hint="Favorable points for SCALP.">
            <input style={inputStyle} type="number" value={draft.scalp_favorable_points} onChange={(e) => patch({ scalp_favorable_points: Number(e.target.value) })} />
          </Field>
          <Field label="Extended +" hint="Favorable points for EXTENDED_SCALP.">
            <input style={inputStyle} type="number" value={draft.extended_favorable_points} onChange={(e) => patch({ extended_favorable_points: Number(e.target.value) })} />
          </Field>
          <Field label="Intraday +" hint="Favorable points for INTRADAY.">
            <input style={inputStyle} type="number" value={draft.intraday_favorable_points} onChange={(e) => patch({ intraday_favorable_points: Number(e.target.value) })} />
          </Field>
        </div>
      <InertNote replacement="" />
      </Section>

      <Section
        title="Readiness"
        description="What the last snapshot has and what is still waiting."
        summary={snapshot.data?.software_complete ? 'board ready · orders off' : 'waiting on snapshot'}
        persistKey="ae-readiness"
      >
        <div style={{ padding: '4px 2px 10px', display: 'grid', gap: 6 }}>
          {(snapshot.data?.readiness ?? []).map((item) => (
            <div key={item.name} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12 }}>
              <span style={{ color: TEXT }}>{item.name.split('_').join(' ')}</span>
              <span style={{ color: item.ready ? 'var(--k-green-deep)' : '#b85c00' }}>{item.ready ? 'ready' : 'blocked'}</span>
            </div>
          ))}
          {!snapshot.data && <div style={{ color: MUTED, fontSize: 12 }}>Snapshot not loaded yet.</div>}
        </div>
      </Section>

      <SettingsDraftBar
        dirty={dirty}
        saving={save.isPending}
        onApply={() => { if (!invalid) save.mutate(draft, { onSuccess: () => setDirty(false) }); }}
        onDiscard={() => { if (data) { setDraft(withDefaults(data.settings)); setDirty(false); } }}
        onReset={() => { if (data) { setDraft(withDefaults(data.settings)); setDirty(false); } }}
      />

      <style>{`
        @media (max-width: 640px) {
          .sk-config-summary { display: none; }
          .sk-config-section-body { padding: 0 14px 18px !important; }
          .sk-config-field { grid-template-columns: 1fr !important; gap: 8px !important; }
          .sk-config-check-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </>
  );
}

export default AdaptiveEdgeSettingsPanel;
