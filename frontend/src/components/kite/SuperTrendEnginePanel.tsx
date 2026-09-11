import React from 'react';
import {
  useEngineConfig, useResetEngineConfig, useRunScan, useSetEngineConfig,
} from '../../hooks/useSterlingKiteEngine';
import {
  BORDER, ChoiceRow, DIM, Field, MUTED, ORANGE, Section, SOFT, Switch, TEXT,
} from './kiteSettingsPrimitives';
import { ConfigNote, PanelCard, SettingsDraftBar } from './config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';
import { EnginePowerHeader } from './config/EnginePowerHeader';
import { ContractsGroup, ExpirySettingsGroup, InstrumentsGroup, SignalSourceGroup } from './config/ScanSettings';
import { OptionContractsPicker } from './config/OptionContractsPicker';
import {
  EXIT_MODE_OPTIONS, FIELDS, TRAIL_OPTIONS, exitModeLabel, scanSourceLabel,
} from './config/registry';
import type { EngineConfigModel } from '../../types/kiteEngine';
import { notifyOrder } from '../../store/useKiteNotifications';

/**
 * SuperTrend engine settings — shared order with Navigator:
 * draft bar → power → chart → instruments → contracts → expiry → engine-specific.
 */
export function SuperTrendEnginePanel() {
  const { data: serverCfg, isLoading } = useEngineConfig();
  const setCfg = useSetEngineConfig();
  const resetCfg = useResetEngineConfig();
  const runScan = useRunScan();

  const [draft, setDraft] = React.useState<EngineConfigModel | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  // Leaving this section unmounts the panel and the draft with it.
  useUnsavedDraftGuard('supertrend', dirty);

  React.useEffect(() => {
    if (!serverCfg) return;
    if (!dirty) setDraft(serverCfg);
  }, [serverCfg, dirty]);

  if (isLoading || !draft) {
    return <div style={{ padding: 18, color: DIM, fontSize: 12 }}>Loading SuperTrend settings…</div>;
  }

  const cfg = draft;

  const patch = (values: Partial<EngineConfigModel>) => {
    setDraft((prev) => (prev ? { ...prev, ...values } : prev));
    setDirty(true);
    setResetConfirm(false);
  };

  const handleApply = () => {
    if (!draft) return;
    setCfg.mutate(draft, {
      onSuccess: () => {
        setDirty(false);
        notifyOrder({ kind: 'info', title: 'Settings updated', message: 'SuperTrend settings applied.' });
        runScan.mutate();
      },
      // Without this a failed save was completely silent: the draft stayed dirty
      // and nothing anywhere said the write had not landed, so the user reads the
      // still-showing "Unsaved changes" as their own unfinished edit rather than a
      // rejected one — and walks away believing the engine took the new settings.
      onError: (err) => {
        notifyOrder({
          kind: 'error',
          title: 'Settings NOT saved',
          message: `SuperTrend settings were not applied: ${String(
            (err as Error)?.message ?? 'the save was rejected')}. Your changes are still here — try Apply again.`,
        });
      },
    });
  };

  const handleDiscard = () => {
    if (serverCfg) setDraft(serverCfg);
    setDirty(false);
    setResetConfirm(false);
  };

  const handleReset = () => {
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }
    resetCfg.mutate(undefined, {
      onSuccess: () => {
        setDirty(false);
        setResetConfirm(false);
        runScan.mutate();
      },
    });
  };

  const on = cfg.engine_enabled;
  const trailLabel = TRAIL_OPTIONS.find((o) => o.value === cfg.trail_target)?.label ?? cfg.trail_target;
  const indexExpiries = cfg.scan_expiries_indices ?? cfg.scan_expiries;
  // The derivatives pass is the only one that charts a contract's own premium,
  // so this knob is meaningless unless that pass actually runs.
  const derivSourceActive = cfg.scan_source === 'derivatives' || cfg.scan_source === 'both';
  const derivMonthlyOnly = (cfg.deriv_expiries ?? ['monthly']).join() === 'monthly';
  const instrumentsSummary = !(cfg.scan_stock_contracts ?? true)
    ? `${cfg.scan_indices.length} indices · no stocks`
    : cfg.scan_all_stocks
      ? `All F&O · ${cfg.scan_indices.length} indices`
      : `${cfg.scan_stocks.length} stocks · ${cfg.scan_indices.length} indices`;

  const saving = setCfg.isPending;

  return (
    <>
      <EnginePowerHeader
        name="SuperTrend"
        tagline="Triple SuperTrend on a 1H Heikin-Ashi chart."
        on={on}
        liveOn={serverCfg?.engine_enabled ?? on}
        busy={saving}
        onToggle={() => patch({ engine_enabled: !on })}
        runningNote="Scanning, producing signals, and eligible for automatic execution."
        offNote="Not scanning. Navigator can still run on its own."
      />

      <PanelCard>
        <Section
          title="Chart source"
          description="Which price series SuperTrend reads a setup from."
          summary={scanSourceLabel(cfg.scan_source)}
          defaultOpen
          persistKey="st-chart">
          <SignalSourceGroup
            name="supertrend-signal-source"
            value={cfg.scan_source}
            onChange={(v) => patch({ scan_source: v })}
          />
          {derivSourceActive && (
            <Field
              label={FIELDS.deriv_expiries.label}
              hint={FIELDS.deriv_expiries.help}
            >
              <ChoiceRow
                value={derivMonthlyOnly ? 'monthly' : 'follow'}
                options={[
                  { value: 'monthly', label: 'Monthly only', hint: 'Recommended. Every contract has room to warm up.' },
                  { value: 'follow', label: 'Follow index expiries', hint: 'Also scans weeklies — mute for most of their life at 1H.' },
                ]}
                onChange={(v) => patch({ deriv_expiries: v === 'monthly' ? ['monthly'] : null })}
              />
              <ConfigNote>
                {derivMonthlyOnly ? (
                  <>
                    <strong>Monthly only.</strong> The derivatives source charts each
                    contract&apos;s <em>own premium</em>, and the three SuperTrends need 21
                    bars before any of them can read. A monthly contract spends 3.5 of its
                    22 sessions warming up, so <strong>84%</strong> of its life can signal.
                    Weekly contracts are skipped by this source; they are still traded
                    normally by the spot and confluence sources.
                  </>
                ) : (
                  <>
                    <strong>Weeklies included — expect far fewer signals per contract.</strong>{' '}
                    A weekly has 5 sessions and 3.5 of them go to the 21-bar warmup, so it
                    can only signal for the last <strong>1.5 sessions (30% of its life)</strong>
                    {' '}— the stretch where time decay is worst. Nothing breaks and no
                    signal is faked: the contract is simply silent until it has enough
                    history. Worth choosing only if you move this engine to a timeframe
                    faster than 1H, where a weekly warms up in well under a day.
                  </>
                )}
              </ConfigNote>
            </Field>
          )}
        </Section>

        <Section
          title="Instruments"
          description="The indices and F&O stocks this engine watches."
          summary={instrumentsSummary}
          defaultOpen
          persistKey="st-instruments">
          <InstrumentsGroup
            idPrefix="SuperTrend"
            indices={cfg.scan_indices}
            stocks={cfg.scan_stocks}
            allStocks={cfg.scan_all_stocks}
            stockContracts={cfg.scan_stock_contracts ?? true}
            onChange={(next) => patch(next)}
          />
        </Section>

        <Section
          title="Contracts"
          description="Which strikes and expiry cycles SuperTrend resolves."
          summary={`${cfg.strike_moneyness.length} strikes · ${indexExpiries.join(' + ')}`}
          defaultOpen
          persistKey="st-contracts">
          <ContractsGroup
            strikes={cfg.strike_moneyness}
            indexExpiries={indexExpiries}
            onChange={(next) => patch(next)}
          />
          <OptionContractsPicker
            config={cfg}
            onSave={(patchData) => patch(patchData)}
            saving={saving}
          />
        </Section>

        <Section
          title="Expiry"
          description="Rules governing contract days-to-expiry and settlement dates."
          summary={`${cfg.expiry_dte_min ?? 0}–${cfg.expiry_dte_max ?? 400} DTE${cfg.avoid_expiry_day ? ' · avoid expiry day' : ''}`}
          defaultOpen
          persistKey="st-expiry">
          <ExpirySettingsGroup
            dteMin={cfg.expiry_dte_min ?? 0}
            dteMax={cfg.expiry_dte_max ?? 400}
            avoidExpiryDay={cfg.avoid_expiry_day ?? false}
            dteDefaults={{ min: 0, max: 400 }}
            dteNote={
              <>
                Separate from the expiry square-off below: this decides which
                contracts may be <em>entered</em>, that one closes a position
                already held as its contract runs out.
              </>
            }
            onChange={(next) => patch(next)}
          />
        </Section>

        <Section
          title="Direction"
          description="Which side of a SuperTrend alignment may open a trade."
          summary={(cfg.allow_short ?? false) ? 'Long and short' : 'Long only'}
          persistKey="st-direction">
          <Field label={FIELDS.allow_short.label} hint={FIELDS.allow_short.help}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <Switch
                checked={cfg.allow_short ?? false}
                label="Take short entries on a bear alignment"
                onChange={() => patch({ allow_short: !(cfg.allow_short ?? false) })}
              />
              <span style={{ color: TEXT, fontSize: 11.5 }}>
                {(cfg.allow_short ?? false) ? 'Both directions' : 'Long only'}
              </span>
            </div>
          </Field>
          <ConfigNote>
            Measured on 7.5 years of real 1H candles across all four indices, with
            costs and slippage: the short book netted about zero over 1102 trades
            while the long book earned the entire return. Long-only takes slightly
            more money on half the trades, with a quarter less drawdown
            (profit factor 1.54 vs 1.25, worst drawdown 19.9% vs 26.0%). Closing a
            position is unaffected either way.
          </ConfigNote>
        </Section>

        <Section
          title="Trail tightness"
          description="Which line the stop follows once a trade is running."
          summary={`${trailLabel}${cfg.exit_aligned_trail ? ' · anchored to exit counter' : ''}`}
          defaultOpen
          persistKey="st-trail">
          <Field label={FIELDS.trail_target.label} hint={FIELDS.trail_target.help}>
            <ChoiceRow
              value={cfg.trail_target} options={TRAIL_OPTIONS}
              onChange={(v) => patch({ trail_target: v })}
            />
          </Field>
          <Field label={FIELDS.exit_aligned_trail.label} hint={FIELDS.exit_aligned_trail.help}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <Switch
                checked={cfg.exit_aligned_trail ?? false} label="Anchor stop to exit counter"
                onChange={() => patch({ exit_aligned_trail: !(cfg.exit_aligned_trail ?? false) })}
              />
              <span style={{ color: TEXT, fontSize: 11.5 }}>
                {cfg.exit_aligned_trail ? 'Aligned to exit counter' : 'Tightest fast line'}
              </span>
            </div>
          </Field>
        </Section>

        <Section
          title="Exit rule"
          description="What closes a SuperTrend trade."
          summary={`${exitModeLabel(cfg.exit_mode)}${(cfg.price_stop_exit ?? true) ? ' · trail enforced' : ' · counter only'}`}
          defaultOpen
          persistKey="st-exit">
          <Field label={FIELDS.exit_mode.label} hint={FIELDS.exit_mode.help}>
            <ChoiceRow
              value={cfg.exit_mode} options={EXIT_MODE_OPTIONS}
              onChange={(v) => patch({ exit_mode: v })}
            />
          </Field>
          <Field label={FIELDS.price_stop_exit.label} hint={FIELDS.price_stop_exit.help}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <Switch
                checked={cfg.price_stop_exit ?? true} label="Enforce the trailing stop as a real exit"
                onChange={() => patch({ price_stop_exit: !(cfg.price_stop_exit ?? true) })}
              />
              <span style={{ color: TEXT, fontSize: 11.5 }}>
                {(cfg.price_stop_exit ?? true) ? 'Trail or exit counter, whichever fires first' : 'Exit counter only'}
              </span>
            </div>
          </Field>
          <ConfigNote>
            Entry is fixed: all three SuperTrend lines must be green and the signal fresh on the
            latest closed 1H bar. Filters that can refuse an automatic entry live under{' '}
            <b>Automatic rules</b>. A position already held by the server-side tick monitor has its
            trail enforced regardless of the board exit rule.
          </ConfigNote>
        </Section>
      </PanelCard>

      <SettingsDraftBar
        dirty={dirty}
        saving={saving}
        onApply={handleApply}
        onDiscard={handleDiscard}
        onReset={handleReset}
        resetConfirm={resetConfirm}
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

export default SuperTrendEnginePanel;
