import React, { useState, useEffect, useRef } from 'react';
import { k } from '../../styles/kiteUI';
import { NavItem } from './KiteLayout';
import { toggleKiteStatusCard } from '../../store/useKiteStatusCardStore';

export interface CommandItem {
  id: string;
  category: 'Symbols' | 'Navigation' | 'Docks' | 'Actions';
  title: string;
  subtitle?: string;
  icon?: string;
  shortcut?: string;
  onSelect: () => void;
}

interface KiteCommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavClick: (nav: NavItem) => void;
  onOpenChart?: (symbol: string) => void;
  onToggleDock?: (dock: 'watchlist' | 'signals' | 'terminal') => void;
}

const DEFAULT_SYMBOLS = [
  { symbol: 'NSE:NIFTY 50', name: 'NIFTY 50 Index', exchange: 'NSE' },
  { symbol: 'NSE:NIFTY BANK', name: 'BANKNIFTY Index', exchange: 'NSE' },
  { symbol: 'NSE:FINNIFTY', name: 'FINNIFTY Index', exchange: 'NSE' },
  { symbol: 'NSE:MIDCPNIFTY', name: 'MIDCPNIFTY Index', exchange: 'NSE' },
  { symbol: 'NSE:RELIANCE', name: 'Reliance Industries Ltd', exchange: 'NSE' },
  { symbol: 'NSE:TCS', name: 'Tata Consultancy Services', exchange: 'NSE' },
  { symbol: 'NSE:INFY', name: 'Infosys Limited', exchange: 'NSE' },
  { symbol: 'NSE:HDFCBANK', name: 'HDFC Bank Ltd', exchange: 'NSE' },
  { symbol: 'NSE:ICICIBANK', name: 'ICICI Bank Ltd', exchange: 'NSE' },
  { symbol: 'NSE:SBIN', name: 'State Bank of India', exchange: 'NSE' },
];

export function KiteCommandPalette({
  open,
  onClose,
  onNavClick,
  onOpenChart,
  onToggleDock,
}: KiteCommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (open) onClose();
        else {
          window.dispatchEvent(new CustomEvent('kite-open-command-palette'));
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const commands: CommandItem[] = [
    // Navigation
    { id: 'nav-dashboard', category: 'Navigation', title: 'Main Dashboard', subtitle: 'View margins, holdings summary & market overview', icon: '📊', onSelect: () => onNavClick('dashboard') },
    { id: 'nav-positions', category: 'Navigation', title: 'Positions Dock', subtitle: 'Live open & closed trading positions', icon: '💼', onSelect: () => onNavClick('positions') },
    { id: 'nav-orders', category: 'Navigation', title: 'Orders & Executions', subtitle: 'Order log, basket & pending GTTs', icon: '📝', onSelect: () => onNavClick('orders') },
    { id: 'nav-holdings', category: 'Navigation', title: 'Portfolio Holdings', subtitle: 'Demat portfolio & long term holdings', icon: '🏛', onSelect: () => onNavClick('holdings') },
    { id: 'nav-adaptive', category: 'Navigation', title: 'Adaptive Edge Strategy', subtitle: 'AE AI signals & live strategy board', icon: '⚡', onSelect: () => onNavClick('adaptiveEdge') },
    { id: 'nav-astro', category: 'Navigation', title: 'Astrology Cycle Dock', subtitle: 'Astro time turns & planetary cycles', icon: '🔮', onSelect: () => onNavClick('astro') },
    { id: 'nav-pcr', category: 'Navigation', title: 'PCR Analytics', subtitle: 'Put-Call Ratio live sentiment & strike distribution', icon: '📈', onSelect: () => onNavClick('pcr') },
    { id: 'nav-leaders', category: 'Navigation', title: 'Opening Volume Leaders', subtitle: 'Opening gap & high volume momentum scanner', icon: '🚀', onSelect: () => onNavClick('openingLeaders') },
    { id: 'nav-backtest', category: 'Navigation', title: 'Unified Backtest Engine', subtitle: 'Historical simulation & strategy backtester', icon: '🔬', onSelect: () => onNavClick('backtest') },
    { id: 'nav-data', category: 'Navigation', title: 'Data Lake & TrueData', subtitle: 'Data feeds, historical ticks & market storage', icon: '💾', onSelect: () => onNavClick('data') },

    // Docks Management
    { id: 'dock-watchlist', category: 'Docks', title: 'Toggle Watchlist Dock (Left)', subtitle: 'Show or hide the left watchlist dock', icon: '🗂', onSelect: () => onToggleDock?.('watchlist') },
    { id: 'dock-signals', category: 'Docks', title: 'Toggle Signals Dock (Right)', subtitle: 'Show or hide the right live signals dock', icon: '⚡', onSelect: () => onToggleDock?.('signals') },
    { id: 'dock-terminal', category: 'Docks', title: 'Toggle Terminal Dock (Bottom)', subtitle: 'Show or hide the bottom execution terminal dock', icon: '💻', onSelect: () => onToggleDock?.('terminal') },

    // Actions
    { id: 'act-customize-layout', category: 'Actions', title: 'Customize Layout', subtitle: 'Configure Watchlist, Dashboard, Signals & Terminal docks', icon: '🎨', onSelect: () => window.dispatchEvent(new CustomEvent('kite-open-customize-layout')) },
    { id: 'act-kite-session', category: 'Actions', title: 'Zerodha Kite Account & Session', subtitle: 'Check session status, login or switch account', icon: '🔑', onSelect: () => toggleKiteStatusCard() },
    { id: 'act-replay-dock', category: 'Actions', title: 'Open Replay Simulation Dock', subtitle: 'Run tick-by-tick market session replay', icon: '▶', onSelect: () => window.dispatchEvent(new CustomEvent('kite-toggle-replay-dock')) },
    { id: 'act-restore-all', category: 'Actions', title: 'Restore All Workspace Panes', subtitle: 'Expand all minimized workspace docks', icon: '↺', onSelect: () => window.dispatchEvent(new CustomEvent('kite-restore-all-panes')) },
  ];

  // Symbols
  const symbolCommands: CommandItem[] = DEFAULT_SYMBOLS.map((s) => ({
    id: `sym-${s.symbol}`,
    category: 'Symbols',
    title: s.symbol,
    subtitle: `${s.name} (${s.exchange})`,
    icon: '📊',
    onSelect: () => onOpenChart?.(s.symbol),
  }));

  const allItems = [...symbolCommands, ...commands];
  const q = query.trim().toLowerCase();

  const filtered = q === ''
    ? allItems.slice(0, 12)
    : allItems.filter(
        (item) =>
          item.title.toLowerCase().includes(q) ||
          (item.subtitle && item.subtitle.toLowerCase().includes(q)) ||
          item.category.toLowerCase().includes(q)
      );

  const handleSelect = (item: CommandItem) => {
    onClose();
    item.onSelect();
  };

  return (
    <div
      role="dialog"
      aria-label="Global Command Palette"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200000,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '10vh',
        background: 'rgba(15, 23, 42, 0.45)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        fontFamily: k.fontFamily,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(640px, calc(100vw - 32px))',
          background: 'var(--k-bg)',
          borderRadius: 12,
          border: '1px solid var(--k-border-strong-3)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          maxHeight: '75vh',
        }}
      >
        {/* Search Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '12px 16px',
            borderBottom: '1px solid var(--k-border-2)',
            background: 'var(--k-surface-3)',
          }}
        >
          <span style={{ fontSize: 16, color: 'var(--k-brand)' }}>🔍</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose();
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelectedIndex((i) => Math.min(filtered.length - 1, i + 1));
              }
              if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelectedIndex((i) => Math.max(0, i - 1));
              }
              if (e.key === 'Enter' && filtered[selectedIndex]) {
                e.preventDefault();
                handleSelect(filtered[selectedIndex]);
              }
            }}
            placeholder="Type a symbol, dock name or command... (e.g. NIFTY, Positions, Customize Layout)"
            style={{
              flex: 1,
              border: 0,
              outline: 'none',
              background: 'transparent',
              fontSize: 14,
              color: 'var(--k-text)',
              fontFamily: 'inherit',
            }}
          />
          <kbd
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: '3px 7px',
              borderRadius: 4,
              background: 'var(--k-surface-2)',
              border: '1px solid var(--k-border)',
              color: 'var(--k-dim)',
            }}
          >
            ESC
          </kbd>
        </div>

        {/* Results List */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '8px 0',
          }}
        >
          {filtered.length === 0 ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--k-dim)', fontSize: 12 }}>
              No matching commands or symbols found for "{query}"
            </div>
          ) : (
            filtered.map((item, idx) => {
              const active = idx === selectedIndex;
              return (
                <button
                  key={item.id}
                  type="button"
                  onMouseEnter={() => setSelectedIndex(idx)}
                  onClick={() => handleSelect(item)}
                  style={{
                    width: '100%',
                    padding: '9px 16px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    textAlign: 'left',
                    border: 0,
                    background: active ? 'rgba(240, 100, 40, 0.12)' : 'transparent',
                    borderLeft: active ? '3px solid var(--k-brand)' : '3px solid transparent',
                    cursor: 'pointer',
                    transition: 'background 0.12s',
                  }}
                >
                  <span style={{ fontSize: 15, flexShrink: 0 }}>{item.icon || '⚡'}</span>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 650, color: active ? 'var(--k-brand)' : 'var(--k-text)' }}>
                      {item.title}
                    </div>
                    {item.subtitle && (
                      <div style={{ fontSize: 10.5, color: 'var(--k-dim)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.subtitle}
                      </div>
                    )}
                  </div>
                  <span
                    style={{
                      fontSize: 9.5,
                      fontWeight: 700,
                      padding: '2px 6px',
                      borderRadius: 4,
                      background: 'var(--k-surface-2)',
                      color: 'var(--k-dim-2)',
                      letterSpacing: '.05em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {item.category}
                  </span>
                </button>
              );
            })
          )}
        </div>

        {/* Footer shortcuts helper */}
        <div
          style={{
            padding: '7px 16px',
            borderTop: '1px solid var(--k-border-2)',
            background: 'var(--k-surface-2)',
            fontSize: 10,
            color: 'var(--k-dim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>Use <kbd style={{ fontWeight: 700 }}>↑</kbd> <kbd style={{ fontWeight: 700 }}>↓</kbd> to navigate, <kbd style={{ fontWeight: 700 }}>↵</kbd> to select</span>
          <span>Sterling Kite Command Center</span>
        </div>
      </div>
    </div>
  );
}
