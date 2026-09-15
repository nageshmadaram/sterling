import React from 'react';
import { useKitePositions, useKiteQuote } from '../../hooks/useKite';
import { useEngineConfig, useEngineSignals } from '../../hooks/useSterlingKiteEngine';
import { useTickerPins } from '../../store/useTickerPins';
import { k } from '../../styles/kiteUI';
import { MacBootOverlay, MacSkeleton } from './MacLoadingSurface';

interface StartupCoordinatorProps {
  statusLoading: boolean;
  hasStatus: boolean;
}

interface BoundaryProps {
  children: React.ReactNode;
}

interface StartupBoundaryProps extends BoundaryProps {
  busy: boolean;
  testId: string;
  minHeight?: number | string;
  fallback: React.ReactNode;
  minVisibleMs?: number;
  maxVisibleMs?: number;
}

/**
 * Initial-load-only visibility gate.
 *
 * The surface stays visible long enough to avoid a one-frame flash, but it also
 * has a hard ceiling so a slow endpoint never hides the usable application.
 * Once dismissed it never reappears during normal background refetches.
 */
function useInitialBusy(active: boolean, minVisibleMs = 0, maxVisibleMs = 300): boolean {
  const startedAt = React.useRef(Date.now());
  const [visible, setVisible] = React.useState(active);

  React.useEffect(() => {
    if (!active) {
      const elapsed = Date.now() - startedAt.current;
      const delay = Math.max(0, minVisibleMs - elapsed);
      if (delay === 0) {
        setVisible(false);
        return;
      }
      const timer = window.setTimeout(() => setVisible(false), delay);
      return () => window.clearTimeout(timer);
    } else {
      const elapsed = Date.now() - startedAt.current;
      const delay = Math.max(0, maxVisibleMs - elapsed);
      const timer = window.setTimeout(() => setVisible(false), delay);
      return () => window.clearTimeout(timer);
    }
  }, [active, minVisibleMs, maxVisibleMs]);

  return active && visible;
}

function StartupBoundary({
  busy,
  children,
  testId,
  fallback,
  minHeight,
  minVisibleMs = 0,
  maxVisibleMs = 300,
}: StartupBoundaryProps) {
  const visible = useInitialBusy(busy, minVisibleMs, maxVisibleMs);

  return (
    <div
      aria-busy={visible}
      style={{ position: 'relative', height: '100%', minHeight, background: k.bg }}
    >
      <div
        aria-hidden={visible ? 'true' : undefined}
        style={{
          height: '100%',
          opacity: visible ? 0 : 1,
          transform: visible ? 'translate3d(0, 3px, 0)' : 'translate3d(0, 0, 0)',
          pointerEvents: visible ? 'none' : 'auto',
          transition: 'opacity 180ms ease-out, transform 220ms cubic-bezier(.16, 1, .3, 1)',
        }}
      >
        {children}
      </div>

      {visible && (
        <div
          data-testid={testId}
          aria-hidden="true"
          style={{
            position: 'absolute', inset: 0, zIndex: 30,
            overflow: 'hidden', background: k.bg,
          }}
        >
          {fallback}
        </div>
      )}
    </div>
  );
}

function WatchlistSkeleton() {
  return (
    <div style={{ height: '100%', minHeight: 320, background: k.bg, fontFamily: k.fontFamily }}>
      <div style={{ height: 50, padding: '0 12px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: `1px solid ${k.border}` }}>
        <MacSkeleton width={15} height={15} radius={8} />
        <MacSkeleton width="58%" height={10} radius={6} />
        <div style={{ flex: 1 }} />
        <MacSkeleton width={24} height={24} radius={7} />
      </div>
      <div style={{ paddingTop: 5 }}>
        {[0, 1, 2, 3, 4, 5, 6].map((row) => (
          <div
            key={row}
            style={{
              minHeight: 54, padding: '9px 14px', boxSizing: 'border-box',
              display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 72px', gap: 14,
              alignItems: 'center', borderBottom: `1px solid ${k.border}`,
            }}
          >
            <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <MacSkeleton width={row % 3 === 0 ? '62%' : row % 3 === 1 ? '48%' : '72%'} height={10} radius={5} />
              <MacSkeleton width={row % 2 === 0 ? '38%' : '29%'} height={7} radius={4} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
              <MacSkeleton width={58} height={10} radius={5} />
              <MacSkeleton width={42} height={7} radius={4} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EngineSkeleton() {
  return (
    <div style={{ height: '100%', minHeight: 320, background: k.bg, fontFamily: k.fontFamily }}>
      <div style={{ height: 44, padding: '0 14px', display: 'flex', alignItems: 'center', gap: 9, borderBottom: `1px solid ${k.border}` }}>
        <MacSkeleton width={15} height={15} radius={4} />
        <MacSkeleton width={136} height={11} radius={6} />
        <MacSkeleton width={24} height={14} radius={4} />
        <div style={{ flex: 1 }} />
        {[0, 1, 2, 3].map((i) => <MacSkeleton key={i} width={26} height={26} radius={7} />)}
      </div>
      <div style={{ height: 3, background: '#edf0f3', overflow: 'hidden' }}>
        <div style={{ width: '36%', height: '100%', background: 'linear-gradient(90deg, #d9e7fb, #8bb8ff, #d9e7fb)' }} />
      </div>
      <div style={{ padding: '18px 16px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 22 }}>
          <MacSkeleton width="44%" height={11} radius={6} />
          <div style={{ flex: 1 }} />
          <MacSkeleton width={72} height={22} radius={11} />
        </div>
        {[0, 1, 2, 3, 4].map((row) => (
          <div key={row} style={{ padding: '12px 0', display: 'flex', alignItems: 'center', gap: 12, borderBottom: `1px solid ${k.border}` }}>
            <MacSkeleton width={8} height={8} radius={8} />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <MacSkeleton width={row % 2 === 0 ? '46%' : '61%'} height={10} radius={5} />
              <MacSkeleton width={row % 2 === 0 ? '31%' : '39%'} height={7} radius={4} />
            </div>
            <MacSkeleton width={62} height={10} radius={5} />
          </div>
        ))}
      </div>
    </div>
  );
}

function TickerSkeleton({ count }: { count: number }) {
  return (
    <div style={{ minHeight: 124, padding: '10px 20px', display: 'flex', alignItems: 'stretch', gap: 10, overflow: 'hidden', borderBottom: `1px solid ${k.border}`, boxSizing: 'border-box', fontFamily: k.fontFamily }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{ width: 250, minHeight: 102, flexShrink: 0, padding: '12px 14px', boxSizing: 'border-box', border: '1px solid #d7d9dd', borderRadius: 7, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 76px', gap: 12, alignItems: 'center', background: 'var(--k-bg)' }}>
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 9 }}>
            <MacSkeleton width={i === 1 ? '74%' : '58%'} height={9} radius={5} />
            <MacSkeleton width={i === 2 ? '64%' : '78%'} height={24} radius={7} />
            <div style={{ display: 'flex', gap: 10 }}>
              <MacSkeleton width={43} height={9} radius={5} />
              <MacSkeleton width={49} height={9} radius={5} />
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', height: 52, gap: 4 }}>
            {[18, 29, 23, 40, 33, 46, 38].map((height, index) => (
              <MacSkeleton key={index} width={7} height={height} radius={4} style={{ alignSelf: 'flex-end' }} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function KiteStartupCoordinator({ statusLoading, hasStatus }: StartupCoordinatorProps) {
  const visible = useInitialBusy(statusLoading && !hasStatus, 0, 300);
  const [phase, setPhase] = React.useState(0);

  React.useEffect(() => {
    if (!visible) return;
    const workspaceTimer = window.setTimeout(() => setPhase(1), 280);
    const marketTimer = window.setTimeout(() => setPhase(2), 720);
    return () => {
      window.clearTimeout(workspaceTimer);
      window.clearTimeout(marketTimer);
    };
  }, [visible]);

  const copy = [
    ['Checking Kite session', 'Restoring your secure broker connection'],
    ['Restoring workspace', 'Loading watchlists, layout and portfolio surfaces'],
    ['Starting live market surfaces', 'Preparing quotes, signals and chart services'],
  ][phase] || ['Preparing Sterling Kite', 'Starting your trading workspace'];

  return <MacBootOverlay active={visible} title={copy[0]} detail={copy[1]} />;
}

export function WatchlistStartupBoundary({ children }: BoundaryProps) {
  const positions = useKitePositions(true);
  const hadStoredWatchlist = React.useMemo(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem('sterling.kite.watchlist.v1') != null
      || window.localStorage.getItem('sterling.kite.watchlist.manual-empty.v1') === '1';
  }, []);
  const busy = !hadStoredWatchlist && !positions.data && positions.isLoading;

  return (
    <StartupBoundary busy={busy} testId="kite-watchlist-startup" minHeight={320} fallback={<WatchlistSkeleton />}>
      {children}
    </StartupBoundary>
  );
}

export function EngineStartupBoundary({ children }: BoundaryProps) {
  const config = useEngineConfig();
  const signals = useEngineSignals();
  const missingInitialData = !config.data || !signals.data;
  const busy = missingInitialData && (config.isLoading || signals.isLoading || config.isFetching || signals.isFetching);

  return (
    <StartupBoundary busy={busy} testId="kite-engine-startup" minHeight={320} fallback={<EngineSkeleton />}>
      {children}
    </StartupBoundary>
  );
}

export function TickerStartupBoundary({ children }: BoundaryProps) {
  const pins = useTickerPins((state) => state.pins);
  const quoteQuery = useKiteQuote(pins, pins.length > 0);
  const busy = pins.length > 0 && !quoteQuery.data && (quoteQuery.isLoading || quoteQuery.isFetching);
  const count = Math.min(3, Math.max(1, pins.length));

  return (
    <StartupBoundary busy={busy} testId="kite-ticker-startup" minHeight={pins.length > 0 ? 124 : 56} fallback={<TickerSkeleton count={count} />}>
      {children}
    </StartupBoundary>
  );
}

export default KiteStartupCoordinator;
