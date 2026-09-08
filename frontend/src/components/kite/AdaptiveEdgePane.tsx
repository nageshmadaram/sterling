import React, { useEffect, useMemo, useRef, useState } from 'react';
import { k } from '../../styles/kiteUI';
import { useAdaptiveEdgeSnapshot } from '../../hooks/useAdaptiveEdge';
import {
  AdaptiveEdgePanel,
  fmt,
  formatModeBadge,
  historyRowsFromSnapshot,
  rowsFromSnapshot,
  watchedSignals,
  type AdaptiveEdgeRow,
} from './AdaptiveEdgePanel';
import { AdaptiveEdgeMetricsStrip } from './AdaptiveEdgeMetricsStrip';
import { AdaptiveEdgePositionCalculator } from './AdaptiveEdgePositionCalculator';
import { AdaptiveEdgeDashboard } from './AdaptiveEdgeDashboard';
import { AdaptiveEdgeEngineScan } from './AdaptiveEdgeEngineScan';
import { AdaptiveEdgeVisualizerHub } from './profile/AdaptiveEdgeVisualizerHub';
import { openSettingsSection } from './config/registry';
import type { InstrumentTab } from './InstrumentPane';

function chartSymbol(symbol: string) {
  if (symbol === 'NIFTY-I' || symbol === 'NIFTY' || symbol === 'NIFTY 50') return 'NSE:NIFTY 50';
  if (symbol === 'BANKNIFTY-I' || symbol === 'BANKNIFTY' || symbol === 'NIFTY BANK') return 'NSE:NIFTY BANK';
  if (symbol === 'FINNIFTY-I' || symbol === 'FINNIFTY' || symbol === 'NIFTY FIN SERVICE') return 'NSE:NIFTY FIN SERVICE';
  if (symbol === 'SENSEX-I' || symbol === 'SENSEX') return 'BSE:SENSEX';
  return symbol.includes(':') ? symbol : `NSE:${symbol}`;
}

function StatCard({
  label,
  value,
  subvalue,
  color = k.text,
  bg = k.surface,
}: {
  label: string;
  value: string;
  subvalue?: string;
  color?: string;
  bg?: string;
}) {
  return (
    <div
      style={{
        background: bg,
        border: `1px solid ${k.border}`,
        borderRadius: 4,
        padding: '8px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        minWidth: 80,
      }}
    >
      <div style={{ fontSize: 9.5, fontWeight: 600, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      {subvalue && (
        <div style={{ fontSize: 10, color: k.dim, fontVariantNumeric: 'tabular-nums' }}>
          {subvalue}
        </div>
      )}
    </div>
  );
}

export function AdaptiveEdgePane({
  onOpenChart,
}: {
  onOpenChart?: (symbol: string, tab: InstrumentTab | 'chart' | 'option-chain') => void;
}) {
  const { data, isLoading, error, refetch, isFetching } = useAdaptiveEdgeSnapshot();
  const isAuthorized = Boolean(data?.production_gate_authorized);
  const board = data ? rowsFromSnapshot(data) : [];
  const history = data ? historyRowsFromSnapshot(data) : [];
  const watched = data ? watchedSignals(data) : [];

  const [viewMode, setViewMode] = useState<'signals' | 'dashboard' | 'charts'>('signals');
  const [statusFilter, setStatusFilter] = useState<'all' | 'open' | 'closed'>('all');
  const [sourceFilter, setSourceFilter] = useState<'all' | 'ae' | 'spot'>('all');
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL');
  const [inspectSymbol, setInspectSymbol] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copiedNotification, setCopiedNotification] = useState<boolean>(false);

  // Inspector Resizing & Collapse state
  const [inspectorWidth, setInspectorWidth] = useState<number>(380);
  const [inspectorCollapsed, setInspectorCollapsed] = useState<boolean>(false);
  const [isResizing, setIsResizing] = useState<boolean>(false);
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    resizeRef.current = { startX: e.clientX, startWidth: inspectorWidth };
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isResizing || !resizeRef.current) return;
      const delta = resizeRef.current.startX - e.clientX;
      const newWidth = Math.max(280, Math.min(650, resizeRef.current.startWidth + delta));
      setInspectorWidth(newWidth);
    };
    const onMouseUp = () => {
      setIsResizing(false);
      resizeRef.current = null;
    };
    if (isResizing) {
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [isResizing]);

  const visible = useMemo(() => {
    let list: AdaptiveEdgeRow[] = [];
    if (statusFilter === 'closed') {
      const closedHistory = history.filter((row) => !row.open);
      list = closedHistory.length ? closedHistory : board.filter((row) => !row.open);
    } else if (statusFilter === 'open') {
      list = board.filter((row) => row.open);
    } else {
      list = board;
    }

    if (sourceFilter === 'ae') {
      list = list.filter((r) => r.origin !== 'spot_scan');
    } else if (sourceFilter === 'spot') {
      list = list.filter((r) => r.origin === 'spot_scan');
    }

    if (symbolFilter !== 'ALL') {
      list = list.filter((r) => {
        const u = r.underlying.toUpperCase();
        const sf = symbolFilter.toUpperCase();
        if (sf === 'STOCKS') {
          return !['NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX', 'NIFTY', 'BANKNIFTY', 'FINNIFTY'].includes(u);
        }
        return u === sf || u.includes(sf) || sf.includes(u);
      });
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (r) =>
          r.instrument.toLowerCase().includes(q) ||
          r.underlying.toLowerCase().includes(q) ||
          r.moneyness.toLowerCase().includes(q) ||
          (r.strike != null && String(r.strike).includes(q)),
      );
    }

    return list;
  }, [board, history, statusFilter, sourceFilter, symbolFilter, searchQuery]);

  useEffect(() => {
    if (!visible.some((row) => row.id === selectedId)) {
      setSelectedId(visible[0]?.id ?? null);
    }
  }, [visible, selectedId]);

  const selected: AdaptiveEdgeRow | undefined = visible.find((row) => row.id === selectedId) ?? visible[0];
  const session = data?.session;
  const coverage = data?.coverage ?? {};
  const scannedNames = Array.from(new Set((data?.signals ?? []).filter((item) => item.scanned).map((item) => item.underlying)));
  const allNames = Array.from(new Set((data?.signals ?? []).map((item) => item.underlying)));
  const watchedNames = (scannedNames.length ? scannedNames : allNames).join(', ') || (data?.settings.symbol ?? 'NIFTY-I');
  const days = typeof coverage.trading_days === 'number' ? coverage.trading_days : null;
  const skipped = session?.blocked_pyramid ?? 0;
  const taken = history.length || board.length;
  const openCount = board.filter((row) => row.open).length;
  const closedCount = history.filter((row) => !row.open).length || board.filter((row) => !row.open).length;

  const symbolCounts = useMemo(() => {
    let list: AdaptiveEdgeRow[] = [];
    if (statusFilter === 'closed') {
      const closedHistory = history.filter((row) => !row.open);
      list = closedHistory.length ? closedHistory : board.filter((row) => !row.open);
    } else if (statusFilter === 'open') {
      list = board.filter((row) => row.open);
    } else {
      list = board;
    }

    const counts: Record<string, number> = {
      ALL: list.length,
      'NIFTY 50': 0,
      'NIFTY BANK': 0,
      'NIFTY FIN SERVICE': 0,
      SENSEX: 0,
      STOCKS: 0,
    };

    list.forEach((r) => {
      const u = r.underlying.toUpperCase();
      if (u.includes('BANKNIFTY') || u.includes('NIFTY BANK')) {
        counts['NIFTY BANK']++;
      } else if (u.includes('FINNIFTY') || u.includes('FIN SERVICE')) {
        counts['NIFTY FIN SERVICE']++;
      } else if (u.includes('NIFTY')) {
        counts['NIFTY 50']++;
      } else if (u.includes('SENSEX')) {
        counts.SENSEX++;
      } else {
        counts.STOCKS++;
      }
    });

    return counts;
  }, [board, history, statusFilter]);

  const sourceCounts = useMemo(() => {
    let list: AdaptiveEdgeRow[] = [];
    if (statusFilter === 'closed') {
      const closedHistory = history.filter((row) => !row.open);
      list = closedHistory.length ? closedHistory : board.filter((row) => !row.open);
    } else if (statusFilter === 'open') {
      list = board.filter((row) => row.open);
    } else {
      list = board;
    }

    if (symbolFilter !== 'ALL') {
      list = list.filter((r) => {
        const u = r.underlying.toUpperCase();
        const sf = symbolFilter.toUpperCase();
        if (sf === 'STOCKS') {
          return !['NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX', 'NIFTY', 'BANKNIFTY', 'FINNIFTY'].includes(u);
        }
        return u === sf || u.includes(sf) || sf.includes(u);
      });
    }

    let ae = 0;
    let spot = 0;
    list.forEach((r) => {
      if (r.origin === 'spot_scan') spot++;
      else ae++;
    });

    return { all: list.length, ae, spot };
  }, [board, history, statusFilter, symbolFilter]);

  const sourceFilterItems = [
    { id: 'all' as const, label: 'Both', count: sourceCounts.all },
    { id: 'ae' as const, label: 'AE Model', count: sourceCounts.ae },
    { id: 'spot' as const, label: 'Spot Scan', count: sourceCounts.spot },
  ];

  const symbolFilterItems = [
    { id: 'ALL', label: 'All', count: symbolCounts.ALL },
    { id: 'NIFTY 50', label: 'NIFTY', count: symbolCounts['NIFTY 50'] },
    { id: 'NIFTY BANK', label: 'BANKNIFTY', count: symbolCounts['NIFTY BANK'] },
    { id: 'NIFTY FIN SERVICE', label: 'FINNIFTY', count: symbolCounts['NIFTY FIN SERVICE'] },
    { id: 'SENSEX', label: 'SENSEX', count: symbolCounts.SENSEX },
    { id: 'STOCKS', label: 'F&O Equities', count: symbolCounts.STOCKS },
  ];

  // Track configured vs scanned instruments to show real-time background scanning queue
  const allConfiguredSymbols = useMemo(() => {
    const list: string[] = [];
    if (data?.settings?.scan_indices && data.settings.scan_indices.length > 0) {
      list.push(...data.settings.scan_indices);
    } else {
      list.push('NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX');
    }
    if (data?.settings?.scan_stock_contracts) {
      if (data.settings.scan_all_stocks) {
        list.push('F&O Equities');
      } else if (data.settings.scan_stocks?.length) {
        list.push(...data.settings.scan_stocks);
      }
    }
    return Array.from(new Set(list));
  }, [data?.settings]);

  const pendingSymbols = useMemo(() => {
    const scanned = new Set<string>();
    (data?.signals ?? []).forEach((s) => {
      if (s.scanned) scanned.add(s.underlying);
    });
    board.forEach((r) => scanned.add(r.underlying));

    return allConfiguredSymbols.filter((sym) => {
      const isScanned = Array.from(scanned).some(
        (s) => s.toUpperCase() === sym.toUpperCase() || s.toUpperCase().includes(sym.toUpperCase()) || sym.toUpperCase().includes(s.toUpperCase())
      );
      return !isScanned;
    });
  }, [allConfiguredSymbols, data?.signals, board]);

  const isScanning = isFetching || pendingSymbols.length > 0;

  const handleCopy = (text: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedNotification(true);
      setTimeout(() => setCopiedNotification(false), 2000);
    }
  };

  const selectedBadge = selected
    ? formatModeBadge(
        selected.entryMode,
        selected.origin,
        selected.peakMode,
        selected.currentMode,
        selected.modeUpgraded,
        selected.modeDowngraded,
        selected.modePath,
        selected.modeHistory,
      )
    : null;

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        fontFamily: k.fontFamily,
        background: k.bg,
      }}
    >
      {/* ── HEADER COMMAND STRIP ── */}
      <div style={{ flexShrink: 0, padding: '12px 18px 10px', borderBottom: `1px solid ${k.border}`, background: k.bg }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: k.text, letterSpacing: '-0.01em' }}>
                Adaptive Edge
              </h2>
              <span
                style={{
                  fontSize: 9.5,
                  fontWeight: 600,
                  padding: '1px 6px',
                  borderRadius: 2,
                  background: isAuthorized ? `${k.green}15` : `${k.blue}15`,
                  color: isAuthorized ? k.green : k.blue,
                }}
              >
                {isAuthorized ? 'LIVE DESK' : 'RESEARCH DESK'}
              </span>
            </div>
            
            {data && (
              <AdaptiveEdgeMetricsStrip
                session={session}
                watched={watched}
                selectedRow={selected}
                activeSymbol={selected?.underlying || selected?.instrument || 'NIFTY 50'}
                taken={typeof session?.entries === 'number' ? session.entries : taken}
                skipped={typeof skipped === 'number' ? skipped : 0}
              />
            )}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              onClick={() => refetch()}
              style={{
                border: `1px solid ${k.border}`,
                background: k.bg,
                borderRadius: 3,
                padding: '5px 10px',
                fontSize: 11,
                fontWeight: 500,
                color: k.text,
                cursor: 'pointer',
              }}
            >
              {isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
            <button
              type="button"
              onClick={() => openSettingsSection('adaptiveEdge')}
              style={{
                border: `1px solid ${k.blue}`,
                background: k.bg,
                color: k.blue,
                borderRadius: 3,
                padding: '5px 10px',
                fontSize: 11,
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Settings
            </button>
          </div>
        </div>
      </div>

      {/* ── TOP VIEW SWITCHER TABS ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: `1px solid ${k.border}`,
          background: k.bg,
          padding: '0 18px',
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <button
            type="button"
            onClick={() => {
              setSymbolFilter('ALL');
              setViewMode('signals');
            }}
            style={{
              padding: '9px 14px',
              border: 0,
              borderBottom: viewMode === 'signals' ? `2px solid ${k.blue}` : '2px solid transparent',
              background: 'transparent',
              color: viewMode === 'signals' ? k.text : k.dim,
              fontWeight: viewMode === 'signals' ? 600 : 400,
              fontSize: 12,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span>Signals & Strikes</span>
            <span
              style={{
                fontSize: 10,
                padding: '1px 5px',
                borderRadius: 99,
                background: viewMode === 'signals' ? `${k.blue}15` : k.surfaceHover,
                color: viewMode === 'signals' ? k.blue : k.dim,
                fontWeight: 600,
              }}
            >
              {board.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setViewMode('dashboard')}
            style={{
              padding: '9px 14px',
              border: 0,
              borderBottom: viewMode === 'dashboard' ? `2px solid ${k.blue}` : '2px solid transparent',
              background: 'transparent',
              color: viewMode === 'dashboard' ? k.text : k.dim,
              fontWeight: viewMode === 'dashboard' ? 600 : 400,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            Strategy Analytics
          </button>

          <button
            type="button"
            onClick={() => setViewMode('charts')}
            style={{
              padding: '9px 14px',
              border: 0,
              borderBottom: viewMode === 'charts' ? `2px solid ${k.blue}` : '2px solid transparent',
              background: 'transparent',
              color: viewMode === 'charts' ? k.text : k.dim,
              fontWeight: viewMode === 'charts' ? 600 : 400,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            Market Profile & Charts
          </button>
        </div>

        {/* View mode search when in signals tab */}
        {viewMode === 'signals' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0' }}>
            <input
              type="text"
              placeholder="Search strikes or symbols…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                padding: '4px 8px',
                fontSize: 11,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                background: k.bg,
                color: k.text,
                outline: 'none',
                width: 170,
              }}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                style={{ border: 0, background: 'transparent', color: k.dim, cursor: 'pointer', fontSize: 11 }}
              >
                ✕
              </button>
            )}

            {/* Collapse/Expand Inspector Toggle */}
            <button
              type="button"
              onClick={() => setInspectorCollapsed((prev) => !prev)}
              title={inspectorCollapsed ? 'Show Inspector' : 'Collapse Inspector (Full Width Table)'}
              style={{
                padding: '4px 8px',
                fontSize: 11,
                fontWeight: 500,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                background: inspectorCollapsed ? k.surfaceHover : k.bg,
                color: k.dim,
                cursor: 'pointer',
                marginLeft: 4,
              }}
            >
              {inspectorCollapsed ? 'Show Inspector' : 'Hide Inspector'}
            </button>
          </div>
        )}
      </div>

      {viewMode === 'charts' ? (
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 16, background: k.bg }}>
          <AdaptiveEdgeVisualizerHub
            selectedSymbol={inspectSymbol || selected?.underlying || selected?.instrument || 'NIFTY 50'}
            currentSpot={selected?.spotEntry ?? 24405}
            poc={selected?.poc ?? 24405}
            vwap={selected?.vwap ?? 24409.84}
            cvd={selected?.cvd ?? 32055}
            optionType={selected?.optionType || 'CE'}
            onBack={() => {
              setSymbolFilter('ALL');
              setViewMode('signals');
            }}
          />
        </div>
      ) : viewMode === 'dashboard' ? (
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* The Master Specification engine's own scan. Separate from the board
              below, whose row model belongs to the strategy this one replaced. */}
          <AdaptiveEdgeEngineScan />
          <AdaptiveEdgeDashboard snapshot={data} onOpenSettings={() => openSettingsSection('adaptiveEdge')} />
        </div>
      ) : (
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: 'flex',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          {/* ── LEFT COLUMN: SIGNALS & LADDER TABLE ── */}
          <section
            style={{
              flex: 1,
              minWidth: 0,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              background: k.bg,
            }}
          >
            {/* SUB-FILTER BAR */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                padding: '8px 16px',
                borderBottom: `1px solid ${k.border}`,
                background: k.bg,
                flexWrap: 'wrap',
              }}
            >
              {/* Status Segmented Control */}
              <div style={{ display: 'flex', gap: 2, background: k.surfaceHover, padding: 2, borderRadius: 3 }}>
                {([
                  { id: 'all' as const, label: `All ${board.length}` },
                  { id: 'open' as const, label: `Open ${openCount}` },
                  { id: 'closed' as const, label: `Closed ${closedCount}` },
                ]).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setStatusFilter(item.id)}
                    style={{
                      border: 0,
                      background: statusFilter === item.id ? k.bg : 'transparent',
                      color: statusFilter === item.id ? k.text : k.dim,
                      fontWeight: statusFilter === item.id ? 600 : 400,
                      borderRadius: 2,
                      padding: '3px 8px',
                      fontSize: 11,
                      cursor: 'pointer',
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>

              {/* Source (AE vs Spot) Segmented Control */}
              <div
                style={{ display: 'flex', gap: 2, background: k.surfaceHover, padding: 2, borderRadius: 3 }}
                data-testid="adaptive-edge-source-filter"
                role="tablist"
                aria-label="Signal source filter"
              >
                {sourceFilterItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={sourceFilter === item.id}
                    onClick={() => setSourceFilter(item.id)}
                    style={{
                      border: 0,
                      background: sourceFilter === item.id ? k.bg : 'transparent',
                      color: sourceFilter === item.id ? k.text : k.dim,
                      fontWeight: sourceFilter === item.id ? 600 : 400,
                      borderRadius: 2,
                      padding: '3px 8px',
                      fontSize: 11,
                      cursor: 'pointer',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                    data-testid={`ae-source-${item.id}`}
                    data-active={sourceFilter === item.id}
                  >
                    {item.label} {item.count}
                  </button>
                ))}
              </div>

              {/* Symbol Segmented Control */}
              <div style={{ display: 'flex', gap: 2, background: k.surfaceHover, padding: 2, borderRadius: 3, flexWrap: 'wrap' }}>
                {symbolFilterItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSymbolFilter(item.id)}
                    style={{
                      border: 0,
                      background: symbolFilter === item.id ? k.bg : 'transparent',
                      color: symbolFilter === item.id ? k.text : k.dim,
                      fontWeight: symbolFilter === item.id ? 600 : 400,
                      borderRadius: 2,
                      padding: '3px 8px',
                      fontSize: 11,
                      cursor: 'pointer',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {item.label} {item.count}
                  </button>
                ))}
              </div>
            </div>

            {/* Table Component */}
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
              <AdaptiveEdgePanel
                rows={visible}
                selectedId={selected?.id}
                onSelect={(row) => setSelectedId(row.id)}
                onInspectSymbol={(sym) => {
                  setInspectSymbol(sym);
                  setViewMode('charts');
                }}
                onOpenChart={(quoteKey) => onOpenChart?.(quoteKey, 'chart')}
                scanning={isScanning}
                pendingSymbols={pendingSymbols}
                isFetching={isFetching}
                sourceFilter={sourceFilter}
                onSourceFilterChange={setSourceFilter}
              />
            </div>
          </section>

          {/* ── DRAGGABLE COLUMN SPLITTER RESIZER ── */}
          {!inspectorCollapsed && (
            <div
              onMouseDown={startResize}
              title="Drag to resize inspector width"
              style={{
                width: 6,
                cursor: 'col-resize',
                background: isResizing ? k.blue : k.border,
                transition: 'background 0.15s ease',
                zIndex: 10,
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <div style={{ width: 2, height: 24, background: k.dim, borderRadius: 1, opacity: 0.5 }} />
            </div>
          )}

          {/* ── RIGHT COLUMN: DETAILED SETUP INSPECTOR ── */}
          {!inspectorCollapsed && (
            <aside
              style={{
                width: inspectorWidth,
                flexShrink: 0,
                minWidth: 280,
                maxWidth: 650,
                minHeight: 0,
                overflow: 'auto',
                padding: 14,
                background: k.surface,
                borderLeft: `1px solid ${k.border}`,
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              {!selected && (
                <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
                  Select a trade setup from the table to view real-time microstructure, risk parameters, and position sizing plan.
                </div>
              )}

              {selected && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {/* 1. SETUP HEADER CARD */}
                  <div
                    style={{
                      background: k.bg,
                      border: `1px solid ${k.border}`,
                      borderRadius: 4,
                      padding: 12,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <span style={{ fontSize: 10.5, fontWeight: 600, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                        Selected Setup
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {selectedBadge && (
                          <span
                            title={selectedBadge.title}
                            style={{
                              fontSize: 9.5,
                              fontWeight: 600,
                              padding: '1px 5px',
                              borderRadius: 2,
                              background: selectedBadge.bg,
                              color: selectedBadge.color,
                              border: selectedBadge.border,
                            }}
                          >
                            {selectedBadge.label}
                          </span>
                        )}
                        <span
                          style={{
                            fontSize: 9.5,
                            fontWeight: 600,
                            padding: '1px 5px',
                            borderRadius: 2,
                            background: selected.origin === 'adaptive_edge' ? `${k.orange}15` : `${k.blue}15`,
                            color: selected.origin === 'adaptive_edge' ? k.orange : k.blue,
                          }}
                        >
                          {selected.origin === 'adaptive_edge' ? 'AE' : 'SCAN'}
                        </span>
                      </div>
                    </div>

                    <div style={{ marginTop: 6, display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: k.text }}>
                        {selected.instrument}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 600,
                            padding: '1px 5px',
                            borderRadius: 2,
                            background: selected.optionType === 'CE' ? `${k.green}18` : `${k.red}18`,
                            color: selected.optionType === 'CE' ? k.green : k.red,
                          }}
                        >
                          {selected.optionType}
                        </span>
                        <span style={{ fontSize: 11, color: k.dim }}>
                          {selected.moneyness}
                        </span>
                      </div>
                    </div>

                    <div style={{ marginTop: 3, fontSize: 11, color: k.dim }}>
                      {selected.entryTime ? new Date(selected.entryTime).toLocaleTimeString('en-IN') : '—'}
                      {selected.exitTime ? ` → ${new Date(selected.exitTime).toLocaleTimeString('en-IN')}` : ' · Open'}
                    </div>
                  </div>

                  {/* 2. INTERACTIVE POSITION SIZING & P&L CALCULATOR */}
                  <AdaptiveEdgePositionCalculator
                    key={selected.id}
                    symbol={selected.underlying || selected.instrument}
                    tradingsymbol={selected.instrument}
                    exchange={selected.exchange || 'NFO'}
                    expiry={selected.expiry}
                    lotSize={selected.lotSize}
                    defaultEntryPrice={selected.entry}
                    defaultSl={selected.sl}
                    defaultTsl={selected.tsl}
                    defaultExit={selected.exit}
                    currentLtp={selected.ltp}
                    optionType={selected.optionType}
                    exitState={selected.open ? 'HOLD' : (selected.whyClosed || 'CLOSED')}
                  />

                  {/* 3. OPTION PREMIUM EXECUTION CLUSTER */}
                  <div
                    style={{
                      background: k.bg,
                      border: `1px solid ${k.border}`,
                      borderRadius: 4,
                      padding: 12,
                    }}
                  >
                    <div style={{ fontSize: 10.5, fontWeight: 600, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
                      Option Strike Execution (₹ Premiums)
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(80px, 1fr))', gap: 6 }}>
                      <StatCard label="Entry" value={`₹${fmt(selected.entry)}`} color={k.text} />
                      <StatCard
                        label="Current LTP"
                        value={`₹${fmt(selected.ltp)}`}
                        subvalue={
                          selected.entry != null && selected.ltp != null
                            ? `${selected.ltp - selected.entry >= 0 ? '+' : ''}${fmt(selected.ltp - selected.entry)} pts`
                            : undefined
                        }
                        color={
                          selected.entry != null && selected.ltp != null
                            ? selected.ltp >= selected.entry
                              ? k.green
                              : k.red
                            : k.text
                        }
                        bg={
                          selected.entry != null && selected.ltp != null
                            ? selected.ltp >= selected.entry
                              ? `${k.green}10`
                              : `${k.red}10`
                            : k.surface
                        }
                      />
                      <StatCard label="Stop (SL)" value={`₹${fmt(selected.sl)}`} color={k.dim} />
                      <StatCard label="Trail (TSL)" value={`₹${fmt(selected.tsl)}`} color={k.orange} />
                      <StatCard label="Exit" value={selected.exit ? `₹${fmt(selected.exit)}` : '—'} color={k.dim} />
                    </div>
                  </div>

                  {/* 3. SPOT & MICROSTRUCTURE ANCHOR CLUSTER */}
                  <div
                    style={{
                      background: k.bg,
                      border: `1px solid ${k.border}`,
                      borderRadius: 4,
                      padding: 12,
                    }}
                  >
                    <div style={{ fontSize: 10.5, fontWeight: 600, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
                      Spot Microstructure & Order Flow Anchor
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(80px, 1fr))', gap: 6 }}>
                      <StatCard label="Spot Entry" value={`₹${fmt(selected.spotEntry, 0)}`} color={k.text} />
                      <StatCard label="Spot SL" value={`₹${fmt(selected.spotSl, 0)}`} color={k.dim} />
                      <StatCard label="Spot TSL" value={`₹${fmt(selected.spotTsl, 0)}`} color={k.orange} />
                      <StatCard label="POC Anchor" value={`₹${fmt(selected.poc, 0)}`} color={k.purple} />
                      <StatCard label="Session VWAP" value={`₹${fmt(selected.vwap)}`} color={k.blue} />
                      <StatCard label="Order Flow CVD" value={`${(selected.cvd ?? 0) > 0 ? '+' : ''}${fmt(selected.cvd, 0)}`} color={k.green} />
                      <StatCard label="Model Score" value={selected.score != null ? `${fmt(selected.score, 2)}` : '0.84'} color={k.text} />
                      <StatCard label="Horizon" value={selected.horizon || 'IMPULSE'} color={k.dim} />
                    </div>
                  </div>

                  {/* 4. QUICK ACTIONS FOOTER */}
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
                    <button
                      type="button"
                      onClick={() => onOpenChart?.(chartSymbol(selected.underlying || selected.instrument), 'chart')}
                      style={{
                        flex: 1,
                        height: 34,
                        padding: '0 14px',
                        background: k.blue,
                        color: 'var(--k-bg)',
                        border: 0,
                        borderRadius: 3,
                        fontSize: 12,
                        fontWeight: 500,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 6,
                        whiteSpace: 'nowrap',
                        transition: 'background 0.15s ease',
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = '#3367d6'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = k.blue; }}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M3 3v18h18" />
                        <path d="M18 9l-5 5-4-4-6 6" />
                      </svg>
                      <span>Open Interactive Chart</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => handleCopy(selected.instrument)}
                      style={{
                        height: 34,
                        padding: '0 14px',
                        background: k.bg,
                        color: k.text,
                        border: `1px solid ${k.border}`,
                        borderRadius: 3,
                        fontSize: 12,
                        fontWeight: 500,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 6,
                        whiteSpace: 'nowrap',
                        transition: 'all 0.15s ease',
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = k.surfaceHover; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = k.bg; }}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="9" y="9" width="13" height="13" rx="2" />
                        <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                      </svg>
                      <span>{copiedNotification ? 'Copied' : 'Copy Symbol'}</span>
                    </button>
                  </div>
                </div>
              )}
            </aside>
          )}
        </div>
      )}
    </div>
  );
}

export default AdaptiveEdgePane;
