import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom/vitest';
import { EngineTerminal, TERMINAL_MODE_KEY } from '../EngineTerminal';
import { KiteLayout } from '../KiteLayout';
import { WORKSPACE_LAYOUT_KEY } from '../workspaceLayout';

// KiteLayout reads useQueryClient() directly (to invalidate the signals query
// on scan-state edges), so it needs a real QueryClientProvider ancestor even
// though useEngineActivity itself is mocked below.
function renderLayout(componentProps: typeof props) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><KiteLayout {...componentProps} /></QueryClientProvider>);
}

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineActivity: () => ({ data: undefined }),
  useEngineServerLogs: () => ({ data: undefined }),
}));
vi.mock('../../../store/useLiveSignalCount', () => ({
  useLiveSignalCount: (selector: (state: { count: number }) => unknown) => selector({ count: 27 }),
}));
// The footer now carries broker and per-strategy state, which reaches for every
// engine's hook. None of that is under test here, and this file is about the
// workspace, so the strip is stubbed rather than each of its six hooks mocked.
vi.mock('../KiteFooterStatus', () => ({ KiteFooterStatus: () => <div data-testid="footer-status" /> }));

const props = {
  activeNav: 'dashboard' as const,
  onNavClick: vi.fn(),
  sidebar: <div>watchlist body</div>,
  rightSidebar: <div>signals body</div>,
  bottomBar: <div>terminal body</div>,
  centerTopBar: <div>ticker strip</div>,
  content: <div>dashboard body</div>,
};

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('KiteLayout advanced workspace', () => {
  it('gives all four panes independent window controls', () => {
    renderLayout(props);

    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      expect(screen.getByLabelText(`${title} pane`)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Minimize ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Half screen ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Maximize ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Full screen ${title}` })).toBeInTheDocument();
    }
    expect(screen.queryByTitle('Show / hide left sidebar')).not.toBeInTheDocument();
    expect(screen.getByText('All panes active')).toBeInTheDocument();
  });

  it('prevents outer scrolling on dashboard pane so bottom docks remain fixed', () => {
    renderLayout(props);
    const dashboardPane = screen.getByLabelText('Dashboard pane');
    const contentWrapper = dashboardPane.querySelector('div[style*="overflow: hidden"]');
    expect(contentWrapper).toBeInTheDocument();
  });

  it('minimizes into the dock and restores without changing the pane slot', () => {
    renderLayout(props);
    fireEvent.click(screen.getByRole('button', { name: 'Minimize Watchlist' }));

    expect(screen.queryByLabelText('Watchlist pane')).not.toBeInTheDocument();
    const restore = screen.getByTitle('Restore Watchlist');
    expect(restore).toBeInTheDocument();
    fireEvent.click(restore);

    expect(screen.getByLabelText('Watchlist pane')).toBeInTheDocument();
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.slots.left).toBe('watchlist');
    expect(saved.minimized).toEqual([]);
  });

  it('restores every minimized pane in one click while keeping individual restore controls', () => {
    renderLayout(props);

    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      fireEvent.click(screen.getByRole('button', { name: `Minimize ${title}` }));
    }

    expect(screen.getByRole('button', { name: 'Restore all panes' })).toBeInTheDocument();
    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      expect(screen.getByTitle(`Restore ${title}`)).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole('button', { name: 'Restore all panes' }));

    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
    expect(screen.getByText('All panes active')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Restore all panes' })).not.toBeInTheDocument();
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('normal');
    expect(JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!).minimized).toEqual([]);
  });

  it('supports half, maximize, fullscreen, restore, and Escape focus flows', () => {
    renderLayout(props);

    fireEvent.click(screen.getByRole('button', { name: 'Half screen Dashboard' }));
    expect(screen.getByRole('button', { name: 'Restore Dashboard' })).toBeInTheDocument();
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);

    fireEvent.click(screen.getByRole('button', { name: 'Maximize Dashboard' }));
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(1);
    expect(screen.getByLabelText('Dashboard pane')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Full screen Dashboard' }));
    expect(screen.getByLabelText('Dashboard pane').parentElement).toHaveStyle({ position: 'fixed' });

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
  });

  it('repositions by swapping panes through visible dock targets', () => {
    renderLayout(props);
    const pane = screen.getByLabelText('Signals pane');
    const handle = within(pane).getByTitle(/Drag to reposition/);
    const values: Record<string, string> = {};
    const dataTransfer = {
      effectAllowed: 'none',
      dropEffect: 'none',
      setData: (type: string, value: string) => { values[type] = value; },
      getData: (type: string) => values[type] ?? '',
    };

    fireEvent.dragStart(handle, { dataTransfer });
    expect(screen.getByLabelText('Pane drop targets')).toBeInTheDocument();
    fireEvent.drop(screen.getByRole('button', { name: 'Left dock' }), { dataTransfer });

    const saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.slots.left).toBe('signals');
    expect(saved.slots.right).toBe('watchlist');
    expect(screen.queryByLabelText('Pane drop targets')).not.toBeInTheDocument();
  });

  it('consolidates presets, reset, restore-all, and locking in one menu', () => {
    renderLayout(props);
    fireEvent.click(screen.getByRole('button', { name: 'Minimize Terminal' }));
    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));

    expect(screen.getByRole('dialog', { name: 'Workspace layout menu' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Classic/ }));
    let saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.sizes).toEqual({ left: 420, right: 1290, bottom: 220 });
    expect(saved.minimized).toEqual([]);
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('normal');
    expect(screen.getByLabelText('Terminal pane')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));
    fireEvent.click(screen.getByRole('button', { name: /Chart focus/ }));
    saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.sizes).toEqual({ left: 280, right: 720, bottom: 220 });
    expect(saved.slots).toEqual({ left: 'terminal', center: 'dashboard', right: 'signals', bottom: 'watchlist' });
    expect(saved.minimized).toEqual(['terminal']);
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('minimized');
    expect(screen.getByLabelText('Dashboard pane').parentElement).toHaveAttribute('data-workspace-slot', 'center');
    expect(screen.getByLabelText('Watchlist pane').parentElement).toHaveAttribute('data-workspace-slot', 'bottom');
    expect(screen.getByLabelText('Signals pane').parentElement).toHaveAttribute('data-workspace-slot', 'right');
    expect(screen.queryByLabelText('Terminal pane')).not.toBeInTheDocument();
    expect(screen.getByTitle('Restore Terminal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lock pane movement' }));
    saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.locked).toBe(true);
    expect(within(screen.getByLabelText('Watchlist pane')).getByTitle('Layout locked')).toBeInTheDocument();
  });

  it('places Layout immediately before the live engine status group', () => {
    renderLayout(props);

    const status = screen.getByLabelText('Workspace and engine status');
    const layoutButton = within(status).getByRole('button', { name: 'Layout' });
    const live = within(status).getByText('27 live');

    expect(status.firstElementChild).toContainElement(layoutButton);
    expect(layoutButton.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the legacy terminal event contract for chart-open auto minimization', () => {
    renderLayout(props);

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'minimized' })));
    expect(screen.queryByLabelText('Terminal pane')).not.toBeInTheDocument();
    expect(screen.getByTitle('Restore Terminal')).toBeInTheDocument();

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'full' })));
    expect(screen.getByLabelText('Terminal pane').parentElement).toHaveStyle({ position: 'fixed' });

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'normal' })));
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
  });

});

describe('EngineTerminal workspace synchronization', () => {
  it('re-reads normal mode after a minimized terminal is restored while unmounted', () => {
    const first = render(<EngineTerminal />);
    fireEvent.click(screen.getByTitle('Minimize terminal'));
    first.unmount();

    localStorage.setItem(TERMINAL_MODE_KEY, 'normal');
    render(<EngineTerminal />);

    expect(screen.getByText(/Waiting for background scan/)).toBeInTheDocument();
    expect(screen.getByTitle('Minimize terminal')).toBeInTheDocument();
  });
});
