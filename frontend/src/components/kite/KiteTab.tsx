import React, { useState, useEffect, useRef } from 'react';
import { KiteLayout, NavItem, MoreTab } from './KiteLayout';
import { DataLakePane } from './DataLakePane';
import { KiteDashboard } from './KiteDashboard';
import { SterlingWatchListWithHoldingsSync } from './SterlingWatchListWithHoldingsSync';
import { MarketDataPane } from './MarketDataPane';
import { ConnectPane } from './ConnectPane';
import { HelpPane } from './HelpPane';
import { MutualFundsPane } from './MutualFundsPane';
import { PortfolioPane } from './PortfolioPane';
import { PositionsPane } from './PositionsPane';
import { OrdersPane } from './OrdersPane';
import { FundsPane } from './FundsPane';
import { BidsPane } from './BidsPane';
import { AlertsPane } from './AlertsPane';
import { BacktestPane } from './BacktestPane';
import { InstrumentPane, InstrumentTab } from './InstrumentPane';
import { KiteNotifications } from './KiteNotifications';
import { PendingGttProtectionWatcher } from './PendingGttProtectionWatcher';
import { KiteSessionGuard } from './KiteSessionGuard';
import { KiteAuthOverlay } from './KiteLoader';
import { SetupChart } from './SetupChart';
import { SignalDetailPane } from './SignalDetailPane';
import { BoardDetailPane } from './board/BoardDetailPane';
import type { BoardSignal } from './board/boardTypes';
import { EngineTerminal } from './EngineTerminal';
import { KiteTicker } from './KiteTicker';
import { useKiteAutoSession, useKiteStatus } from '../../hooks/useKite';
import { OrderWindow } from './OrderWindow';
import { useOrderWindowStore } from '../../store/useOrderWindowStore';
import { BasketPane } from './BasketPane';
import { useKiteBasketStore } from '../../store/useKiteBasketStore';
import { useKiteSettings } from '../../store/useKiteSettings';
import {
  EngineStartupBoundary,
  KiteStartupCoordinator,
  TickerStartupBoundary,
  WatchlistStartupBoundary,
} from './KiteStartupSurfaces';
import { KiteInteractionMotion } from './KiteInteractionMotion';
import { k } from '../../styles/kiteUI';
import { type SignalChartData } from '../../types/kiteEngine';
import { hasUnsavedDraft } from './config/unsavedDraftGuard';
import { AdaptiveEdgeRightSidebar } from './AdaptiveEdgeRightSidebar';
import { AdaptiveEdgePane } from './AdaptiveEdgePane';
import { UnifiedBacktestPane } from '../backtest/UnifiedBacktestPane';
import { AstroPane } from './AstroPane';
import { PcrPane } from './PcrPane';
import { OpeningVolumeLeadersPane } from './OpeningVolumeLeadersPane';

const MORE_TABS: { id: MoreTab; label: string }[] = [
  { id: 'bids', label: 'Bids' },
  { id: 'funds', label: 'Funds' },
  { id: 'mf', label: 'Mutual Funds' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'backtest', label: 'Backtest' },
  { id: 'data', label: 'Data' },
];

function MorePane({ activeTab, onTabChange }: { activeTab: MoreTab; onTabChange: (t: MoreTab) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', borderBottom: `1px solid ${k.border}`, background: k.bg, flexShrink: 0 }}>
        {MORE_TABS.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => onTabChange(t.id)}
              style={{
                padding: '10px 16px', border: 'none', background: 'transparent', cursor: 'pointer',
                fontSize: 13, fontWeight: active ? 600 : 400,
                color: active ? 'var(--k-brand)' : 'var(--k-ink-4)',
                borderBottom: active ? '2px solid var(--k-brand)' : '2px solid transparent',
                marginBottom: -1, transition: 'color 0.15s',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {activeTab === 'bids' && <BidsPane />}
        {activeTab === 'funds' && <FundsPane />}
        {activeTab === 'mf' && <MutualFundsPane />}
        {activeTab === 'alerts' && <AlertsPane />}
        {activeTab === 'backtest' && <BacktestPane />}
        {activeTab === 'data' && <MarketDataPane />}
      </div>
    </div>
  );
}

export function KiteTab() {
  const preferredSection = useKiteSettings((s) => s.defaultSection);
  const userNavigatedRef = useRef(false);
  const [nav, setNav] = useState<NavItem>(() => useKiteSettings.getState().defaultSection || 'dashboard');
  const [moreTab, setMoreTab] = useState<MoreTab>('bids');
  const [instrumentView, setInstrumentView] = useState<{ symbol: string; tab: InstrumentTab; trailTarget?: 'fast' | 'mid' | 'slow'; signalData?: SignalChartData } | null>(null);
  const [setupView, setSetupView] = useState<{ token: number; underlying: string } | null>(null);
  const [detailView, setDetailView] = useState<{ token: number; underlying: string; timestamp_ms: number; source?: string } | null>(null);
  // Engines on the shared board carry their whole record in the signal, so
  // their detail page needs no second fetch and no per-engine component.
  const [boardDetail, setBoardDetail] = useState<BoardSignal | null>(null);
  const [savedTerminalMode, setSavedTerminalMode] = useState<'minimized' | 'normal' | 'partial' | 'full' | null>(null);
  const [basketOpen, setBasketOpen] = useState(false);
  const basketCount = useKiteBasketStore((s) => s.entries.length);
  const { data: kiteStatus, isLoading: kiteStatusLoading } = useKiteStatus();
  useKiteAutoSession();

  useEffect(() => {
    const cb = (e: Event) => handleNavClick((e as CustomEvent<NavItem>).detail);
    if (!userNavigatedRef.current && preferredSection && preferredSection !== nav) {
      setNav(preferredSection);
    }
  }, [preferredSection, nav]);

  useEffect(() => {
    const cb = (e: Event) => {
      userNavigatedRef.current = true;
      handleNavClick((e as CustomEvent<NavItem>).detail);
    };
    window.addEventListener('kite-nav-click', cb);
    return () => window.removeEventListener('kite-nav-click', cb);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { isOpen, options, closeOrderWindow } = useOrderWindowStore();

  const handleNavClick = (n: NavItem) => {
    userNavigatedRef.current = true;
    if (n !== nav && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('kite-scroll-to-draft-bar'));
      if (!window.confirm('You have unsaved settings changes. Leave this page and discard them?')) {
        return;
      }
    }
    closeChartView();
    setNav(n);
    setSetupView(null);
    setDetailView(null);
    setBoardDetail(null);
  };

  const handleOpenInstrument = (symbol: string, defaultTab: InstrumentTab | 'chart' | 'option-chain', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: SignalChartData) => {
    if (!instrumentView) {
      const cur = localStorage.getItem('kite_terminal_mode');
      setSavedTerminalMode(cur === 'minimized' || cur === 'partial' || cur === 'full' ? cur : 'normal');
      window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'minimized' }));
    }
    // The instrument view renders in the CENTRE slot. If the operator has that pane
    // minimized, it mounts into a collapsed dock and the Chart button looks dead —
    // which is exactly how it was reported. Minimized state persists across reloads,
    // so this is not a transient condition that fixes itself.
    window.dispatchEvent(new CustomEvent('kite-restore-slot', { detail: 'center' }));
    // A detail or setup view outranks the instrument view where `content` is chosen,
    // so opening a chart while either is up would set state that nothing renders.
    // Every other caller clears its siblings; this one did not.
    setDetailView(null);
    setSetupView(null);
    setInstrumentView({ symbol, tab: defaultTab as InstrumentTab, trailTarget, signalData });
  };

  const closeChartView = () => {
    if (savedTerminalMode) {
      window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: savedTerminalMode }));
      setSavedTerminalMode(null);
    }
    setInstrumentView(null);
  };

  let content = null;
  if (setupView) {
    content = <SetupChart token={setupView.token} underlying={setupView.underlying} onClose={() => { closeChartView(); setSetupView(null); }} />;
  } else if (boardDetail) {
    content = <BoardDetailPane signal={boardDetail} onClose={() => { closeChartView(); setBoardDetail(null); }} />;
  } else if (detailView) {
    content = (
      <SignalDetailPane
        token={detailView.token}
        underlying={detailView.underlying}
        timestamp_ms={detailView.timestamp_ms}
        source={detailView.source}
        onClose={() => { closeChartView(); setDetailView(null); }}
        onShowSetup={() => setSetupView(detailView)}
        onShowOptionChain={(u) => { closeChartView(); setInstrumentView({ symbol: u, tab: 'option-chain' }); }}
      />
    );
  } else if (instrumentView) {
    content = (
      <InstrumentPane
        symbol={instrumentView.symbol}
        initialTab={instrumentView.tab}
        trailTarget={instrumentView.trailTarget}
        signalData={instrumentView.signalData}
        onSymbolChange={(newSymbol) => setInstrumentView({ symbol: newSymbol, tab: 'chart', trailTarget: instrumentView.trailTarget, signalData: instrumentView.signalData })}
      />
    );
  } else {
    if (nav === 'dashboard') content = <KiteDashboard />;
    else if (nav === 'astro') content = <AstroPane />;
    else if (nav === 'pcr') content = <PcrPane />;
    else if (nav === 'openingLeaders') content = <OpeningVolumeLeadersPane onOpenChart={(symbol) => handleOpenInstrument(symbol, 'chart')} />;
    else if (nav === 'orders') content = <OrdersPane onOpenBasket={() => setBasketOpen(true)} />;
    else if (nav === 'holdings') content = <PortfolioPane view="holdings" />;
    else if (nav === 'positions') content = <PositionsPane onOpenInstrument={handleOpenInstrument} />;
    else if (nav === 'more') content = <MorePane activeTab={moreTab} onTabChange={setMoreTab} />;
    else if (nav === 'data') content = <DataLakePane />;
    else if (nav === 'adaptiveEdge') content = <AdaptiveEdgePane onOpenChart={handleOpenInstrument} />;
    else if (nav === 'backtest') content = <UnifiedBacktestPane />;
    else if (nav === 'connect') content = <ConnectPane />;
    else if (nav === 'help') content = <HelpPane />;
  }

  return (
    <KiteInteractionMotion>
        <KiteStartupCoordinator statusLoading={kiteStatusLoading} hasStatus={!!kiteStatus} />
        <KiteLayout
          activeNav={nav}
          onNavClick={handleNavClick}
          sidebar={(
            <WatchlistStartupBoundary>
              <SterlingWatchListWithHoldingsSync onOpenInstrument={handleOpenInstrument} />
            </WatchlistStartupBoundary>
          )}
          rightSidebar={(
            <EngineStartupBoundary>
              <AdaptiveEdgeRightSidebar
                onSelectSignal={(sel) => { setInstrumentView(null); setSetupView(null); setDetailView(sel); }}
                onOpenBoardDetail={(signal) => { setInstrumentView(null); setSetupView(null); setDetailView(null); setBoardDetail(signal); }}
                onOpenChart={handleOpenInstrument}
              />
            </EngineStartupBoundary>
          )}
          bottomBar={<EngineTerminal />}
          centerTopBar={(
            <TickerStartupBoundary>
              <KiteTicker onOpenChart={(symbol) => handleOpenInstrument(symbol, 'chart')} />
            </TickerStartupBoundary>
          )}
          content={content}
          onBasketClick={() => setBasketOpen(true)}
          basketCount={basketCount}
        />
        <KiteNotifications />
        <PendingGttProtectionWatcher />
        <KiteSessionGuard />
        <KiteAuthOverlay />
        {isOpen && options && <OrderWindow options={options} onClose={closeOrderWindow} />}
        {basketOpen && <BasketPane onClose={() => setBasketOpen(false)} />}
    </KiteInteractionMotion>
  );
}

export default KiteTab;
