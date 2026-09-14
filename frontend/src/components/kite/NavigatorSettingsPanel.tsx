import React from 'react';
import { BORDER, ChoiceRow, DIM, Field, MUTED, ORANGE, Section, SOFT, Switch, TEXT, inputStyle } from './kiteSettingsPrimitives';
import { Icons } from '../../styles/kiteUI';
import { SCAN_SOURCE_OPTIONS } from './config/registry';
import { AdvancedSection, PanelCard, SettingsDraftBar } from './config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';
import { EnginePowerHeader } from './config/EnginePowerHeader';
import { ScopeLink, ScopedGroup } from './config/EngineScope';
import { ContractsGroup, InstrumentsGroup, SignalSourceGroup } from './config/ScanSettings';
import { useNavigatorConfig, useResetNavigatorConfig, useSetNavigatorConfig } from '../../hooks/useNavigator';
import { notifyOrder } from '../../store/useKiteNotifications';
import { useEngineConfig } from '../../hooks/useSterlingKiteEngine';
import type { EngineConfigModel } from '../../types/kiteEngine';
import type {
  AvwapGrade, NavigatorConfigModel, NavigatorOperatingMode, SignalOrigination,
} from '../../types/navigator';
import {
  HARDCODED_MANUAL_RULES, MANUAL_FIELDS, getManualFieldValue, resetManualField,
  type ManualFieldSpec,
} from './navigatorManualDefaults';
import {
  AVWAP_DEFAULTS, FLOW_DEFAULTS, FUSION_DEFAULTS, GAMMA_DEFAULTS, RANGES_DEFAULTS, ROOT_DEFAULTS, VOLATILITY_DEFAULTS,
} from './navigatorDefaults';

const GREEN = 'var(--k-green)';
const RED = 'var(--k-red)';
const AMBER = 'var(--k-amber)';
const MANUAL_BLUE = 'var(--k-blue-deep)';

const NUM_INPUT_CSS = `
.nav-settings-input::-webkit-outer-spin-button,
.nav-settings-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.nav-settings-input { -moz-appearance: textfield; }
.nav-settings-input:focus { outline: none; box-shadow: 0 0 0 2px rgba(240,100,40,.25); }
`;

function roundToStep(value: number, step: number): number {
  const decimals = (String(step).split('.')[1] || '').length;
  return Number(value.toFixed(decimals));
}

const stepperButtonStyle: React.CSSProperties = {
  flex: 1, border: 'none', background: 'transparent', padding: 0, cursor: 'pointer',
  display: 'flex', alignItems: 'center', justifyContent: 'center', color: DIM, lineHeight: 1, fontSize: 7,
};

function NumberInput({ value, onChange, step = 1, min, max, style, ariaLabel, placeholder }: {
  value: number | null; onChange: (v: number) => void; step?: number; min?: number; max?: number;
  style?: React.CSSProperties; ariaLabel: string; placeholder?: string;
}) {
  const clamp = (v: number) => {
    let next = v;
    if (min !== undefined) next = Math.max(min, next);
    if (max !== undefined) next = Math.min(max, next);
    return roundToStep(next, step);
  };
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <input
        className="nav-settings-input"
        type="number" value={value ?? ''} step={step} min={min} max={max} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
        style={{ ...style, paddingRight: 22 }} aria-label={ariaLabel}
      />
      <div style={{ position: 'absolute', right: 2, top: 2, bottom: 2, width: 16, display: 'flex', flexDirection: 'column' }}>
        <button type="button" tabIndex={-1} aria-label={`Increase ${ariaLabel}`} onClick={() => onChange(clamp((value ?? 0) + step))} style={stepperButtonStyle}>▲</button>
        <button type="button" tabIndex={-1} aria-label={`Decrease ${ariaLabel}`} onClick={() => onChange(clamp((value ?? 0) - step))} style={stepperButtonStyle}>▼</button>
      </div>
    </div>
  );
}

function StrategyDefinitionGroup({ badgeText, children }: { badgeText: string; children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return (
    <details
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      style={{ borderTop: `3px solid ${AMBER}`, background: '#fdf8f0' }}
    >
      <summary style={{ listStyle: 'none', cursor: 'pointer', padding: '16px 18px', display: 'flex', alignItems: 'center', gap: 12, userSelect: 'none' }}>
        <span aria-hidden style={{ width: 18, color: AMBER, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .16s ease' }}>›</span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ color: '#8a5a00', fontSize: 12.5, fontWeight: 800 }}>Strategy definition (from the source manual)</div>
          <div style={{ color: MUTED, fontSize: 10.5, lineHeight: 1.5, marginTop: 3 }}>
            These 6 settings ARE the strategy — from the manual this app is built on.
            Kept nested so they are not changed by accident while tuning other knobs.
          </div>
        </div>
        <span style={{ fontSize: 9.5, fontWeight: 700, color: AMBER, background: 'var(--k-tint-amber)', border: `1px solid ${AMBER}66`, borderRadius: 4, padding: '2px 8px', flexShrink: 0, whiteSpace: 'nowrap' }}>
          {badgeText}
        </span>
      </summary>
      <div style={{ background: 'var(--k-bg)' }}>{children}</div>
    </details>
  );
}

const AT_DEFAULT_BORDER = '#a8d5aa';
const CHANGED_BORDER = AMBER;
const CHANGED_BG = '#fff8ec';

function fieldHighlightStyle(isDefault: boolean | null): React.CSSProperties {
  if (isDefault === null) return {};
  return isDefault
    ? { borderColor: AT_DEFAULT_BORDER }
    : { borderColor: CHANGED_BORDER, background: CHANGED_BG };
}

function RevertNote({ displayDefault, onRevert, sourceLabel = "Sterling's" }: { displayDefault: string; onRevert: () => void; sourceLabel?: string }) {
  return (
    <button
      type="button" onClick={onRevert} title={`Revert to ${sourceLabel} default (${displayDefault})`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 6, border: 'none', background: 'none', color: AMBER, fontSize: 9.5, fontWeight: 700, cursor: 'pointer', padding: 0, fontFamily: 'inherit' }}
    >
      <Icons.Reload /> was {displayDefault} — revert
    </button>
  );
}

function seedCustomScope(
  draft: NavigatorConfigModel, engineCfg: EngineConfigModel | undefined,
): NavigatorConfigModel {
  const alreadyConfigured = draft.scan_indices.length || draft.scan_stocks.length || draft.scan_all_stocks;
  if (alreadyConfigured) return { ...draft, scan_scope_mode: 'custom' };
  return {
    ...draft,
    scan_scope_mode: 'custom',
    scan_indices: engineCfg?.scan_indices ?? ['NIFTY 50'],
    scan_stocks: engineCfg?.scan_stocks ?? [],
    scan_all_stocks: engineCfg?.scan_all_stocks ?? false,
    scan_stock_contracts: engineCfg?.scan_stock_contracts ?? true,
    scan_source: engineCfg?.scan_source === 'both' || engineCfg?.scan_source === 'confluence'
      || engineCfg?.scan_source === 'derivatives' ? engineCfg.scan_source : 'spot',
  };
}

const ORIGINATION_EXPLAIN: Record<SignalOrigination, string> = {
  off: "Unchanged: Navigator only ever comments on a setup that SuperTrend already found. It never adds a new row by itself.",
  heads_up: 'Navigator can show its own idea, even when SuperTrend found nothing. You\'ll see it as a "Navigator idea" row — but you can\'t trade it, it\'s just there to look at.',
  full: "Same as Heads-up, but now you can actually trade it — Navigator picks a real strike, and the row works like any other one.",
};

function NumField({ label, hint, value, onChange, step = 1, min, max, defaultValue }: {
  label: string; hint?: string; value: number; onChange: (v: number) => void;
  step?: number; min?: number; max?: number; defaultValue?: number;
}) {
  const isDefault = defaultValue === undefined ? null : value === defaultValue;
  return (
    <Field label={label} hint={hint}>
      <NumberInput
        value={Number.isFinite(value) ? value : 0} step={step} min={min} max={max}
        onChange={onChange}
        style={{ ...inputStyle, ...fieldHighlightStyle(isDefault) }} ariaLabel={label}
      />
      {isDefault === false && (
        <RevertNote displayDefault={String(defaultValue)} onRevert={() => onChange(defaultValue as number)} />
      )}
    </Field>
  );
}

function BoolField({ label, hint, value, onChange, defaultValue }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void; defaultValue?: boolean }) {
  const isDefault = defaultValue === undefined ? null : value === defaultValue;
  return (
    <Field label={label} hint={hint}>
      <div style={{ display: 'inline-flex', borderRadius: 14, ...(isDefault === false ? { boxShadow: `0 0 0 2px ${CHANGED_BORDER}40` } : {}) }}>
        <Switch checked={value} label={label} onChange={() => onChange(!value)} />
      </div>
      {isDefault === false && (
        <RevertNote displayDefault={defaultValue ? 'On' : 'Off'} onRevert={() => onChange(defaultValue as boolean)} />
      )}
    </Field>
  );
}

function ManualControl({ spec, config, onReset, children }: {
  spec: ManualFieldSpec; config: NavigatorConfigModel; onReset: () => void; children: React.ReactNode;
}) {
  const value = getManualFieldValue(config, spec.path);
  const isDefault = value === spec.defaultValue;
  return (
    <Field label={spec.label}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ ...(isDefault ? { boxShadow: `0 0 0 2px ${MANUAL_BLUE}30`, borderRadius: 8 } : { boxShadow: `0 0 0 2px ${CHANGED_BORDER}40`, borderRadius: 8 }) }}>
          {children}
        </div>
        <span
          aria-label="From the source manual"
          style={{ flexShrink: 0, width: 16, height: 16, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 9.5, fontWeight: 800, color: 'var(--k-bg)', background: MANUAL_BLUE, borderRadius: '50%' }}
        >
          M
        </span>
      </div>
      <div style={{ color: MUTED, fontSize: 11, lineHeight: 1.5, marginTop: 6 }}>
        {spec.plainExplain} <span style={{ color: DIM, fontSize: 9.5 }}>({spec.source})</span>
      </div>
      {!isDefault && <RevertNote displayDefault={spec.displayDefault} onRevert={onReset} sourceLabel="the manual's" />}
    </Field>
  );
}

function set<K extends keyof NavigatorConfigModel>(
  draft: NavigatorConfigModel, key: K, patch: Partial<NavigatorConfigModel[K]>,
): NavigatorConfigModel {
  return { ...draft, [key]: { ...(draft[key] as object), ...patch } };
}


export function NavigatorSettingsPanel() {
  const { data, isLoading, error: loadError, refetch } = useNavigatorConfig();
  const setConfig = useSetNavigatorConfig();
  const resetConfig = useResetNavigatorConfig();
  const { data: engineCfg } = useEngineConfig();

  const [draft, setDraft] = React.useState<NavigatorConfigModel | null>(null);
  const [baseRevision, setBaseRevision] = React.useState<number | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [conflict, setConflict] = React.useState<boolean>(false);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  // Leaving this section unmounts the panel and the draft with it.
  useUnsavedDraftGuard('navigator', dirty);

  React.useEffect(() => {
    if (!data) return;
    if (!dirty) {
      setDraft(data.record.config);
      setBaseRevision(data.record.revision);
      setConflict(false);
    }
  }, [data, dirty]);

  if (isLoading || !draft) {
    return <div style={{ padding: 18, color: DIM, fontSize: 12 }}>Loading Navigator configuration…</div>;
  }
  if (loadError) {
    return <div style={{ padding: 18, color: RED, fontSize: 12 }}>Failed to load Navigator configuration: {String(loadError)}</div>;
  }

  const record = data!.record;
  const gateReady = record.calibration_readiness === 'ready';

  const patch = (next: NavigatorConfigModel) => {
    setDraft(next);
    setDirty(true);
  };

  const handleApply = async () => {
    if (!draft) return;
    let revision = baseRevision;

    if (conflict) {
      // The banner offers "Apply to overwrite", and that can only be true if we send
      // the revision the server holds NOW. `baseRevision` is refreshed only while
      // !dirty, and a conflict leaves us dirty by definition — so retrying resent the
      // exact revision the server had just rejected and 409'd forever. The user was
      // offered a choice (keep my edits, or reload and lose them) where only one side
      // worked.
      const fresh = await refetch();
      const latest = fresh.data?.record?.revision;
      if (latest == null) return;   // cannot learn it → leave the banner up
      revision = latest;
    }

    if (revision == null) return;
    setConfig.mutate({ config: draft, expected_revision: revision }, {
      onSuccess: (saved) => {
        setDirty(false);
        setConflict(false);
        setBaseRevision(saved?.record?.revision ?? null);
        notifyOrder({ kind: 'info', title: 'Settings saved', message: 'Navigator settings applied.' });
      },
      onError: (err) => {
        if (String(err.message).includes('REVISION_CONFLICT')) {
          setConflict(true);
        } else {
          notifyOrder({
            kind: 'error',
            title: 'Settings NOT saved',
            message: `Navigator settings were not applied: ${err?.message || 'save failed'}. Your changes are still here — try Apply again.`,
          });
        }
      },
    });
  };

  const handleReload = () => {
    if (data) {
      setDraft(data.record.config);
      setBaseRevision(data.record.revision);
    }
    setDirty(false);
    setConflict(false);
  };

  const handleReset = () => {
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }
    resetConfig.mutate(undefined, {
      onSuccess: () => { setDirty(false); setResetConfirm(false); },
      onError: (err) => {
        notifyOrder({ kind: 'error', title: 'Reset failed', message: err?.message || 'Could not reset Navigator settings.' });
      },
    });
  };

  const saveError = setConfig.isError && !conflict ? String(setConfig.error?.message ?? 'save failed') : null;

  // Stocks only count toward a non-empty universe when the single-stock master
  // switch is on. With it off, `select_scan_universe` drops every stock, so an
  // indices-empty scope with a full stock list scans NOTHING — and the old check
  // called that "not empty" because the stock list itself was non-empty, letting
  // Apply through with no warning.
  const stocksScanned = (draft.scan_stock_contracts ?? true)
    && (draft.scan_all_stocks || draft.scan_stocks.length > 0);
  const customScopeEmpty = draft.scan_scope_mode === 'custom'
    && !draft.scan_indices.length && !stocksScanned;
  const customScopeCount = !stocksScanned
    ? `${draft.scan_indices.length} indices · no stocks`
    : draft.scan_all_stocks
      ? `${draft.scan_indices.length} indices + all stocks`
      : `${draft.scan_indices.length + draft.scan_stocks.length} instruments`;

  const instrumentsSummary = draft.scan_scope_mode === 'shared'
    ? 'Like SuperTrend'
    : customScopeCount;

  return (
    <>
      <style>{NUM_INPUT_CSS}</style>

      <EnginePowerHeader
        name="Value-Flow Navigator"
        tagline="Anchored VWAP structure, projected ranges, volatility regime, option flow and gamma activity."
        on={draft.enabled}
        liveOn={data?.record?.config?.enabled ?? draft.enabled}
        busy={setConfig.isPending}
        onToggle={() => patch({ ...draft, enabled: !draft.enabled })}
        runningNote="Reading structure for its instruments. It can confirm SuperTrend setups and find its own."
        offNote="Not scanning. SuperTrend can still run on its own."
      >
        {conflict && (
          <div style={{ margin: '0 18px 12px', padding: '9px 11px', borderRadius: 7, background: 'var(--k-surface-warm)', border: `1px solid var(--k-border-brand)`, display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
            <Icons.Warning />
            <span style={{ flex: 1, color: TEXT }}>This config changed elsewhere. Reload or Apply to overwrite.</span>
            <button type="button" onClick={handleReload} style={{ ...pillButtonStyle }}>Reload latest</button>
          </div>
        )}
        {saveError && (
          <div style={{ margin: '0 18px 12px', padding: '9px 11px', borderRadius: 7, background: '#fff0f0', border: `1px solid #e2a4a4`, color: RED, fontSize: 11, display: 'flex', gap: 8, alignItems: 'center' }}>
            <Icons.Warning /> {saveError}
          </div>
        )}
      </EnginePowerHeader>

      <PanelCard>
      {/* ═══════════════ CORE (order matches SuperTrend) ═══════════════ */}
      <Section
        title="Chart source"
        description={"The chart this engine takes its entry signal off.\nAlways its own — SuperTrend's source is never applied here."}
        summary={SCAN_SOURCE_OPTIONS.find((o) => o.value === draft.scan_source)?.label ?? draft.scan_source}
        defaultOpen
        persistKey="nav-chart"
      >
        <div>
          <SignalSourceGroup
            name="navigator-signal-source"
            value={draft.scan_source}
            fieldHint={null}
            onChange={(v) => patch({ ...draft, scan_source: v })}
          />
        </div>
      </Section>

      <Section
        title="Instruments"
        description="The indices and stocks Navigator watches."
        summary={instrumentsSummary}
        defaultOpen
        headerAction={(
          <ScopeLink
            groupLabel="Instruments"
            linked={draft.scan_scope_mode === 'shared'}
            onChange={(linked) => patch(linked
              ? { ...draft, scan_scope_mode: 'shared' }
              : seedCustomScope(draft, engineCfg))}
            ownLabel="Own"
            sharedLabel="Like SuperTrend"
          />
        )}
        persistKey="nav-instruments"
      >
        <ScopedGroup
          title="Instruments"
          description="The indices and stocks Navigator watches."
          linked={draft.scan_scope_mode === 'shared'}
          hideLink
          onLinkChange={(linked) => patch(linked
            ? { ...draft, scan_scope_mode: 'shared' }
            : seedCustomScope(draft, engineCfg))}
          sharedSummary={engineCfg?.scan_indices ? (
            <>
              Following SuperTrend:{' '}
              {!(engineCfg.scan_stock_contracts ?? true)
                ? `${engineCfg.scan_indices.length} indices, no stocks`
                : engineCfg.scan_all_stocks
                  ? `${engineCfg.scan_indices.length} indices + all F&O stocks`
                  : `${engineCfg.scan_indices.length} indices + ${(engineCfg.scan_stocks ?? []).length} stocks`}.
            </>
          ) : 'Following SuperTrend.'}
        >
          <InstrumentsGroup
            idPrefix="Navigator"
            allowEmptyIndices
            indices={draft.scan_indices}
            stocks={draft.scan_stocks}
            allStocks={draft.scan_all_stocks}
            stockContracts={draft.scan_stock_contracts ?? true}
            onChange={(next) => patch({
              ...draft,
              ...(next.scan_indices !== undefined ? { scan_indices: next.scan_indices } : {}),
              ...(next.scan_stocks !== undefined ? { scan_stocks: next.scan_stocks } : {}),
              ...(next.scan_all_stocks !== undefined ? { scan_all_stocks: next.scan_all_stocks } : {}),
              ...(next.scan_stock_contracts !== undefined ? { scan_stock_contracts: next.scan_stock_contracts } : {}),
            })}
          />
          {customScopeEmpty && (
            <div style={{ padding: '9px 11px', borderRadius: 7, background: 'var(--k-surface-warm)', border: '1px solid var(--k-border-brand)', color: TEXT, fontSize: 11, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Icons.Warning />
              Pick at least one index or stock — an empty list means Navigator scans nothing at all.
            </div>
          )}
        </ScopedGroup>

      </Section>

      <Section
        title="Contracts"
        description="Which strikes and expiry cycles Navigator resolves for its own setups."
        summary={
          draft.strike_moneyness == null
            ? 'Like SuperTrend'
            : `${draft.strike_moneyness.length} strikes`
        }
        defaultOpen
        headerAction={(
          <ScopeLink
            groupLabel="Contracts"
            linked={draft.strike_moneyness == null}
            onChange={(linked) => patch(linked
              ? { ...draft, strike_moneyness: null, scan_expiries_indices: null }
              : {
                  ...draft,
                  strike_moneyness: engineCfg?.strike_moneyness ?? ['ITM1', 'ATM', 'OTM1'],
                  scan_expiries_indices: engineCfg?.scan_expiries_indices ?? engineCfg?.scan_expiries ?? ['weekly', 'monthly'],
                })}
            ownLabel="Own"
            sharedLabel="Like SuperTrend"
          />
        )}
        persistKey="nav-contracts"
      >
        <ScopedGroup
          title="Contracts"
          description="Which strikes and expiry cycles Navigator resolves for its own setups."
          linked={draft.strike_moneyness == null}
          hideLink
          onLinkChange={(linked) => patch(linked
            ? { ...draft, strike_moneyness: null, scan_expiries_indices: null }
            : {
                ...draft,
                strike_moneyness: engineCfg?.strike_moneyness ?? ['ITM1', 'ATM', 'OTM1'],
                scan_expiries_indices: engineCfg?.scan_expiries_indices ?? engineCfg?.scan_expiries ?? ['weekly', 'monthly'],
              })}
          sharedSummary={engineCfg?.strike_moneyness
            ? `Following SuperTrend: ${engineCfg.strike_moneyness.length} strikes · ${(engineCfg.scan_expiries_indices ?? engineCfg.scan_expiries ?? ['weekly', 'monthly']).join(' + ')}.`
            : 'Following SuperTrend.'}
        >
          <ContractsGroup
            strikes={draft.strike_moneyness ?? engineCfg?.strike_moneyness ?? ['ATM']}
            indexExpiries={draft.scan_expiries_indices ?? ['weekly', 'monthly']}
            /* Unset means "follow SuperTrend", the same rule the strike ladder
               and expiry cycles above already use — so the box shows what
               Navigator will actually do, not a blank. */
            dteMin={draft.expiry_dte_min ?? engineCfg?.expiry_dte_min ?? 0}
            dteMax={draft.expiry_dte_max ?? engineCfg?.expiry_dte_max ?? 400}
            avoidExpiryDay={draft.avoid_expiry_day ?? engineCfg?.avoid_expiry_day ?? false}
            dteDefaults={{ min: engineCfg?.expiry_dte_min ?? 0,
                           max: engineCfg?.expiry_dte_max ?? 400 }}
            dteNote="Left at SuperTrend's values these follow it; change one and Navigator keeps its own."
            onChange={(next) => patch({
              ...draft,
              ...(next.strike_moneyness !== undefined ? { strike_moneyness: next.strike_moneyness } : {}),
              ...(next.scan_expiries_indices !== undefined ? { scan_expiries_indices: next.scan_expiries_indices } : {}),
              ...(next.expiry_dte_min !== undefined ? { expiry_dte_min: next.expiry_dte_min } : {}),
              ...(next.expiry_dte_max !== undefined ? { expiry_dte_max: next.expiry_dte_max } : {}),
              ...(next.avoid_expiry_day !== undefined ? { avoid_expiry_day: next.avoid_expiry_day } : {}),
            })}
          />
        </ScopedGroup>
      </Section>

      <Section
        title="Structure Radar and Signal Origination"
        description="Optional: let Navigator find and show its own setups, without waiting for SuperTrend. Off by default."
        summary={draft.signal_origination === 'off' ? (draft.structure_radar_enabled ? 'Radar only' : 'Off') : `Origination: ${draft.signal_origination === 'heads_up' ? 'Heads-up' : 'Full'}`}
        defaultOpen
        persistKey="nav-radar"
      >
        <BoolField
          label="Structure Radar"
          hint="Keeps reading structure even when SuperTrend has nothing live. Never adds a row by itself."
          value={draft.structure_radar_enabled}
          onChange={(v) => patch({ ...draft, structure_radar_enabled: v })}
          defaultValue={ROOT_DEFAULTS.structure_radar_enabled}
        />
        <Field label="Signal Origination" hint="Whether Navigator's own ideas (no SuperTrend needed) show up as a row.">
          <ChoiceRow<SignalOrigination>
            value={draft.signal_origination}
            onChange={(v) => patch({ ...draft, signal_origination: v, ...(v !== 'full' ? { auto_execute_originated: false } : {}) })}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'heads_up', label: 'Heads-up' },
              { value: 'full', label: 'Full' },
            ]}
          />
          <div style={{ color: MUTED, fontSize: 11, lineHeight: 1.5, marginTop: 8 }}>{ORIGINATION_EXPLAIN[draft.signal_origination]}</div>
          {draft.signal_origination !== ROOT_DEFAULTS.signal_origination && (
            <RevertNote displayDefault="Off" onRevert={() => patch({ ...draft, signal_origination: ROOT_DEFAULTS.signal_origination, auto_execute_originated: false })} />
          )}
        </Field>
        <BoolField
          label="Auto-Execute Originated"
          hint={
            draft.signal_origination !== 'full'
              ? 'Needs Signal Origination set to Full first.'
              : gateReady
                ? "Lets Navigator's own ideas trade automatically. Also needs the engine Auto-Execute on."
                : 'Locked — calibration not ready (same lock as Gate mode).'
          }
          value={draft.auto_execute_originated}
          onChange={(v) => {
            if (draft.signal_origination !== 'full' || !gateReady) return;
            patch({ ...draft, auto_execute_originated: v });
          }}
          defaultValue={ROOT_DEFAULTS.auto_execute_originated}
        />
      </Section>

      {/* ═══════════════ ADVANCED — fine-tuning ═══════════════ */}

      <Section
        title="Mode"
        description="How Navigator uses its reads — observe, advise, or gate entries."
        summary={
          draft.operating_mode === 'gate'
            ? (gateReady ? 'Gate' : 'Gate (locked)')
            : draft.operating_mode === 'advisory' ? 'Advisory' : 'Shadow'
        }
        defaultOpen
        persistKey="nav-mode"
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 14 }}>
          <div style={{ minWidth: 0, flex: '1 1 220px' }}>
            <ChoiceRow
              value={draft.operating_mode}
              onChange={(mode) => {
                if (mode === 'gate' && !gateReady) return;
                patch({ ...draft, operating_mode: mode });
              }}
              options={[
                { value: 'shadow', label: 'Shadow' },
                { value: 'advisory', label: 'Advisory' },
                { value: 'gate', label: gateReady ? 'Gate' : 'Gate (locked)' },
              ]}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: gateReady ? GREEN : DIM, fontSize: 10.5 }}>
            <Icons.Pulse />
            {gateReady ? 'Calibration ready' : 'Gate unavailable — not yet calibrated'}
          </div>
          <div style={{ color: DIM, fontSize: 10.5 }}>Revision {record.revision}</div>
        </div>
      </Section>

      <AdvancedSection count={8}>
        <StrategyDefinitionGroup badgeText={`${MANUAL_FIELDS.filter((f) => getManualFieldValue(draft, f.path) === f.defaultValue).length}/${MANUAL_FIELDS.length} at manual default`}>
          <div style={{ padding: '4px 18px 18px' }}>
            <div style={{ color: MUTED, fontSize: 11, lineHeight: 1.55, marginBottom: 4 }}>
              These 6 numbers come from the guide this strategy is based on, not from Sterling.
            </div>
            <ManualControl spec={MANUAL_FIELDS[0]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[0].path))}>
              <ChoiceRow value={draft.flow.mode} onChange={(mode) => patch(set(draft, 'flow', { mode }))} options={[{ value: 'dynamic', label: 'Dynamic' }, { value: 'broad', label: 'Broad' }]} />
            </ManualControl>
            <ManualControl spec={MANUAL_FIELDS[1]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[1].path))}>
              <NumberInput value={draft.flow.strong_zone} min={0} max={100} onChange={(v) => patch(set(draft, 'flow', { strong_zone: v }))} style={inputStyle} ariaLabel="Strong flow zone" />
            </ManualControl>
            <ManualControl spec={MANUAL_FIELDS[2]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[2].path))}>
              <NumberInput value={draft.flow.extreme_zone} min={0} max={100} onChange={(v) => patch(set(draft, 'flow', { extreme_zone: v }))} style={inputStyle} ariaLabel="Extreme flow zone" />
            </ManualControl>
            <ManualControl spec={MANUAL_FIELDS[3]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[3].path))}>
              <Switch checked={draft.gamma.require_flow_alignment} label="Gamma requires flow alignment" onChange={() => patch(set(draft, 'gamma', { require_flow_alignment: !draft.gamma.require_flow_alignment }))} />
            </ManualControl>
            <ManualControl spec={MANUAL_FIELDS[4]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[4].path))}>
              <ChoiceRow<AvwapGrade> value={draft.fusion.min_avwap_grade} onChange={(v) => patch(set(draft, 'fusion', { min_avwap_grade: v }))} options={[
                { value: 'B', label: 'B' }, { value: 'A', label: 'A' }, { value: 'A+', label: 'A+' },
              ]} />
            </ManualControl>
            <ManualControl spec={MANUAL_FIELDS[5]} config={draft} onReset={() => patch(resetManualField(draft, MANUAL_FIELDS[5].path))}>
              <NumberInput value={draft.volatility.min_direction_confidence} min={0} max={100} onChange={(v) => patch(set(draft, 'volatility', { min_direction_confidence: v }))} style={inputStyle} ariaLabel="Minimum directional confidence" />
            </ManualControl>
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px dashed ${BORDER}` }}>
              <div style={{ color: DIM, fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', marginBottom: 8 }}>
                Also from the manual — always on
              </div>
              {HARDCODED_MANUAL_RULES.map((rule) => (
                <div key={rule.label} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '6px 0' }}>
                  <span aria-hidden style={{ width: 6, height: 6, borderRadius: 3, background: MANUAL_BLUE, flexShrink: 0, marginTop: 5 }} />
                  <div>
                    <div style={{ fontSize: 11.5, color: TEXT, fontWeight: 650 }}>{rule.label}</div>
                    <div style={{ fontSize: 10.5, color: MUTED, lineHeight: 1.45, marginTop: 2 }}>{rule.note}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </StrategyDefinitionGroup>

        <Section title="Timing" description="Sampling and freshness windows." summary={`${draft.flow_sample_seconds}s sample`}>
          <Field label="Price timeframe" hint="Read-only in v1 — matches the Kite engine 1H clock.">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>60 minute</div>
          </Field>
          <NumField label="Flow sample interval" hint="Seconds between option-chain samples (15–300)." value={draft.flow_sample_seconds} step={5} min={15} max={300} onChange={(v) => patch({ ...draft, flow_sample_seconds: v })} defaultValue={ROOT_DEFAULTS.flow_sample_seconds} />
          <NumField label="Max feature age" hint="Seconds before cached evidence is treated as stale." value={draft.max_feature_age_seconds} step={10} min={10} max={3600} onChange={(v) => patch({ ...draft, max_feature_age_seconds: v })} defaultValue={ROOT_DEFAULTS.max_feature_age_seconds} />
          <NumField label="Event alignment window" hint="Bars of tolerance between a base-fresh and AVWAP-fresh trigger." value={draft.event_alignment_bars} step={1} min={0} max={20} onChange={(v) => patch({ ...draft, event_alignment_bars: v })} defaultValue={ROOT_DEFAULTS.event_alignment_bars} />
          <NumField label="Entry delay after open" hint="Minutes after session open before entries are considered." value={draft.entry_delay_after_open_minutes} step={1} min={0} max={60} onChange={(v) => patch({ ...draft, entry_delay_after_open_minutes: v })} defaultValue={ROOT_DEFAULTS.entry_delay_after_open_minutes} />
        </Section>

        <Section title="Anchored VWAP and signal grades" description="Structure, pullback/continuation, and grade thresholds." summary={draft.avwap.enabled ? 'Enabled' : 'Disabled'}>
          <BoolField label="Enabled" hint="Required for gate mode." value={draft.avwap.enabled} onChange={(v) => patch(set(draft, 'avwap', { enabled: v }))} defaultValue={AVWAP_DEFAULTS.enabled} />
          <NumField label="Pivot left bars" value={draft.avwap.pivot_left_bars} min={1} max={20} onChange={(v) => patch(set(draft, 'avwap', { pivot_left_bars: v }))} defaultValue={AVWAP_DEFAULTS.pivot_left_bars} />
          <NumField label="Pivot right bars" value={draft.avwap.pivot_right_bars} min={1} max={20} onChange={(v) => patch(set(draft, 'avwap', { pivot_right_bars: v }))} defaultValue={AVWAP_DEFAULTS.pivot_right_bars} />
          <NumField label="Slope lookback" value={draft.avwap.slope_lookback_bars} min={2} max={50} onChange={(v) => patch(set(draft, 'avwap', { slope_lookback_bars: v }))} defaultValue={AVWAP_DEFAULTS.slope_lookback_bars} />
          <NumField label="Min slope (ATR/bar)" value={draft.avwap.min_slope_atr_per_bar} step={0.01} min={0} max={2} onChange={(v) => patch(set(draft, 'avwap', { min_slope_atr_per_bar: v }))} defaultValue={AVWAP_DEFAULTS.min_slope_atr_per_bar} />
          <NumField label="ATR period" value={draft.avwap.atr_period} min={5} max={100} onChange={(v) => patch(set(draft, 'avwap', { atr_period: v }))} defaultValue={AVWAP_DEFAULTS.atr_period} />
          <NumField label="Relative volume period" value={draft.avwap.relative_volume_period} min={5} max={200} onChange={(v) => patch(set(draft, 'avwap', { relative_volume_period: v }))} defaultValue={AVWAP_DEFAULTS.relative_volume_period} />
          <NumField label="Touch tolerance (ATR)" value={draft.avwap.touch_tolerance_atr} step={0.01} min={0.01} max={1} onChange={(v) => patch(set(draft, 'avwap', { touch_tolerance_atr: v }))} defaultValue={AVWAP_DEFAULTS.touch_tolerance_atr} />
          <NumField label="Min body (ATR)" value={draft.avwap.min_body_atr} step={0.01} min={0} max={3} onChange={(v) => patch(set(draft, 'avwap', { min_body_atr: v }))} defaultValue={AVWAP_DEFAULTS.min_body_atr} />
          <NumField label="Min relative volume" value={draft.avwap.min_relative_volume} step={0.05} min={0} max={10} onChange={(v) => patch(set(draft, 'avwap', { min_relative_volume: v }))} defaultValue={AVWAP_DEFAULTS.min_relative_volume} />
          <NumField label="Breakout buffer (ATR)" value={draft.avwap.breakout_buffer_atr} step={0.01} min={0} max={2} onChange={(v) => patch(set(draft, 'avwap', { breakout_buffer_atr: v }))} defaultValue={AVWAP_DEFAULTS.breakout_buffer_atr} />
          <NumField label="Max extension (ATR)" value={draft.avwap.max_extension_atr} step={0.05} min={0.25} max={10} onChange={(v) => patch(set(draft, 'avwap', { max_extension_atr: v }))} defaultValue={AVWAP_DEFAULTS.max_extension_atr} />
          <NumField label="Cooldown bars" value={draft.avwap.cooldown_bars} min={0} max={100} onChange={(v) => patch(set(draft, 'avwap', { cooldown_bars: v }))} defaultValue={AVWAP_DEFAULTS.cooldown_bars} />
          <NumField label="Grade A+ min" value={draft.avwap.grade_a_plus_min} min={0} max={100} onChange={(v) => patch(set(draft, 'avwap', { grade_a_plus_min: v }))} defaultValue={AVWAP_DEFAULTS.grade_a_plus_min} />
          <NumField label="Grade A min" value={draft.avwap.grade_a_min} min={0} max={100} onChange={(v) => patch(set(draft, 'avwap', { grade_a_min: v }))} defaultValue={AVWAP_DEFAULTS.grade_a_min} />
          <NumField label="Grade B min" value={draft.avwap.grade_b_min} min={0} max={100} onChange={(v) => patch(set(draft, 'avwap', { grade_b_min: v }))} defaultValue={AVWAP_DEFAULTS.grade_b_min} />
          <NumField label="Stop buffer (ATR)" value={draft.avwap.stop_buffer_atr} step={0.01} min={0} max={3} onChange={(v) => patch(set(draft, 'avwap', { stop_buffer_atr: v }))} defaultValue={AVWAP_DEFAULTS.stop_buffer_atr} />
          <NumField label="Max stop distance (ATR)" value={draft.avwap.max_stop_distance_atr} step={0.05} min={0.1} max={20} onChange={(v) => patch(set(draft, 'avwap', { max_stop_distance_atr: v }))} defaultValue={AVWAP_DEFAULTS.max_stop_distance_atr} />
          <NumField label="Target R multiple" value={draft.avwap.target_r} step={0.1} min={0.5} max={10} onChange={(v) => patch(set(draft, 'avwap', { target_r: v }))} defaultValue={AVWAP_DEFAULTS.target_r} />
        </Section>

        <Section title="Daily and weekly ranges" description="Frozen projected ranges via rolling weighted quantiles." summary={`${Math.round(draft.ranges.target_coverage * 100)}% target coverage`}>
          <Field label="Method" hint="Versioned model — not free-form text."><div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>rolling_empirical_quantile_v1</div></Field>
          <NumField label="Target coverage" value={draft.ranges.target_coverage} step={0.01} min={0.01} max={0.99} onChange={(v) => patch(set(draft, 'ranges', { target_coverage: v }))} defaultValue={RANGES_DEFAULTS.target_coverage} />
          <NumField label="Daily lookback sessions" value={draft.ranges.daily_lookback_sessions} min={1} onChange={(v) => patch(set(draft, 'ranges', { daily_lookback_sessions: v }))} defaultValue={RANGES_DEFAULTS.daily_lookback_sessions} />
          <NumField label="Daily min sessions" value={draft.ranges.daily_min_sessions} min={1} onChange={(v) => patch(set(draft, 'ranges', { daily_min_sessions: v }))} defaultValue={RANGES_DEFAULTS.daily_min_sessions} />
          <NumField label="Weekly lookback periods" value={draft.ranges.weekly_lookback_periods} min={1} onChange={(v) => patch(set(draft, 'ranges', { weekly_lookback_periods: v }))} defaultValue={RANGES_DEFAULTS.weekly_lookback_periods} />
          <NumField label="Weekly min periods" value={draft.ranges.weekly_min_periods} min={1} onChange={(v) => patch(set(draft, 'ranges', { weekly_min_periods: v }))} defaultValue={RANGES_DEFAULTS.weekly_min_periods} />
          <BoolField label="Condition on volatility" value={draft.ranges.condition_on_volatility} onChange={(v) => patch(set(draft, 'ranges', { condition_on_volatility: v }))} defaultValue={RANGES_DEFAULTS.condition_on_volatility} />
          <NumField label="Min condition bucket" value={draft.ranges.min_condition_bucket} min={1} onChange={(v) => patch(set(draft, 'ranges', { min_condition_bucket: v }))} defaultValue={RANGES_DEFAULTS.min_condition_bucket} />
          <NumField label="Decay" value={draft.ranges.decay} step={0.01} min={0.9} max={1} onChange={(v) => patch(set(draft, 'ranges', { decay: v }))} defaultValue={RANGES_DEFAULTS.decay} />
          <NumField label="Edge tolerance (ATR)" value={draft.ranges.edge_tolerance_atr} step={0.01} min={0.01} onChange={(v) => patch(set(draft, 'ranges', { edge_tolerance_atr: v }))} defaultValue={RANGES_DEFAULTS.edge_tolerance_atr} />
        </Section>

        <Section title="Volatility regime" description="Expansion/compression classification and directional read." summary={draft.volatility.enabled ? 'Enabled' : 'Disabled'}>
          <BoolField label="Enabled" hint="Required for gate. Compression always forces WAIT." value={draft.volatility.enabled} onChange={(v) => patch(set(draft, 'volatility', { enabled: v }))} defaultValue={VOLATILITY_DEFAULTS.enabled} />
          <NumField label="ATR period" value={draft.volatility.atr_period} min={2} onChange={(v) => patch(set(draft, 'volatility', { atr_period: v }))} defaultValue={VOLATILITY_DEFAULTS.atr_period} />
          <NumField label="RV short bars" value={draft.volatility.rv_short_bars} min={2} onChange={(v) => patch(set(draft, 'volatility', { rv_short_bars: v }))} defaultValue={VOLATILITY_DEFAULTS.rv_short_bars} />
          <NumField label="RV long bars" value={draft.volatility.rv_long_bars} min={2} onChange={(v) => patch(set(draft, 'volatility', { rv_long_bars: v }))} defaultValue={VOLATILITY_DEFAULTS.rv_long_bars} />
          <NumField label="Bollinger band period" value={draft.volatility.band_period} min={2} onChange={(v) => patch(set(draft, 'volatility', { band_period: v }))} defaultValue={VOLATILITY_DEFAULTS.band_period} />
          <NumField label="Band std-dev" value={draft.volatility.band_stddev} step={0.1} min={0.1} onChange={(v) => patch(set(draft, 'volatility', { band_stddev: v }))} defaultValue={VOLATILITY_DEFAULTS.band_stddev} />
          <NumField label="Percentile lookback" value={draft.volatility.percentile_lookback} min={60} onChange={(v) => patch(set(draft, 'volatility', { percentile_lookback: v }))} defaultValue={VOLATILITY_DEFAULTS.percentile_lookback} />
          <NumField label="Gradient bars" value={draft.volatility.gradient_bars} min={2} max={50} onChange={(v) => patch(set(draft, 'volatility', { gradient_bars: v }))} defaultValue={VOLATILITY_DEFAULTS.gradient_bars} />
          <NumField label="Expansion min score" value={draft.volatility.expansion_min} min={0} max={100} onChange={(v) => patch(set(draft, 'volatility', { expansion_min: v }))} defaultValue={VOLATILITY_DEFAULTS.expansion_min} />
          <NumField label="Compression max score" value={draft.volatility.compression_max} min={0} max={100} onChange={(v) => patch(set(draft, 'volatility', { compression_max: v }))} defaultValue={VOLATILITY_DEFAULTS.compression_max} />
          <NumField label="ADX period" value={draft.volatility.adx_period} min={2} onChange={(v) => patch(set(draft, 'volatility', { adx_period: v }))} defaultValue={VOLATILITY_DEFAULTS.adx_period} />
          <NumField label="ADX min" value={draft.volatility.adx_min} min={0} max={100} onChange={(v) => patch(set(draft, 'volatility', { adx_min: v }))} defaultValue={VOLATILITY_DEFAULTS.adx_min} />
          <NumField label="EMA fast period" value={draft.volatility.ema_fast_period} min={1} onChange={(v) => patch(set(draft, 'volatility', { ema_fast_period: v }))} defaultValue={VOLATILITY_DEFAULTS.ema_fast_period} />
          <NumField label="EMA slow period" value={draft.volatility.ema_slow_period} min={2} onChange={(v) => patch(set(draft, 'volatility', { ema_slow_period: v }))} defaultValue={VOLATILITY_DEFAULTS.ema_slow_period} />
          <NumField label="Trend confirm bars" value={draft.volatility.trend_confirm_bars} min={1} onChange={(v) => patch(set(draft, 'volatility', { trend_confirm_bars: v }))} defaultValue={VOLATILITY_DEFAULTS.trend_confirm_bars} />
          <NumField label="Max flip age (bars)" value={draft.volatility.max_flip_age_bars} min={1} onChange={(v) => patch(set(draft, 'volatility', { max_flip_age_bars: v }))} defaultValue={VOLATILITY_DEFAULTS.max_flip_age_bars} />
          <Field label="Min direction confidence" hint="Set under Strategy definition (manual).">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>{draft.volatility.min_direction_confidence}</div>
          </Field>
        </Section>

        <Section title="Option-flow oscillator" description="Robust-normalized activity from chain samples." summary={draft.flow.mode}>
          <BoolField label="Enabled" value={draft.flow.enabled} onChange={(v) => patch(set(draft, 'flow', { enabled: v }))} defaultValue={FLOW_DEFAULTS.enabled} />
          <Field label="Mode" hint="Set under Strategy definition (manual).">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto', textTransform: 'capitalize' }}>{draft.flow.mode}</div>
          </Field>
          <NumField label="Dynamic strike radius" value={draft.flow.dynamic_strike_radius} min={1} max={20} onChange={(v) => patch(set(draft, 'flow', { dynamic_strike_radius: v }))} defaultValue={FLOW_DEFAULTS.dynamic_strike_radius} />
          <NumField label="Broad strike radius" value={draft.flow.broad_strike_radius} min={1} max={50} onChange={(v) => patch(set(draft, 'flow', { broad_strike_radius: v }))} defaultValue={FLOW_DEFAULTS.broad_strike_radius} />
          <NumField label="Max quote age (s)" value={draft.flow.max_quote_age_seconds} min={1} onChange={(v) => patch(set(draft, 'flow', { max_quote_age_seconds: v }))} defaultValue={FLOW_DEFAULTS.max_quote_age_seconds} />
          <NumField label="Max sample gap (s)" value={draft.flow.max_sample_gap_seconds} min={1} onChange={(v) => patch(set(draft, 'flow', { max_sample_gap_seconds: v }))} defaultValue={FLOW_DEFAULTS.max_sample_gap_seconds} />
          <NumField label="Min chain completeness" value={draft.flow.min_chain_completeness} step={0.05} min={0.01} max={1} onChange={(v) => patch(set(draft, 'flow', { min_chain_completeness: v }))} defaultValue={FLOW_DEFAULTS.min_chain_completeness} />
          <NumField label="Max spread %" value={draft.flow.max_spread_pct} step={0.01} min={0.01} max={1} onChange={(v) => patch(set(draft, 'flow', { max_spread_pct: v }))} defaultValue={FLOW_DEFAULTS.max_spread_pct} />
          <NumField label="Warmup samples" value={draft.flow.warmup_samples} min={1} onChange={(v) => patch(set(draft, 'flow', { warmup_samples: v }))} defaultValue={FLOW_DEFAULTS.warmup_samples} />
          <NumField label="Robust window samples" value={draft.flow.robust_window_samples} min={1} onChange={(v) => patch(set(draft, 'flow', { robust_window_samples: v }))} defaultValue={FLOW_DEFAULTS.robust_window_samples} />
          <NumField label="OI intensity weight" value={draft.flow.oi_intensity_weight} step={0.05} min={0} max={1} onChange={(v) => patch(set(draft, 'flow', { oi_intensity_weight: v }))} defaultValue={FLOW_DEFAULTS.oi_intensity_weight} />
          <NumField label="Z-scale" value={draft.flow.z_scale} step={0.1} min={0.1} onChange={(v) => patch(set(draft, 'flow', { z_scale: v }))} defaultValue={FLOW_DEFAULTS.z_scale} />
          <NumField label="Zero-line hysteresis" value={draft.flow.zero_hysteresis} min={0} max={100} onChange={(v) => patch(set(draft, 'flow', { zero_hysteresis: v }))} defaultValue={FLOW_DEFAULTS.zero_hysteresis} />
          <Field label="Strong / extreme zone" hint="Set under Strategy definition (manual).">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>{draft.flow.strong_zone} / {draft.flow.extreme_zone}</div>
          </Field>
          <BoolField label="Require for index gate" hint="Missing index flow blocks gate eligibility." value={draft.flow.require_for_index_gate} onChange={(v) => patch(set(draft, 'flow', { require_for_index_gate: v }))} defaultValue={FLOW_DEFAULTS.require_for_index_gate} />
        </Section>

        <Section title="Gamma activity" description="Confirmation-only. Never determines direction by itself." summary={draft.gamma.enabled ? 'Enabled' : 'Disabled'}>
          <BoolField label="Enabled" value={draft.gamma.enabled} onChange={(v) => patch(set(draft, 'gamma', { enabled: v }))} defaultValue={GAMMA_DEFAULTS.enabled} />
          <Field label="Risk-free rate" hint="Required for gamma — leave blank until set.">
            <NumberInput
              value={draft.gamma.risk_free_rate} step={0.001} placeholder="unset" ariaLabel="Risk-free rate"
              onChange={(v) => patch(set(draft, 'gamma', { risk_free_rate: v }))}
              style={{ ...inputStyle, ...fieldHighlightStyle(draft.gamma.risk_free_rate == null) }}
            />
            {draft.gamma.risk_free_rate != null && <RevertNote displayDefault="unset" onRevert={() => patch(set(draft, 'gamma', { risk_free_rate: null }))} />}
          </Field>
          <Field label="Dividend yield" hint="Required for gamma — leave blank until set.">
            <NumberInput
              value={draft.gamma.dividend_yield} step={0.001} placeholder="unset" ariaLabel="Dividend yield"
              onChange={(v) => patch(set(draft, 'gamma', { dividend_yield: v }))}
              style={{ ...inputStyle, ...fieldHighlightStyle(draft.gamma.dividend_yield == null) }}
            />
            {draft.gamma.dividend_yield != null && <RevertNote displayDefault="unset" onRevert={() => patch(set(draft, 'gamma', { dividend_yield: null }))} />}
          </Field>
          <NumField label="Min IV" value={draft.gamma.min_iv} step={0.001} min={0.001} onChange={(v) => patch(set(draft, 'gamma', { min_iv: v }))} defaultValue={GAMMA_DEFAULTS.min_iv} />
          <NumField label="Max IV" value={draft.gamma.max_iv} step={0.1} min={0.1} onChange={(v) => patch(set(draft, 'gamma', { max_iv: v }))} defaultValue={GAMMA_DEFAULTS.max_iv} />
          <NumField label="Robust window samples" value={draft.gamma.robust_window_samples} min={1} onChange={(v) => patch(set(draft, 'gamma', { robust_window_samples: v }))} defaultValue={GAMMA_DEFAULTS.robust_window_samples} />
          <NumField label="Min samples" value={draft.gamma.min_samples} min={1} onChange={(v) => patch(set(draft, 'gamma', { min_samples: v }))} defaultValue={GAMMA_DEFAULTS.min_samples} />
          <NumField label="Blast Z min" value={draft.gamma.blast_z_min} step={0.1} min={0.1} onChange={(v) => patch(set(draft, 'gamma', { blast_z_min: v }))} defaultValue={GAMMA_DEFAULTS.blast_z_min} />
          <NumField label="Acceleration Z min" value={draft.gamma.acceleration_z_min} step={0.1} min={0.1} onChange={(v) => patch(set(draft, 'gamma', { acceleration_z_min: v }))} defaultValue={GAMMA_DEFAULTS.acceleration_z_min} />
          <Field label="Expiry profile start (IST)">
            <input
              className="nav-settings-input"
              type="text" value={draft.gamma.expiry_profile_start_ist}
              onChange={(e) => patch(set(draft, 'gamma', { expiry_profile_start_ist: e.target.value }))}
              style={{ ...inputStyle, ...fieldHighlightStyle(draft.gamma.expiry_profile_start_ist === GAMMA_DEFAULTS.expiry_profile_start_ist) }}
            />
            {draft.gamma.expiry_profile_start_ist !== GAMMA_DEFAULTS.expiry_profile_start_ist && (
              <RevertNote displayDefault={GAMMA_DEFAULTS.expiry_profile_start_ist} onRevert={() => patch(set(draft, 'gamma', { expiry_profile_start_ist: GAMMA_DEFAULTS.expiry_profile_start_ist }))} />
            )}
          </Field>
          <Field label="Require flow alignment" hint="Set under Strategy definition (manual).">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>{draft.gamma.require_flow_alignment ? 'On' : 'Off'}</div>
          </Field>
          <BoolField label="Required for gate" hint="Off by default — missing gamma cannot boost score." value={draft.gamma.required_for_gate} onChange={(v) => patch(set(draft, 'gamma', { required_for_gate: v }))} defaultValue={GAMMA_DEFAULTS.required_for_gate} />
        </Section>

        <Section title="Fusion and eligibility" description="Component weights and status thresholds. Weights must sum to 100." summary={`${draft.fusion.base_weight + draft.fusion.avwap_weight + draft.fusion.volatility_weight + draft.fusion.flow_weight + draft.fusion.gamma_weight}% total`}>
          <NumField label="Base weight" value={draft.fusion.base_weight} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { base_weight: v }))} defaultValue={FUSION_DEFAULTS.base_weight} />
          <NumField label="AVWAP weight" value={draft.fusion.avwap_weight} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { avwap_weight: v }))} defaultValue={FUSION_DEFAULTS.avwap_weight} />
          <NumField label="Volatility weight" value={draft.fusion.volatility_weight} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { volatility_weight: v }))} defaultValue={FUSION_DEFAULTS.volatility_weight} />
          <NumField label="Flow weight" value={draft.fusion.flow_weight} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { flow_weight: v }))} defaultValue={FUSION_DEFAULTS.flow_weight} />
          <NumField label="Gamma weight" value={draft.fusion.gamma_weight} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { gamma_weight: v }))} defaultValue={FUSION_DEFAULTS.gamma_weight} />
          <Field label="Min AVWAP grade to confirm" hint="Set under Strategy definition (manual).">
            <div style={{ ...inputStyle, display: 'flex', alignItems: 'center', color: DIM, width: 'auto' }}>{draft.fusion.min_avwap_grade}</div>
          </Field>
          <NumField label="Strong conflict confidence" value={draft.fusion.strong_conflict_confidence} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { strong_conflict_confidence: v }))} defaultValue={FUSION_DEFAULTS.strong_conflict_confidence} />
          <NumField label="Confirmed score min" value={draft.fusion.confirmed_score_min} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { confirmed_score_min: v }))} defaultValue={FUSION_DEFAULTS.confirmed_score_min} />
          <NumField label="High-conviction score min" value={draft.fusion.high_conviction_score_min} min={0} max={100} onChange={(v) => patch(set(draft, 'fusion', { high_conviction_score_min: v }))} defaultValue={FUSION_DEFAULTS.high_conviction_score_min} />
          <BoolField label="Require fresh trigger" value={draft.fusion.require_fresh_trigger} onChange={(v) => patch(set(draft, 'fusion', { require_fresh_trigger: v }))} defaultValue={FUSION_DEFAULTS.require_fresh_trigger} />
          <BoolField label="Require all gate components" hint="Expected unavailable evidence fails closed." value={draft.fusion.require_all_gate_components} onChange={(v) => patch(set(draft, 'fusion', { require_all_gate_components: v }))} defaultValue={FUSION_DEFAULTS.require_all_gate_components} />
        </Section>

        <Section title="Data retention" description="Storage windows for raw samples and features." summary={`${draft.retention_raw_days}d raw · ${draft.retention_features_days}d features`}>
          <NumField label="Raw snapshot retention (days)" value={draft.retention_raw_days} min={1} max={365} onChange={(v) => patch({ ...draft, retention_raw_days: v })} defaultValue={ROOT_DEFAULTS.retention_raw_days} />
          <NumField label="Feature/signal retention (days)" value={draft.retention_features_days} min={1} max={3650} onChange={(v) => patch({ ...draft, retention_features_days: v })} defaultValue={ROOT_DEFAULTS.retention_features_days} />
        </Section>
      </AdvancedSection>

      </PanelCard>

      <SettingsDraftBar
        dirty={dirty}
        saving={setConfig.isPending}
        onApply={handleApply}
        onDiscard={handleReload}
        onReset={handleReset}
        resetConfirm={resetConfirm}
        applyDisabled={customScopeEmpty}
        applyTitle={customScopeEmpty ? 'Pick at least one index or stock for Navigator to scan' : undefined}
      />
    </>
  );
}


const pillButtonStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${BORDER}`, background: 'var(--k-bg)',
  color: MUTED, borderRadius: 7, padding: '7px 12px', fontSize: 11, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit',
};

const applyButtonStyle: React.CSSProperties = {
  border: 'none', background: ORANGE, color: 'var(--k-bg)', borderRadius: 7, padding: '8px 16px',
  fontSize: 11.5, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit',
};

export default NavigatorSettingsPanel;
