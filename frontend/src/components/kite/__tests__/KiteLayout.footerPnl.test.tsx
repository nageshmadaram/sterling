import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom/vitest';
import { KiteLayout } from '../KiteLayout';

let mockPositionsData: any = { net: [], day: [] };
let mockLtpData: any = {};

vi.mock('../../../hooks/useKite', () => ({
  useKitePositions: () => ({ data: mockPositionsData }),
  useKiteLtp: () => ({ data: mockLtpData }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineActivity: () => ({ data: undefined }),
  useEngineServerLogs: () => ({ data: undefined }),
}));

vi.mock('../../../store/useLiveSignalCount', () => ({
  useLiveSignalCount: (selector: (state: { count: number }) => unknown) => selector({ count: 0 }),
}));

vi.mock('../KiteFooterStatus', () => ({
  KiteFooterStatus: () => <div data-testid="footer-status" />,
}));

const baseProps = {
  activeNav: 'dashboard' as const,
  onNavClick: vi.fn(),
  sidebar: <div>watchlist</div>,
  rightSidebar: <div>signals</div>,
  bottomBar: <div>terminal</div>,
  centerTopBar: <div>ticker</div>,
  content: <div>content</div>,
};

function renderComponent(props = baseProps) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <KiteLayout {...props} />
    </QueryClientProvider>,
  );
}

describe('KiteLayout footer PnL live updates', () => {
  beforeEach(() => {
    localStorage.clear();
    mockPositionsData = { net: [], day: [] };
    mockLtpData = {};
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('does not display P&L button when there are no positions', () => {
    renderComponent();
    expect(screen.queryByText(/P&L/i)).not.toBeInTheDocument();
  });

  it('renders initial broker P&L when open positions exist without LTP feed', () => {
    mockPositionsData = {
      net: [
        {
          exchange: 'BFO',
          tradingsymbol: 'SENSEX2691074700CE',
          quantity: 340,
          pnl: -10737,
          last_price: 167.2,
          multiplier: 1,
        },
      ],
      day: [],
    };
    renderComponent();

    const pnlButton = screen.getByRole('button', { name: /P&L/i });
    expect(pnlButton).toBeInTheDocument();
    expect(pnlButton).toHaveTextContent('−₹10,737.00');
  });

  it('updates P&L in real-time when live LTP moves', () => {
    mockPositionsData = {
      net: [
        {
          exchange: 'BFO',
          tradingsymbol: 'SENSEX2691074700CE',
          quantity: 340,
          pnl: -10737,
          last_price: 167.2,
          multiplier: 1,
        },
      ],
      day: [],
    };
    // Live tick moves to 207.30 (+40.10 points * 340 qty = +13,634 delta => net +2,897)
    mockLtpData = {
      'BFO:SENSEX2691074700CE': { last_price: 207.3 },
    };

    renderComponent();
    const pnlButton = screen.getByRole('button', { name: /P&L/i });
    expect(pnlButton).toBeInTheDocument();
    expect(pnlButton).toHaveTextContent('+₹2,897.00');
  });

  it('clicking the footer P&L button navigates to positions pane', () => {
    const onNavClick = vi.fn();
    mockPositionsData = {
      net: [
        {
          exchange: 'BFO',
          tradingsymbol: 'SENSEX2691074700CE',
          quantity: 340,
          pnl: -10737,
          last_price: 167.2,
          multiplier: 1,
        },
      ],
      day: [],
    };
    renderComponent({ ...baseProps, onNavClick });

    const pnlButton = screen.getByRole('button', { name: /P&L/i });
    fireEvent.click(pnlButton);
    expect(onNavClick).toHaveBeenCalledWith('positions');
  });
});

