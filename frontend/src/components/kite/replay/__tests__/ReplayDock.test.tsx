import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_STATUS, useReplayStore } from '../../../../hooks/useReplayStore';
import { ReplayDock } from '../ReplayDock';
import { ReplayFooterChip } from '../ReplayFooterChip';
import { resetReplayToastBus } from '../replayToastBus';
import { FULL_CAPS, makeSignal, makeStatus, makeTrade, setupDock, stubFetch, stubResizeObserver } from './testUtils';

/**
 * A real QueryClient rather than a mocked module: the session picker calls
 * `useQuery` for `/available-dates`, and a stubbed `useQueryClient` alone
 * leaves that hook without a provider.
 */
function withQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

async function renderDock() {
  await act(async () => {
    render(withQuery(<ReplayDock />));
  });
}

beforeEach(() => {
  localStorage.clear();
  resetReplayToastBus();
  stubResizeObserver();
  stubFetch();
  setupDock();
});

/* ── Mounting and modes ─────────────────────────────────────────────────── */

describe('mounting', () => {
  it('renders nothing but its overlays while closed', async () => {
    setupDock({ open: false });
    await renderDock();
    expect(screen.queryByTestId('replay-dock')).toBeNull();
  });

  it('renders the deck when open', async () => {
    await renderDock();
    const dock = screen.getByTestId('replay-dock');
    expect(dock).toHaveAttribute('data-mode', 'docked');
    expect(within(dock).getByTestId('replay-transport')).toBeTruthy();
    expect(within(dock).getByTestId('replay-timeline')).toBeTruthy();
    expect(within(dock).getByTestId('replay-metrics')).toBeTruthy();
    expect(within(dock).getByTestId('replay-player-bar-info')).toBeTruthy();
  });

  it('renders the session clock and progress percentage in the player bar', async () => {
    setupDock({
      status: makeStatus({
        state: 'running',
        current_time_iso: '2026-09-08T09:16:19.000Z',
        progress_pct: 0,
      }),
    });
    await renderDock();
    const info = screen.getByTestId('replay-player-bar-info');
    expect(within(info).getByText(/IST/)).toBeTruthy();
    expect(within(info).getByText('0%')).toBeTruthy();
  });

  it.each(['docked', 'expanded', 'overlay', 'fullscreen'] as const)('renders in %s mode', async (mode) => {
    setupDock({ mode });
    await renderDock();
    expect(screen.getByTestId('replay-dock')).toHaveAttribute('data-mode', mode);
  });

  it('keeps the metric strip visible in every tab', async () => {
    // It used to live inside two of four tabs, so P&L vanished on the others.
    for (const tab of ['split', 'signals', 'trades'] as const) {
      setupDock({ tab });
      const { unmount } = render(withQuery(<ReplayDock />));
      expect(screen.getByTestId('replay-metrics')).toBeTruthy();
      unmount();
    }
  });

  it('places player bar and session row outside the scrollable body to remain fixed at bottom', async () => {
    await renderDock();
    const dock = screen.getByTestId('replay-dock');
    const playerBar = screen.getByTestId('replay-player-bar');
    const sessionRow = screen.getByTestId('replay-session-row');
    const scrollBody = dock.querySelector('.rd-scroll-content');

    expect(scrollBody).toBeTruthy();
    expect(scrollBody?.contains(playerBar)).toBe(false);
    expect(scrollBody?.contains(sessionRow)).toBe(false);
    expect(dock.contains(playerBar)).toBe(true);
    expect(dock.contains(sessionRow)).toBe(true);
  });

  it('clamps dock geometry to prevent forcing outer container overflow', async () => {
    await renderDock();
    const dock = screen.getByTestId('replay-dock');
    expect(dock.style.maxHeight).toBe('100%');
    expect(dock.style.flexShrink).toBe('1');
    expect(dock.style.minHeight).toBe('0');
  });
});

/* ── Resizer ────────────────────────────────────────────────────────────── */

describe('resizer', () => {
  it('is a keyboard-operable separator, not a bare div', async () => {
    await renderDock();
    const r = screen.getByTestId('replay-resizer');
    expect(r).toHaveAttribute('role', 'separator');
    expect(r).toHaveAttribute('aria-valuenow', '320');
    expect(r.getAttribute('tabindex')).toBe('0');
  });

  it('resizes with the arrow keys, and further with shift', async () => {
    await renderDock();
    const r = screen.getByTestId('replay-resizer');
    fireEvent.keyDown(r, { key: 'ArrowUp' });
    expect(useReplayStore.getState().height).toBe(336);
    fireEvent.keyDown(r, { key: 'ArrowUp', shiftKey: true });
    expect(useReplayStore.getState().height).toBe(400);
    fireEvent.keyDown(r, { key: 'ArrowDown' });
    expect(useReplayStore.getState().height).toBe(384);
  });

  it('clamps at the minimum usable height', async () => {
    setupDock({ height: 224 });
    await renderDock();
    const r = screen.getByTestId('replay-resizer');
    fireEvent.keyDown(r, { key: 'ArrowDown' });
    fireEvent.keyDown(r, { key: 'ArrowDown' });
    expect(useReplayStore.getState().height).toBe(220);
  });

  it('is absent in the modes that cannot be resized', async () => {
    setupDock({ mode: 'fullscreen' });
    await renderDock();
    expect(screen.queryByTestId('replay-resizer')).toBeNull();
  });

  it('jumps to min or max height on Home and End', async () => {
    await renderDock();
    const r = screen.getByTestId('replay-resizer');
    fireEvent.keyDown(r, { key: 'End' });
    expect(useReplayStore.getState().height).toBeGreaterThan(500);
    fireEvent.keyDown(r, { key: 'Home' });
    expect(useReplayStore.getState().height).toBe(220);
  });
});

/* ── Transport ──────────────────────────────────────────────────────────── */

describe('transport', () => {
  it('offers play while idle and pause while running', async () => {
    await renderDock();
    expect(screen.getByTestId('replay-primary')).toHaveAttribute('aria-label', 'Start replay (Space)');

    await act(async () => {
      useReplayStore.getState().setStatus(makeStatus({ state: 'running' }));
    });
    expect(screen.getByTestId('replay-primary')).toHaveAttribute('aria-label', 'Pause replay (Space)');
  });

  it('disables the seek controls while idle', async () => {
    await renderDock();
    expect(screen.getByLabelText('Jump to session start (Home)')).toBeDisabled();
  });

  it('enables them once a session is loaded', async () => {
    setupDock({ status: makeStatus({ state: 'paused' }) });
    await renderDock();
    expect(screen.getByLabelText('Jump to session start (Home)')).not.toBeDisabled();
  });

  it('renders every speed on the ladder and marks the active one', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-speed-trigger');
    expect(within(trigger).getByText('5×')).toBeTruthy();

    await act(async () => {
      fireEvent.click(trigger);
    });
    ['1×', '5×', '10×', '25×', '50×', '100×', 'MAX'].forEach((label) => {
      expect(screen.getByRole('option', { name: new RegExp(`^(?:✓\\s*)?${label}$`) })).toBeTruthy();
    });
    expect(screen.getByRole('option', { name: /^(?:✓\s*)?5×$/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('updates the active speed immediately when a speed option is clicked while running', async () => {
    setupDock({
      status: makeStatus({ state: 'running', config: { speed: 5, date: '2026-09-07' } as any }),
    });
    await renderDock();
    const trigger = screen.getByTestId('replay-speed-trigger');
    expect(within(trigger).getByText('5×')).toBeTruthy();

    await act(async () => {
      fireEvent.click(trigger);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: /^(?:✓\s*)?10×$/ }));
    });
    expect(within(trigger).getByText('10×')).toBeTruthy();
    expect(useReplayStore.getState().status.config?.speed).toBe(10);
  });
});

/* ── Timeline ───────────────────────────────────────────────────────────── */

describe('timeline', () => {
  it('is a slider that reports its position in words', async () => {
    setupDock({
      status: makeStatus({ state: 'running', progress_pct: 42, current_time_iso: '11:30:00', bars_played: 120, bars_total: 300 }),
    });
    await renderDock();
    const t = screen.getByTestId('replay-timeline');
    expect(t).toHaveAttribute('role', 'slider');
    expect(t).toHaveAttribute('aria-valuenow', '42');
    expect(t.getAttribute('aria-valuetext')).toContain('11:30:00');
    expect(t.getAttribute('aria-valuetext')).toContain('120 of 300');
  });

  it('is inert while idle', async () => {
    await renderDock();
    const t = screen.getByTestId('replay-timeline');
    expect(t).toHaveAttribute('aria-disabled', 'true');
    expect(t.getAttribute('tabindex')).toBe('-1');
    expect(t.getAttribute('aria-valuetext')).toContain('09:00:00 IST');
  });

  it('seeks once per drag, not once per pointer move', async () => {
    // A request per pointermove would be hundreds of round trips per drag.
    const { fetchSpy } = setupDock({ status: makeStatus({ state: 'running', bars_total: 300 }) });
    await renderDock();
    const t = screen.getByTestId('replay-timeline');
    (t as HTMLElement).setPointerCapture = vi.fn();
    (t as HTMLElement).releasePointerCapture = vi.fn();
    t.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 24, right: 1000, bottom: 24, x: 0, y: 0, toJSON: () => ({}) });

    fetchSpy.mockClear();
    await act(async () => {
      fireEvent.pointerDown(t, { clientX: 100, pointerId: 1 });
      fireEvent.pointerMove(t, { clientX: 300, pointerId: 1 });
      fireEvent.pointerMove(t, { clientX: 500, pointerId: 1 });
      fireEvent.pointerUp(t, { clientX: 500, pointerId: 1 });
    });

    const seeks = fetchSpy.mock.calls.filter(([url]) => String(url).includes('/seek'));
    expect(seeks).toHaveLength(1);
  });

  it('clusters dots so a busy session is not one solid bar', async () => {
    const events = Array.from({ length: 200 }, (_, i) =>
      makeSignal({ time_iso: `1${i % 5}:0${i % 6}:00`, instrument: `SYM${i}` }),
    );
    setupDock({ status: makeStatus({ state: 'running', stats: { ...DEFAULT_STATUS.stats, events } }) });
    await renderDock();
    const dots = screen.getByTestId('replay-timeline').querySelectorAll('.rd-dot');
    expect(dots.length).toBeGreaterThan(0);
    expect(dots.length).toBeLessThan(events.length);
  });

  it('warns rather than draws a wrong picture for a multi-day range', async () => {
    setupDock({
      status: makeStatus({
        state: 'running',
        config: { date: '2026-09-03', end_date: '2026-09-05', start_time: '09:00:00', end_time: '15:30:00', speed: 5, resolution: '5m', instruments: [] },
      }),
    });
    await renderDock();
    expect(screen.getByText(/Multi-day range/)).toBeTruthy();
  });
});

/* ── Honesty ────────────────────────────────────────────────────────────── */

describe('unmeasured values', () => {
  it('shows an em dash for slippage when friction was not modelled', async () => {
    // It used to print ₹0.00, which reads as "measured, and it was free".
    setupDock({
      status: makeStatus({
        stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ slippage: undefined })], slippage_total: null },
      }),
    });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    const slip = within(strip).getByText('Slippage').parentElement!;
    expect(within(slip).getByText('—')).toBeTruthy();
    expect(within(slip).queryByText(/₹0\.00/)).toBeNull();
  });

  it('shows a real figure once friction was modelled', async () => {
    setupDock({
      status: makeStatus({
        stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ slippage: 12.5 })], slippage_total: 12.5 },
      }),
    });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    expect(within(strip).getByText('−₹12.50')).toBeTruthy();
  });

  it('hides the trades slippage column entirely when nothing measured it', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ slippage: undefined })] } }),
    });
    await renderDock();
    expect(screen.queryByRole('columnheader', { name: 'Slippage' })).toBeNull();
    expect(screen.getByText(/Execution friction is not modelled/)).toBeTruthy();
  });

  it('shows the column and drops the note when it was measured', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ slippage: 12.5, raw_entry: 98, raw_exit: 122 })] } }),
    });
    await renderDock();
    expect(screen.getByRole('columnheader', { name: 'Slippage' })).toBeTruthy();
    expect(screen.queryByText(/Execution friction is not modelled/)).toBeNull();
  });

  it('labels the contract column by what the engine can actually send', async () => {
    setupDock({
      tab: 'signals',
      status: makeStatus({
        capabilities: { ...FULL_CAPS, contract_on_signal: false },
        stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()] },
      }),
    });
    await renderDock();
    expect(screen.getByRole('columnheader', { name: 'Underlying' })).toBeTruthy();
    expect(screen.queryByRole('columnheader', { name: 'Contract' })).toBeNull();
  });
});

/* ── Tables ─────────────────────────────────────────────────────────────── */

describe('signals table', () => {
  it('computes reward to risk, and declines to when the stop is the entry', async () => {
    setupDock({
      tab: 'signals',
      status: makeStatus({
        state: 'running',
        stats: {
          ...DEFAULT_STATUS.stats,
          events: [
            makeSignal({ entry: 100, stop: 90, target: 130 }),
            makeSignal({ time_iso: '10:50:00', entry: 100, stop: 100, target: 130 }),
          ],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('3.0×')).toBeTruthy();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('tells the truth about why it is empty', async () => {
    setupDock({ tab: 'signals' });
    await renderDock();
    // The old copy said "Replay stepping through bars..." even while idle.
    expect(screen.getByText('No replay loaded')).toBeTruthy();

    await act(async () => {
      useReplayStore.getState().setStatus(makeStatus({ state: 'running', bars_played: 47 }));
    });
    expect(screen.getByText('Watching for signals')).toBeTruthy();
    expect(screen.getByText(/47 bars replayed/)).toBeTruthy();
  });

  it('groups signals by date by default in multi-day range', async () => {
    setupDock({
      tab: 'signals',
      status: makeStatus({
        config: {
          date: '2026-08-03',
          end_date: '2026-08-05',
          start_time: '09:00:00',
          end_time: '15:30:00',
          speed: 5,
          resolution: '5m',
          instruments: [],
        },
        stats: {
          ...DEFAULT_STATUS.stats,
          events: [
            makeSignal({ time_iso: '2026-08-03T10:47:05', timestamp_ms: Date.UTC(2026, 7, 3, 5, 17, 5) }),
            makeSignal({ time_iso: '2026-08-04T11:15:00', timestamp_ms: Date.UTC(2026, 7, 4, 5, 45, 0) }),
          ],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('Mon 3 Aug 2026')).toBeTruthy();
    expect(screen.getByText('Tue 4 Aug 2026')).toBeTruthy();
  });

  it('formats contract names using InstrumentLabel', async () => {
    setupDock({
      tab: 'signals',
      status: makeStatus({
        state: 'running',
        stats: {
          ...DEFAULT_STATUS.stats,
          events: [
            makeSignal({ contract: 'NIFTY26AUG24500CE', spot: 24510 }),
          ],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('AUG')).toBeTruthy();
    expect(screen.getByText('24500')).toBeTruthy();
    expect(screen.getByText('CE')).toBeTruthy();
  });
});

describe('trades table', () => {
  it('marks an unrealised P&L so it is not read as booked', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ status: 'OPEN', exit_price: null })] } }),
    });
    await renderDock();
    expect(screen.getByText(/~[+]1,000/)).toBeTruthy();
  });

  it('says whether the total is net of friction', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade()], pnl: 1000 } }),
    });
    await renderDock();
    expect(screen.getByText('no friction modelled')).toBeTruthy();
  });

  it('groups trades by date by default in multi-day range', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        config: {
          date: '2026-08-03',
          end_date: '2026-08-05',
          start_time: '09:00:00',
          end_time: '15:30:00',
          speed: 5,
          resolution: '5m',
          instruments: [],
        },
        stats: {
          ...DEFAULT_STATUS.stats,
          trades: [
            makeTrade({
              trade_id: 'TRD-1',
              entry_time_iso: '2026-08-03T10:47:05',
              timestamp_ms: Date.UTC(2026, 7, 3, 5, 17, 5),
              pnl_usd: 1500,
            }),
            makeTrade({
              trade_id: 'TRD-2',
              entry_time_iso: '2026-08-04T11:15:00',
              timestamp_ms: Date.UTC(2026, 7, 4, 5, 45, 0),
              pnl_usd: -500,
            }),
          ],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('Mon 3 Aug 2026')).toBeTruthy();
    expect(screen.getByText('Tue 4 Aug 2026')).toBeTruthy();
    expect(screen.getAllByText('+₹1,500.00').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('−₹500.00').length).toBeGreaterThanOrEqual(1);
  });

  it('displays each days pnl stats on grouped day rows', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        config: {
          date: '2026-08-03',
          end_date: '2026-08-05',
          start_time: '09:00:00',
          end_time: '15:30:00',
          speed: 5,
          resolution: '5m',
          instruments: [],
        },
        stats: {
          ...DEFAULT_STATUS.stats,
          trades: [
            makeTrade({
              trade_id: 'TRD-1',
              status: 'WIN',
              entry_time_iso: '2026-08-03T10:00:00',
              timestamp_ms: Date.UTC(2026, 7, 3, 4, 30, 0),
              pnl_usd: 2000,
            }),
            makeTrade({
              trade_id: 'TRD-2',
              status: 'LOSS',
              entry_time_iso: '2026-08-03T12:00:00',
              timestamp_ms: Date.UTC(2026, 7, 3, 6, 30, 0),
              pnl_usd: -500,
            }),
          ],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('Mon 3 Aug 2026')).toBeTruthy();
    expect(screen.getByText('50%')).toBeTruthy();
    expect(screen.getByText('4.00')).toBeTruthy();
    expect(screen.getAllByText('+₹750.00').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('(+1,500)').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('10,000').length).toBeGreaterThanOrEqual(1);
  });

  it('toggles between Invested (P&L) and standard P&L view', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ entry_price: 100, quantity: 50, pnl_usd: 1000 })] } }),
    });
    await renderDock();
    // Initially shows invested amount and bracketed P&L: 5,000 (+1,000) in row and footer
    expect(screen.getAllByText('5,000').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('(+1,000)').length).toBeGreaterThanOrEqual(1);

    // Toggle view via toolbar button
    const toggleBtn = screen.getByTestId('replay-toggle-invested');
    fireEvent.click(toggleBtn);

    // Now shows standard P&L
    expect(screen.getAllByText('+₹1,000.00').length).toBeGreaterThanOrEqual(1);
  });

  it('consolidates table investment across trades, groups, and footer', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        config: {
          date: '2026-08-03',
          end_date: '2026-08-05',
          start_time: '09:00:00',
          end_time: '15:30:00',
          speed: 5,
          resolution: '5m',
          instruments: [],
        },
        stats: {
          ...DEFAULT_STATUS.stats,
          trades: [
            makeTrade({
              trade_id: 'TRD-1',
              entry_time_iso: '2026-08-03T10:00:00',
              timestamp_ms: Date.UTC(2026, 7, 3, 4, 30, 0),
              entry_price: 120,
              quantity: 100,
              pnl_usd: 125,
            }),
            makeTrade({
              trade_id: 'TRD-2',
              entry_time_iso: '2026-08-03T12:00:00',
              timestamp_ms: Date.UTC(2026, 7, 3, 6, 30, 0),
              entry_price: 80,
              quantity: 50,
              pnl_usd: -25,
            }),
          ],
        },
      }),
    });
    await renderDock();

    // Row 1: 120 * 100 = 12,000 (+125)
    expect(screen.getByText('12,000')).toBeTruthy();
    expect(screen.getByText('(+125)')).toBeTruthy();

    // Row 2: 80 * 50 = 4,000 (−25)
    expect(screen.getByText('4,000')).toBeTruthy();
    expect(screen.getByText('(−25)')).toBeTruthy();

    // Group consolidated investment: 12,000 + 4,000 = 16,000 (+100)
    expect(screen.getAllByText('16,000').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('(+100)').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('₹16,000 invested')).toBeTruthy();
  });

  it('formats trade contract names using InstrumentLabel', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        stats: {
          ...DEFAULT_STATUS.stats,
          trades: [makeTrade({ symbol: 'NIFTY26AUG24500CE' })],
        },
      }),
    });
    await renderDock();
    expect(screen.getByText('AUG')).toBeTruthy();
    expect(screen.getByText('24500')).toBeTruthy();
    expect(screen.getByText('CE')).toBeTruthy();
  });
});

/* ── Configuration ──────────────────────────────────────────────────────── */

describe('configuration', () => {
  it('is reachable while idle and locked while running', async () => {
    await renderDock();
    expect(screen.getByTestId('replay-session-trigger')).not.toBeDisabled();
    expect(screen.getByTestId('replay-hours-trigger')).not.toBeDisabled();
    expect(screen.getByTestId('replay-strategy-trigger')).not.toBeDisabled();
    expect(screen.getByTestId('replay-sizing-trigger')).not.toBeDisabled();
    expect(screen.getByTestId('replay-filters-trigger')).not.toBeDisabled();

    await act(async () => {
      useReplayStore.getState().setStatus(makeStatus({ state: 'running' }));
    });
    expect(screen.getByTestId('replay-session-trigger')).toBeDisabled();
    expect(screen.getByTestId('replay-hours-trigger')).toBeDisabled();
    expect(screen.getByTestId('replay-strategy-trigger')).toBeDisabled();
    expect(screen.getByTestId('replay-sizing-trigger')).toBeDisabled();
    expect(screen.getByTestId('replay-filters-trigger')).toBeDisabled();
  });

  it('opens as a popover dialog and closes on Escape, returning focus', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-filters-trigger');
    trigger.focus();
    await act(async () => { fireEvent.click(trigger); });
    const pop = screen.getByRole('dialog', { name: 'Execution and engine settings' });
    expect(pop).toBeInTheDocument();

    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
    expect(screen.queryByRole('dialog', { name: 'Execution and engine settings' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('says plainly when the engine cannot model friction', async () => {
    setupDock({ status: makeStatus({ capabilities: { ...FULL_CAPS, friction: false } }) });
    await renderDock();
    await act(async () => { fireEvent.click(screen.getByTestId('replay-filters-trigger')); });
    expect(screen.getByText(/Friction and spread modelling is not supported/)).toBeTruthy();
    expect(screen.queryByLabelText('Index spread %')).toBeNull();
  });

  it('offers the real parameters when it can', async () => {
    await renderDock();
    await act(async () => { fireEvent.click(screen.getByTestId('replay-filters-trigger')); });
    expect(screen.getByLabelText('Index spread %')).toBeTruthy();
    expect(screen.getByLabelText('Slippage % (each leg)')).toBeTruthy();
  });

  it('does not close the filter popover when scrolling inside the popover', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-filters-trigger');
    await act(async () => {
      fireEvent.click(trigger);
    });
    const dialog = screen.getByRole('dialog', { name: 'Execution and engine settings' });
    expect(dialog).toBeTruthy();

    await act(async () => {
      const scrollable = dialog.querySelector('div') || dialog;
      fireEvent.scroll(scrollable);
    });
    expect(screen.getByRole('dialog', { name: 'Execution and engine settings' })).toBeTruthy();
  });

  it('allows selecting session presets', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-session-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const options = screen.getAllByRole('option');
    expect(options.length).toBeGreaterThan(0);
    await act(async () => { fireEvent.click(options[0]); });
  });

  it('allows selecting market hours preset', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-hours-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const preopen = screen.getByRole('option', { name: /Pre-open/ });
    await act(async () => { fireEvent.click(preopen); });
    expect(useReplayStore.getState().draft.startTime).toBe('09:00:00');
  });

  it('allows configuring replay strategies', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-strategy-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const orbLabel = screen.getByText('NIFTY ORB');
    await act(async () => { fireEvent.click(orbLabel); });
    expect(useReplayStore.getState().draft.strategies).toContain('nifty_orb');
  });

  it('allows configuring position sizing and moneyness', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-sizing-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const lot5 = screen.getByRole('button', { name: '5L' });
    await act(async () => { fireEvent.click(lot5); });
    expect(useReplayStore.getState().draft.lots).toBe(5);
  });

  it('indicates invalid hours when start time is after end time', async () => {
    useReplayStore.getState().setDraft({ startTime: '15:30:00', endTime: '09:15:00' });
    await renderDock();
    const trigger = screen.getByTestId('replay-hours-trigger');
    expect(trigger).toHaveAttribute('title', 'Start time must precede end time');
  });

  it('rejects starting replay when start time is after end time', async () => {
    useReplayStore.getState().setDraft({ startTime: '15:30:00', endTime: '09:15:00' });
    await renderDock();
    const playBtn = screen.getByTestId('replay-primary');
    await act(async () => { fireEvent.click(playBtn); });
    expect(useReplayStore.getState().error?.code).toBe('invalid_time_range');
  });

  it('resets date range to single day with quick action', async () => {
    useReplayStore.getState().setDraft({ date: '2026-09-01', endDate: '2026-09-05' });
    await renderDock();
    const trigger = screen.getByTestId('replay-session-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const singleDayBtn = screen.getByTestId('replay-session-single-day');
    expect(singleDayBtn).toBeTruthy();
    await act(async () => { fireEvent.click(singleDayBtn); });
    expect(useReplayStore.getState().draft.endDate).toBe('2026-09-01');
  });

  it('allows picking a single day with only one input field', async () => {
    useReplayStore.getState().setDraft({ date: '2026-09-01', endDate: '2026-09-01' });
    await renderDock();
    const trigger = screen.getByTestId('replay-session-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const singleDateInput = screen.getByTestId('replay-session-date-input');
    expect(singleDateInput).toBeTruthy();
    expect(screen.queryByTestId('replay-session-to-input')).toBeNull();
    await act(async () => {
      fireEvent.change(singleDateInput, { target: { value: '2026-07-15' } });
    });
    expect(useReplayStore.getState().draft.date).toBe('2026-07-15');
    expect(useReplayStore.getState().draft.endDate).toBe('2026-07-15');
  });

  it('allows switching between single day and date range mode', async () => {
    useReplayStore.getState().setDraft({ date: '2026-09-01', endDate: '2026-09-01' });
    await renderDock();
    const trigger = screen.getByTestId('replay-session-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const rangeModeBtn = screen.getByTestId('replay-session-date-range');
    await act(async () => { fireEvent.click(rangeModeBtn); });
    const fromInput = screen.getByTestId('replay-session-from-input');
    const toInput = screen.getByTestId('replay-session-to-input');
    expect(fromInput).toBeTruthy();
    expect(toInput).toBeTruthy();
    await act(async () => {
      fireEvent.change(toInput, { target: { value: '2026-09-10' } });
    });
    expect(useReplayStore.getState().draft.date).toBe('2026-09-01');
    expect(useReplayStore.getState().draft.endDate).toBe('2026-09-10');
  });

  it('supports typing custom lot size', async () => {
    await renderDock();
    const trigger = screen.getByTestId('replay-sizing-trigger');
    await act(async () => { fireEvent.click(trigger); });
    const lotInput = screen.getByTitle('Custom lots');
    await act(async () => {
      fireEvent.change(lotInput, { target: { value: '15' } });
    });
    expect(useReplayStore.getState().draft.lots).toBe(15);
  });

  it('switches between trades and signals tabs', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        state: 'running',
        stats: {
          ...DEFAULT_STATUS.stats,
          events: [makeSignal()],
          trades: [makeTrade()],
        },
      }),
    });
    await renderDock();
    expect(screen.getByTestId('replay-tab-trades')).toHaveAttribute('aria-selected', 'true');
    const signalsTab = screen.getByTestId('replay-tab-signals');
    await act(async () => { fireEvent.click(signalsTab); });
    expect(useReplayStore.getState().tab).toBe('signals');
    expect(screen.getByTestId('replay-tab-signals')).toHaveAttribute('aria-selected', 'true');
    const tradesTab = screen.getByTestId('replay-tab-trades');
    await act(async () => { fireEvent.click(tradesTab); });
    expect(useReplayStore.getState().tab).toBe('trades');
    expect(screen.getByTestId('replay-tab-trades')).toHaveAttribute('aria-selected', 'true');
  });

  it('defaults to trades tab when starting replay', async () => {
    setupDock({
      status: makeStatus({
        state: 'running',
        stats: {
          ...DEFAULT_STATUS.stats,
          events: [makeSignal()],
          trades: [makeTrade()],
        },
      }),
    });
    await renderDock();
    expect(screen.getByTestId('replay-tab-trades')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('replay-tab-signals')).toHaveAttribute('aria-selected', 'false');
  });
});

/* ── Keyboard ───────────────────────────────────────────────────────────── */

describe('keyboard scope', () => {
  it('ignores Space typed into an input outside the dock', async () => {
    // The old handler was bound to `window` whenever the dock was merely open,
    // so it stole Space, the arrows and Home/End from the whole app.
    const { fetchSpy } = setupDock({ status: makeStatus({ state: 'running' }) });
    await renderDock();

    const outside = document.createElement('input');
    document.body.appendChild(outside);
    outside.focus();
    fetchSpy.mockClear();

    await act(async () => { fireEvent.keyDown(outside, { key: ' ' }); });
    expect(fetchSpy.mock.calls.filter(([u]) => String(u).includes('/pause'))).toHaveLength(0);
    outside.remove();
  });

  it('acts when focus is inside the dock', async () => {
    const { fetchSpy } = setupDock({ status: makeStatus({ state: 'running' }) });
    await renderDock();
    screen.getByTestId('replay-dock').focus();
    fetchSpy.mockClear();

    await act(async () => { fireEvent.keyDown(document, { key: ' ' }); });
    expect(fetchSpy.mock.calls.some(([u]) => String(u).includes('/pause'))).toBe(true);
  });

  it('switches tabs with D, S and T', async () => {
    await renderDock();
    screen.getByTestId('replay-dock').focus();
    fireEvent.keyDown(document, { key: 's' });
    expect(useReplayStore.getState().tab).toBe('signals');
    fireEvent.keyDown(document, { key: 't' });
    expect(useReplayStore.getState().tab).toBe('trades');
    fireEvent.keyDown(document, { key: 'd' });
    expect(useReplayStore.getState().tab).toBe('trades');
  });

  it('opens the shortcut sheet on ?', async () => {
    await renderDock();
    screen.getByTestId('replay-dock').focus();
    await act(async () => { fireEvent.keyDown(document, { key: '?' }); });
    expect(screen.getByTestId('replay-shortcuts')).toBeTruthy();
  });

  it('toggles the dock from anywhere with the one global binding', async () => {
    await renderDock();
    const outside = document.createElement('input');
    document.body.appendChild(outside);
    outside.focus();
    await act(async () => { fireEvent.keyDown(document, { key: 'R', ctrlKey: true, shiftKey: true }); });
    expect(useReplayStore.getState().open).toBe(false);
    outside.remove();
  });

  it('triggers export on E without crashing', async () => {
    await renderDock();
    screen.getByTestId('replay-dock').focus();
    fireEvent.keyDown(document, { key: 'e' });
  });
});

/* ── Footer ─────────────────────────────────────────────────────────────── */

describe('footer chip', () => {
  it('toggles the dock and never mutates replay state', async () => {
    const { fetchSpy } = setupDock();
    render(withQuery(<ReplayFooterChip />));
    const chip = screen.getByTestId('replay-footer-chip');
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    fetchSpy.mockClear();

    await act(async () => { fireEvent.click(chip); });
    expect(useReplayStore.getState().open).toBe(false);
    expect(fetchSpy.mock.calls.filter(([u]) => /\/(start|stop|pause|resume)/.test(String(u)))).toHaveLength(0);
  });

  it('shows the replay clock while running', async () => {
    setupDock({ status: makeStatus({ state: 'running', current_time_iso: '10:47:05' }) });
    render(withQuery(<ReplayFooterChip />));
    expect(screen.getByText('10:47:05')).toBeTruthy();
  });
});

/* ── Table structure ────────────────────────────────────────────────────── */

describe('trades totals row', () => {
  // Caught in the browser: the blank span was two cells wide when the friction
  // column was present, so every footer cell after Size sat under the wrong
  // header. A totals row that does not line up with its columns is worse than
  // no totals row.
  it.each([
    ['without friction', undefined],
    ['with friction', 12.5],
  ])('spans exactly the header width %s', async (_label, slippage) => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ slippage: slippage as number | undefined })] },
      }),
    });
    await renderDock();

    const table = screen.getByRole('table');
    const headerCells = table.querySelectorAll('thead th').length;
    const footCells = Array.from(table.querySelectorAll('tfoot td'))
      .reduce((sum, td) => sum + (Number(td.getAttribute('colspan')) || 1), 0);
    expect(footCells).toBe(headerCells);
  });
});

/* ── A finished session must not look like a live one ───────────────────── */

describe('idle with results', () => {
  // Reported from the running app: the dock showed a full signal feed and
  // trade list before the user had pressed play. The runner keeps the last
  // session's ledger for review; the dock has to say so.
  it('labels a finished session and offers to clear it', async () => {
    setupDock({
      status: makeStatus({
        state: 'idle',
        session_complete: true,
        config: { date: '2026-09-04', start_time: '09:00:00', end_time: '15:30:00', speed: 5, resolution: '5m', instruments: [] },
        stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()], trades: [makeTrade()] },
      }),
    });
    await renderDock();
    const note = screen.getByTestId('replay-historical-note');
    expect(note.textContent).toContain('finished');
    expect(note.textContent).toContain('Nothing is replaying now');
    expect(within(note).getByRole('button', { name: 'Clear results' })).toBeTruthy();
  });

  it('says nothing when the runner is genuinely empty', async () => {
    setupDock();
    await renderDock();
    expect(screen.queryByTestId('replay-historical-note')).toBeNull();
  });

  it('says nothing while a replay is actually running', async () => {
    setupDock({
      status: makeStatus({
        state: 'running',
        stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()], trades: [makeTrade()] },
      }),
    });
    await renderDock();
    expect(screen.queryByTestId('replay-historical-note')).toBeNull();
  });

  it('clearing empties the ledger and calls the runner', async () => {
    const { fetchSpy } = setupDock({
      status: makeStatus({
        state: 'idle',
        session_complete: true,
        stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()], trades: [makeTrade()] },
      }),
    });
    await renderDock();
    fetchSpy.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear results' }));
    });
    expect(fetchSpy.mock.calls.some(([u]) => String(u).includes('/clear'))).toBe(true);
  });
});

describe('unrealised is kept apart from realised', () => {
  it('shows an em dash when nothing is open', async () => {
    setupDock({ status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade({ status: 'WIN' })] } }) });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    const cell = within(strip).getByText('Open').parentElement!;
    expect(within(cell).getByText('—')).toBeTruthy();
  });

  it('reports the mark-to-market when a position is open', async () => {
    setupDock({
      status: makeStatus({
        open_positions: 1,
        unrealised_pnl: 340,
        stats: { ...DEFAULT_STATUS.stats, pnl: 0, trades: [makeTrade({ status: 'OPEN', pnl_usd: 340 })] },
      }),
    });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    expect(within(strip).getByText('+₹340.00')).toBeTruthy();
    // And the realised figure must NOT have absorbed it.
    const realised = within(strip).getByText('P&L').parentElement!;
    expect(within(realised).getByText('₹0.00')).toBeTruthy();
  });

  it('colors P&L as dim when idle with no trades', async () => {
    setupDock({ status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, pnl: 0, trades: [] } }) });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    const pnlMetric = within(strip).getByText('P&L').closest('.rd-metric')!;
    expect(pnlMetric.getAttribute('data-tone')).toBe('dim');
  });

  it('colors P&L as profit when positive', async () => {
    setupDock({
      status: makeStatus({ stats: { ...DEFAULT_STATUS.stats, pnl: 1500, trades: [makeTrade({ pnl_usd: 1500 })] } }),
    });
    await renderDock();
    const strip = screen.getByTestId('replay-metrics');
    const pnlMetric = within(strip).getByText('P&L').closest('.rd-metric')!;
    expect(pnlMetric.getAttribute('data-tone')).toBe('profit');
  });
});

describe('the keyboard survives a mode change', () => {
  // Observed in the browser: Escape stepped fullscreen -> overlay -> docked and
  // then went dead. Changing mode unmounts the focused control, focus falls to
  // <body>, and the shortcut handler's ownership check rejects everything after
  // that — so the dock could not be closed from the keyboard.
  it('walks the whole Escape ladder, ending closed', async () => {
    setupDock({ mode: 'fullscreen', prevMode: 'overlay' });
    await renderDock();
    screen.getByTestId('replay-dock').focus();

    const esc = async () => {
      await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
    };

    await esc();
    expect(useReplayStore.getState().mode).toBe('overlay');
    await esc();
    expect(useReplayStore.getState().mode).toBe('docked');
    await esc();
    expect(useReplayStore.getState().open).toBe(false);
  });

  it('reclaims focus when a mode change drops it to the body', async () => {
    setupDock();
    await renderDock();
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    await act(async () => { useReplayStore.getState().setMode('overlay'); });
    expect(screen.getByTestId('replay-dock').contains(document.activeElement)).toBe(true);
  });

  it('does not steal focus the user put in another pane', async () => {
    setupDock();
    await renderDock();
    const outside = document.createElement('input');
    document.body.appendChild(outside);
    outside.focus();

    await act(async () => { useReplayStore.getState().setMode('overlay'); });
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});

describe('simulation tables scroll container hierarchy', () => {
  it('preserves the flex scroll container hierarchy from rd-scroll-content to rd-pane-body', async () => {
    setupDock({
      tab: 'trades',
      status: makeStatus({
        stats: {
          ...DEFAULT_STATUS.stats,
          trades: [makeTrade({ trade_id: 'TRD-SCROLL-1' })],
        },
      }),
    });
    await renderDock();

    const scrollContent = document.querySelector('.rd-scroll-content');
    expect(scrollContent).toBeTruthy();

    const unifiedTable = scrollContent?.querySelector('.rd-unified-table');
    expect(unifiedTable).toBeTruthy();

    const tabPanel = unifiedTable?.querySelector('.rd-tab-panel');
    expect(tabPanel).toBeTruthy();

    const paneBody = tabPanel?.querySelector('.rd-pane-body');
    expect(paneBody).toBeTruthy();

    const table = paneBody?.querySelector('.rd-table');
    expect(table).toBeTruthy();

    const thead = table?.querySelector('thead');
    expect(thead).toBeTruthy();

    const tfoot = table?.querySelector('tfoot.rd-tfoot');
    expect(tfoot).toBeTruthy();
  });
});

