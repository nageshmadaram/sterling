import React, { useEffect, useMemo, useReducer, useState } from 'react';
import { useEffectiveNowMs } from '../../hooks/useReplayStore';
import { SterlingKiteEngineWithExpiry } from './SterlingKiteEngineWithExpiry';
import { rowsFromSnapshot } from './AdaptiveEdgePanel';
import { AdaptiveEdgeBoard } from './board/AdaptiveEdgeBoard';
import { GammaMoveBoard } from './board/GammaMoveBoard';
import { IntradayBoard } from './board/IntradayBoard';
import { EngineTabs, type EngineTabState } from './board/EngineToolbar';
import { adaptiveEdgeToBoard } from './board/adaptiveEdgeAdapter';
import { gammaMoveToBoard } from './board/gammaMoveAdapter';
import { supertrendToBoard } from './board/supertrendAdapter';
import { ACTIONABLE, type BoardSignal, type EngineId } from './board/boardTypes';
import { useEngineEnabled } from '../../hooks/useEngineToggles';
import { useAdaptiveEdgeSnapshot } from '../../hooks/useAdaptiveEdge';
import { useEngineSignals, useEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useIntradaySnapshot } from '../../hooks/useIntraday';
import { intradayToBoard } from './board/intradayAdapter';
import { useGammaMoveSnapshot } from '../../hooks/useGammaMove';
import { useNavigatorConfig } from '../../hooks/useNavigator';
import { k, Icons } from '../../styles/kiteUI';
import { useKiteSettings } from '../../store/useKiteSettings';
import { PaneHeaderActions } from './PaneHeaderActions';
import { ToolbarButton } from './board/EngineToolbar';
import { ScanProgressRing } from './board/ScanProgressRing';
import { SignalTableSettingsPanel } from './SterlingKiteEnginePane';
import { SCANNABLE_ENGINE_LABEL, useScanAllStrategies, type ScannableEngine } from '../../hooks/useScanAllStrategies';
import { useCancelScan } from '../../hooks/useSterlingKiteEngine';
import { useCancelNavigatorScan } from '../../hooks/useNavigator';

/**
 * The engine workspace: pick an engine, see its board.
 *
 * The picker used to be three flat words. Choosing between them meant opening
 * each one to find out whether it was running and whether it had anything —
 * which is backwards, because the point of a picker is to make that choice
 * without paying for it.
 *
 * Each tab now carries live state: a dot for running / running-but-quiet / off,
 * and a count of what is live. Every tab therefore has to know its engine's
 * state whether or not it is the visible one, which is why the counts are read
 * here rather than inside each board. All three already poll on their own
 * schedule, so this shares their cached data rather than adding requests.
 */
interface Props {
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  onOpenChart?: (symbol: string, tab: 'chart', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: any) => void;
  /** Opens a board signal as a full detail page in the centre column. */
  onOpenBoardDetail?: (signal: BoardSignal) => void;
}

/** Which engine each nav destination should land on. */
const NAV_TARGET: Record<string, EngineId> = {
  adaptiveEdge: 'adaptive_edge',
  gammaMove: 'gamma_move',
  intraday: 'intraday',
};

export function AdaptiveEdgeRightSidebar({ onSelectSignal, onOpenChart, onOpenBoardDetail }: Props) {
  const [engine, setEngine] = useState<EngineId>('supertrend');
  const engineOn = useEngineEnabled();
  const openChartFor = React.useCallback(
    (quoteKey: string) => onOpenChart?.(quoteKey, 'chart'),
    [onOpenChart],
  );

  const [settingsOpen, setSettingsOpen] = useState(false);
  const rescanStrategies = useKiteSettings((st) => st.rescanStrategies);
  const { scanAll, isPending: scanPending } = useScanAllStrategies();
  const cancelScan = useCancelScan();
  const cancelNavigatorScan = useCancelNavigatorScan();
  const [, tickRing] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    const id = setInterval(tickRing, 1000);
    return () => clearInterval(id);
  }, []);
  const nowMs = useEffectiveNowMs();

  const snapshot = useAdaptiveEdgeSnapshot();
  const engineSignals = useEngineSignals();
  const engineConfig = useEngineConfig();
  const navigatorEnabled = useNavigatorConfig().data?.record.config.enabled ?? false;
  const gmSnapshot = useGammaMoveSnapshot();
  const idSnapshot = useIntradaySnapshot();

  /**
   * The countdown to the next automatic scan, 0..1.
   *
   * This is the only percentage in the vicinity that is real. A scan in FLIGHT is
   * indeterminate — the engine says it is scanning and which instrument it is on,
   * not how far through a known total — so the ring shows motion for that and a
   * number only for this.
   *
   * Market closed means the loop is paused and `next_scan_ms` is stale, so the
   * ring would sit convincingly at 100% forever. Zero instead.
   */
  const sig = engineSignals.data;
  const scanning = sig?.scanning ?? false;
  const countdown = (() => {
    const gen = sig?.generated_ms ?? 0;
    const next = sig?.next_scan_ms ?? 0;
    const interval = next - gen;
    if (!sig?.auto_scan || interval <= 0 || sig?.market_open === false) return 0;
    return Math.min(1, Math.max(0, (Date.now() - gen) / interval));
  })();

  /**
   * Rescan order: the engine you are looking at first.
   *
   * They share one historical-data budget, so they run one at a time — which
   * makes the order the difference between the board in front of you refreshing
   * now or in twenty seconds.
   */
  const scanOrder = useMemo<ScannableEngine[]>(() => {
    const all: ScannableEngine[] = ['supertrend', 'navigator', 'gamma_move', 'adaptive_edge', 'intraday'];
    const first = all.filter((e) => e === engine);
    return [...first, ...all.filter((e) => e !== engine)];
  }, [engine]);

  /**
   * The engines a press will ACTUALLY scan.
   *
   * Switched-off engines are skipped. Scanning one would be work the operator has
   * explicitly declined, and it would make the button's own tooltip a lie — it
   * names what it will run, including "Navigator is off".
   *
   * Derived once and used by both the title and the press, because those two
   * disagreeing is exactly the bug this replaces: the old button said "Re-scan
   * both engines" whether it scanned one or two.
   */
  const enabledToScan = useMemo<ScannableEngine[]>(() => scanOrder.filter((e) => {
    // The operator's own choice of what this button covers. Absent means
    // included, so the map only holds exclusions and a new engine is in from the
    // day it appears.
    if (rescanStrategies[e] === false) return false;
    // ANDed with whether the engine is RUNNING, never ORed: a switched-off engine
    // is skipped whatever is ticked in settings, because scanning it would be
    // work already declined.
    if (e === 'supertrend') return engineConfig.data?.engine_enabled !== false;
    if (e === 'navigator') return navigatorEnabled;
    // Same contract as the three above: a switched-off engine is skipped
    // whatever is ticked in Trading Mode. Absent config (snapshot still
    // loading) means included, so a first press is not a no-op.
    if (e === 'gamma_move') return gmSnapshot.data?.config?.enabled !== false;
    if (e === 'intraday') return idSnapshot.data?.config?.enabled !== false;
    return true;
  }), [scanOrder, rescanStrategies, engineConfig.data?.engine_enabled, navigatorEnabled,
       gmSnapshot.data?.config?.enabled, idSnapshot.data?.config?.enabled]);

  /**
   * Name what the press will actually run, in the order it will run it.
   *
   * A button that scans five engines one at a time, skipping the ones that are
   * switched off, must say so — otherwise a press that scanned three looks
   * identical to a press that scanned five. This moved up from SuperTrend's pane
   * with the button; the ORDER it names changed from lens-first to
   * ACTIVE-TAB-first, because the button now belongs to the whole dock and the
   * board in front of you is the one you want refreshed first.
   *
   * ATM Premium Imbalance stays unlisted: it has no scan, it arms one resolved
   * pair. Naming it would promise something the platform cannot do.
   */
  const scanTitle = (() => {
    if (scanning) return `Scanning ${sig?.scanning_label || '…'}`;
    const names = enabledToScan.map((e) => SCANNABLE_ENGINE_LABEL[e]);
    const off = !navigatorEnabled ? ' · Navigator is off' : '';
    return names.length
      ? `Re-scan ${names.join(', ')}${off}`
      : `Every strategy is switched off${off}`;
  })();

  const tabs: EngineTabState[] = useMemo(() => {
    const st = supertrendToBoard(engineSignals.data?.rows ?? []);
    const ae = snapshot.data ? adaptiveEdgeToBoard(rowsFromSnapshot(snapshot.data)) : [];
    const gm = gammaMoveToBoard(gmSnapshot.data);
    const id = intradayToBoard(idSnapshot.data);
    const live = (list: typeof st) => list.filter((s) => ACTIONABLE.includes(s.status)).length;
    const all: EngineTabState[] = [
      { id: 'supertrend', running: engineConfig.data?.engine_enabled !== false, live: live(st), scanned: st.length },
      { id: 'adaptive_edge', running: !!snapshot.data, live: live(ae), scanned: ae.length },
      { id: 'gamma_move',
        running: gmSnapshot.data?.config?.enabled === true,
        live: live(gm), scanned: gm.length },
      { id: 'intraday',
        running: idSnapshot.data?.config?.enabled === true,
        live: live(id), scanned: id.length },
    ];
    return all.filter((tab) => {
      if (tab.id === 'supertrend') return engineOn.supertrend || engineOn.navigator;
      return engineOn[tab.id as keyof typeof engineOn] !== false;
    });
  }, [engineSignals.data, engineConfig.data, snapshot.data, gmSnapshot.data, idSnapshot.data, engineOn]);

  useEffect(() => {
    if (!tabs.length) return;
    if (!tabs.some((tab) => tab.id === engine)) setEngine(tabs[0].id);
  }, [tabs, engine]);

  useEffect(() => {
    const onNav = (event: Event) => {
      const target = NAV_TARGET[(event as CustomEvent<string>).detail];
      if (target) setEngine(target);
    };
    window.addEventListener('kite-nav-click', onNav);
    return () => window.removeEventListener('kite-nav-click', onNav);
  }, []);

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', background: k.bg }}>
      <div style={{ display: 'flex', flexShrink: 0, borderBottom: `1px solid ${k.border}`, background: k.bg }}>
        <EngineTabs tabs={tabs} active={engine} onSelect={setEngine} />

        <PaneHeaderActions pane="signals">
          {scanning ? (
            <ToolbarButton
              title="Stop scan"
              onClick={() => {
                if (engineConfig.data?.engine_enabled !== false) cancelScan.mutate();
                if (navigatorEnabled) cancelNavigatorScan.mutate();
              }}
              disabled={cancelScan.isPending || cancelNavigatorScan.isPending}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
            </ToolbarButton>
          ) : (
            <ToolbarButton title={scanTitle} disabled={scanPending} onClick={() => { void scanAll(enabledToScan); }}>
              <ScanProgressRing fraction={countdown} scanning={scanPending} />
            </ToolbarButton>
          )}
          <span data-signal-table-settings style={{ display: 'inline-flex' }}>
            <ToolbarButton title="Board settings" active={settingsOpen} onClick={() => setSettingsOpen((v) => !v)}>
              <Icons.Settings />
            </ToolbarButton>
          </span>
        </PaneHeaderActions>
      </div>

      {settingsOpen && (
        <div style={{ flexShrink: 0, overflow: 'hidden' }}>
          <SignalTableSettingsPanel />
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {engine === 'supertrend' && (
          <SterlingKiteEngineWithExpiry onSelectSignal={onSelectSignal} onOpenChart={onOpenChart} />
        )}
        {engine === 'adaptive_edge' && <AdaptiveEdgeBoard onOpenChart={openChartFor} nowMs={nowMs} onOpenDetail={onOpenBoardDetail} />}
        {engine === 'gamma_move' && (
          <GammaMoveBoard onOpenChart={openChartFor} nowMs={nowMs} onOpenDetail={onOpenBoardDetail} />
        )}
        {engine === 'intraday' && (
          <IntradayBoard onOpenChart={openChartFor} nowMs={nowMs} onOpenDetail={onOpenBoardDetail} />
        )}
      </div>
    </div>
  );
}
