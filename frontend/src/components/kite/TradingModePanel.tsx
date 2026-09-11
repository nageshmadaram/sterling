import React from 'react';
import { useKiteSettings } from '../../store/useKiteSettings';
import { useEngineConfig, usePatchEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useNavigatorConfig } from '../../hooks/useNavigator';
import { BORDER, DIM, MUTED, SOFT, Switch, TEXT } from './kiteSettingsPrimitives';
import { PanelCard, PanelSectionHeading } from './config/ConfigPrimitives';
import { TradingModeControls } from './TradingModeControls';
import { useEngineToggles } from '../../hooks/useEngineToggles';
import { useAlgoToggles } from '../../hooks/useAlgoToggles';
import type { EngineConfigModel, DeepItmMoneyness } from '../../types/kiteEngine';

type ProfileId = 'atm' | 'otm' | 'slight_itm' | 'deep_itm' | 'futures';

function deriveActiveProfile(cfg?: EngineConfigModel | null): ProfileId {
  if (!cfg) return 'atm';
  if (cfg.vehicle === 'futures' && cfg.directional_mode) return 'futures';
  const d = cfg.target_delta;
  if (cfg.vehicle === 'deep_itm_options' && cfg.directional_mode) {
    return (d ?? 0.85) >= 0.78 ? 'deep_itm' : 'slight_itm';
  }
  if (d != null && d < 0.45) return 'otm';
  return 'atm';
}

const PROFILES: Array<{
  id: ProfileId;
  label: string;
  delta: string;
  badge: string;
  desc: string;
}> = [
  { id: 'atm', label: 'ATM', delta: 'δ ≈ 0.50', badge: 'Maximum Liquidity', desc: 'At the money — tightest bid-ask spreads, ~50% delta capture, standard option risk.' },
  { id: 'otm', label: 'OTM', delta: 'δ ≈ 0.28', badge: 'High Leverage', desc: 'Out of the money — lowest entry premium per lot, higher leverage, faster theta bleed.' },
  { id: 'slight_itm', label: 'Slight ITM', delta: 'δ ≈ 0.65', badge: 'Reduced Theta', desc: 'In the money — slower theta decay, higher delta capture on breakout moves.' },
  { id: 'deep_itm', label: 'Deep ITM', delta: 'δ ≈ 0.85', badge: 'Near Futures', desc: 'Deep in the money — moves point-for-point with underlying with defined downside.' },
  { id: 'futures', label: 'Futures', delta: 'δ = 1.00', badge: 'Index Futures', desc: 'Direct futures contract — 1.0 delta, no theta decay, requires F&O span margin.' },
];

const ITM_DEPTH_CHOICES: Array<{ id: DeepItmMoneyness; label: string; delta: string }> = [
  { id: 'ITM5', label: 'ITM-5', delta: 'δ ≈ 0.75' },
  { id: 'ITM10', label: 'ITM-10', delta: 'δ ≈ 0.85 (Standard)' },
  { id: 'ITM15', label: 'ITM-15', delta: 'δ ≈ 0.92' },
  { id: 'ITM20', label: 'ITM-20', delta: 'δ ≈ 0.96 (Near Futures)' },
];

const SPREAD_OPTIONS: Array<{ val: number | null; label: string; hint: string }> = [
  { val: 1.5, label: '1.5%', hint: 'Strict — best for liquid index options' },
  { val: 2.0, label: '2.0%', hint: 'Standard — protects against illiquid stock strikes' },
  { val: 3.0, label: '3.0%', hint: 'Relaxed — allows moderately wider spreads' },
  { val: 5.0, label: '5.0%', hint: 'Wide — only blocks severe spread blowouts' },
  { val: null, label: 'Off', hint: 'No spread check — accepts any market spread' },
];

const OI_FLOORS: Array<{ val: number | null; label: string }> = [
  { val: 500, label: '500 OI' },
  { val: 1000, label: '1,000 OI' },
  { val: 5000, label: '5,000 OI' },
  { val: null, label: 'Off' },
];

/**
 * The two questions that decide whether real money moves — paper or live, and
 * who places the order — plus which engines are actually running.
 *
 * These used to sit inside the page titled "SuperTrend Engine", so a user who
 * had turned SuperTrend off had no reason to look there for the switch that
 * also arms Navigator. `auto_execute` is user-global (keyed by uid alone) and
 * Navigator's automatic placement reuses the same path, so it belongs on its
 * own page above both engines.
 *
 * It is also the first place both arming switches appear together.
 * `auto_execute` and Navigator's `auto_execute_originated` are independent
 * switches that both place real orders, and neither surface previously
 * mentioned the other.
 */

export function RunningRow({ label, description, on, children }: {
  label: string;
  description: string;
  on: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      padding: '13px 0', borderTop: `1px solid ${BORDER}`,
    }}>
      <span aria-hidden style={{
        width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
        background: on ? 'var(--k-green)' : '#c7c7c7',
      }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ color: TEXT, fontSize: 12.5, fontWeight: 700 }}>{label}</div>
        <div style={{ color: MUTED, fontSize: 11, lineHeight: 1.5, marginTop: 2 }}>{description}</div>
      </div>
      {children}
    </div>
  );
}

const RESCANNABLE: Array<{ engine: string; label: string; note: string }> = [
  { engine: 'supertrend', label: 'SuperTrend', note: 'Triple SuperTrend across the configured universe' },
  { engine: 'navigator', label: 'Value-Flow Navigator', note: 'AVWAP and flow evidence, its own source' },
  { engine: 'gamma_move', label: 'Gamma Move', note: 'Open-interest unwind around the levels' },
  { engine: 'adaptive_edge', label: 'Adaptive Edge', note: 'Order-flow scalping' },
];

export function TradingModePanel() {
  const { data: cfg } = useEngineConfig();
  const patchCfg = usePatchEngineConfig();
  const { data: navData } = useNavigatorConfig();

  const navCfg = navData?.record.config;
  const navigatorAuto = !!navCfg?.auto_execute_originated;
  const toggles = useEngineToggles();
  const rescanStrategies = useKiteSettings((s) => s.rescanStrategies);
  const toggleRescanStrategy = useKiteSettings((s) => s.toggleRescanStrategy);
  const autoOn = !!cfg?.auto_execute;

  const activeProfile = deriveActiveProfile(cfg);

  const onSelectProfile = (id: ProfileId) => {
    if (!cfg) return;
    let patch: Partial<EngineConfigModel> = {};
    if (id === 'atm') {
      patch = { vehicle: 'otm_options', directional_mode: false, target_delta: 0.50 };
    } else if (id === 'otm') {
      patch = { vehicle: 'otm_options', directional_mode: false, target_delta: 0.28 };
    } else if (id === 'slight_itm') {
      patch = { vehicle: 'deep_itm_options', directional_mode: true, target_delta: 0.65 };
    } else if (id === 'deep_itm') {
      patch = { vehicle: 'deep_itm_options', directional_mode: true, target_delta: 0.85, itm_depth: cfg.itm_depth || 'ITM10' };
    } else if (id === 'futures') {
      patch = { vehicle: 'futures', directional_mode: true, target_delta: null };
    }
    const targetVeh = patch.vehicle!;
    const currentVehs = cfg.enabled_vehicles || ['otm_options'];
    if (!currentVehs.includes(targetVeh)) {
      patch.enabled_vehicles = [...currentVehs, targetVeh];
    }
    patchCfg.mutate(patch);
  };

  return (
    <>
      <TradingModeControls />

      {/* Options & Strike Execution Config */}
      <PanelCard>
        <PanelSectionHeading
          title="Options & Strike Execution Config"
          description="Select contract moneyness, lot size, and real-world bid-ask spread and slippage safeguards for auto and manual orders."
        />
        <div style={{ padding: '0 18px 18px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Strike profile selector */}
          <div>
            <div style={{ fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 8, textTransform: 'uppercase' }}>
              Strike Moneyness & Delta Profile
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
              {PROFILES.map((p) => {
                const isSelected = activeProfile === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => onSelectProfile(p.id)}
                    disabled={patchCfg.isPending}
                    style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                      padding: '10px 12px', borderRadius: 8, textAlign: 'left', cursor: 'pointer',
                      background: isSelected ? 'rgba(240, 100, 40, 0.08)' : 'var(--k-surface-2)',
                      border: isSelected ? '1.5px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', marginBottom: 3 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 750, color: isSelected ? 'var(--k-brand)' : TEXT }}>
                        {p.label}
                      </span>
                      <span style={{ fontSize: 10, fontWeight: 650, color: isSelected ? 'var(--k-brand)' : MUTED }}>
                        {p.delta}
                      </span>
                    </div>
                    <div style={{ fontSize: 9.5, fontWeight: 650, color: isSelected ? 'var(--k-brand)' : DIM, marginBottom: 4 }}>
                      {p.badge}
                    </div>
                    <div style={{ fontSize: 10, color: MUTED, lineHeight: 1.35 }}>
                      {p.desc}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Deep ITM Depth (if active) */}
          {(activeProfile === 'deep_itm' || activeProfile === 'slight_itm') && (
            <div style={{ padding: '10px 12px', background: 'var(--k-surface-sunken-2)', borderRadius: 7, border: `1px solid ${BORDER}` }}>
              <div style={{ fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 6, textTransform: 'uppercase' }}>
                ITM Strike Depth
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {ITM_DEPTH_CHOICES.map((choice) => {
                  const isSel = (cfg?.itm_depth || 'ITM10') === choice.id;
                  return (
                    <button
                      key={choice.id}
                      type="button"
                      onClick={() => patchCfg.mutate({ itm_depth: choice.id })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-bg)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {choice.label} ({choice.delta})
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Execution Lot Sizing */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', paddingTop: 8, borderTop: `1px solid ${BORDER}` }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Lots per Execution</div>
              <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>Number of option/futures lots submitted on automated or manual orders.</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {[1, 2, 5, 10, 25].map((l) => {
                const isSel = (cfg?.max_lots ?? 1) === l;
                return (
                  <button
                    key={l}
                    type="button"
                    onClick={() => patchCfg.mutate({ max_lots: l })}
                    disabled={patchCfg.isPending}
                    style={{
                      padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                      background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                      color: isSel ? 'var(--k-on-accent)' : TEXT,
                      border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                    }}
                  >
                    {l}L
                  </button>
                );
              })}
            </div>
          </div>

          {/* Real-world Slippage & Bid-Ask Spread Protection */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 10, borderTop: `1px solid ${BORDER}` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Max Bid-Ask Spread Filter</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Skips order if the contract spread exceeds this % of mid price, eliminating fatal market slippage.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {SPREAD_OPTIONS.map((opt) => {
                  const isSel = (cfg?.max_spread_pct ?? 2.0) === opt.val;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      title={opt.hint}
                      onClick={() => patchCfg.mutate({ max_spread_pct: opt.val })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Minimum Open Interest (OI)</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Enforces institutional participation and market-maker presence before entering a strike.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5 }}>
                {OI_FLOORS.map((opt) => {
                  const isSel = (cfg?.min_oi ?? null) === opt.val;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => patchCfg.mutate({ min_oi: opt.val })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Protective Stop Placement</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Where the protective exit is managed — exchange trigger order or engine tick monitor.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5 }}>
                {[
                  { id: 'both', label: 'Broker + Monitor' },
                  { id: 'broker', label: 'Broker SL-M' },
                  { id: 'monitor', label: 'Software Trail' },
                ].map((m) => {
                  const isSel = (cfg?.stop_mode || 'both') === m.id;
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => patchCfg.mutate({ stop_mode: m.id as any })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </PanelCard>

      <PanelCard>
        <PanelSectionHeading
          title="What is running"
          description="Which signal engines are active. Turning both off leaves Kite as a normal manual trading platform — market watch, charts and your own orders all still work."
        />
        <div style={{ padding: '2px 18px 16px' }}>
          {toggles.map((engine) => (
            <RunningRow
              key={engine.id}
              label={engine.label}
              description={engine.description}
              on={engine.enabled}
            >
              <Switch
                checked={engine.enabled}
                label={engine.label}
                disabled={!engine.toggle || engine.pending}
                onChange={() => engine.toggle?.()}
              />
            </RunningRow>
          ))}
        </div>

        {autoOn && navigatorAuto && (
          <div style={{
            padding: '11px 18px', background: '#fff7f0', borderTop: `1px solid #edd6c6`,
            color: '#9a4b16', fontSize: 11.5, lineHeight: 1.5,
          }}>
            ⚠ <strong>Two automatic order paths are armed.</strong> SuperTrend signals place through
            AUTO above, and Navigator places its own originated setups through its separate switch.
            Both reach the same broker account.
          </div>
        )}

        <div style={{
          padding: '12px 18px', background: SOFT, borderTop: `1px solid ${BORDER}`,
          color: DIM, fontSize: 10.5, lineHeight: 1.5,
        }}>
          Paper/live is set per account. Automatic execution is set once for your user and applies to
          whichever account is active.
        </div>
      </PanelCard>

      <PanelCard>
        <PanelSectionHeading
          title="Included in re-scan"
          description="Which strategies the re-scan button covers. They share one historical-data budget and run one at a time, so a scan of five costs five times a scan of one — an operator working a single strategy can stop paying for the other four without switching them off for everyone."
        />
        <div style={{ padding: '2px 18px 16px', display: 'grid', gap: 2 }}>
          {RESCANNABLE.map(({ engine, label, note }) => {
            const isEngineRunning = toggles.find((t) => t.id === engine)?.enabled ?? true;
            const checked = isEngineRunning && rescanStrategies[engine] !== false;
            return (
              <label
                key={engine}
                title={isEngineRunning ? note : `${label} is turned off above — turn on engine to enable re-scan.`}
                style={{
                  minHeight: 30, display: 'flex', alignItems: 'center', gap: 8,
                  color: isEngineRunning ? 'var(--k-text)' : 'var(--k-dim)',
                  fontSize: 11, cursor: isEngineRunning ? 'pointer' : 'not-allowed',
                  opacity: isEngineRunning ? 1 : 0.5,
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!isEngineRunning}
                  onChange={() => isEngineRunning && toggleRescanStrategy(engine)}
                  style={{ width: 14, height: 14, margin: 0, accentColor: 'var(--k-orange)', cursor: isEngineRunning ? 'pointer' : 'not-allowed' }}
                />
                <span style={{ fontWeight: 600 }}>{label}</span>
                <span style={{ color: 'var(--k-dim)', fontSize: 10 }}>
                  {isEngineRunning ? note : '(Disabled — strategy engine is turned off)'}
                </span>
              </label>
            );
          })}
          <div style={{ marginTop: 8, fontSize: 10, lineHeight: 1.5, color: 'var(--k-dim)' }}>
            A strategy switched off above is skipped whatever is ticked here — the
            running switch wins, because scanning a stopped engine is work you have
            already declined.
          </div>
        </div>
      </PanelCard>
    </>
  );
}

export default TradingModePanel;
