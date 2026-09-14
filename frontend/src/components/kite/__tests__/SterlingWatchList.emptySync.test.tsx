import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SterlingWatchList } from '../SterlingWatchList';
import * as useKiteModule from '../../../hooks/useKite';

vi.mock('../../../store/useKiteNotifications', () => ({
  notifyOrder: vi.fn(),
}));

describe('SterlingWatchList Empty & Sync', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('renders stable empty state prompt with sync button when watchlist is empty', () => {
    localStorage.setItem('sterling.kite.watchlist.v1', '[]');
    localStorage.setItem('sterling.kite.watchlist.manual-empty.v1', '1');

    vi.spyOn(useKiteModule, 'useKitePositions').mockReturnValue({
      data: { net: [], day: [] },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn().mockResolvedValue({ data: { net: [], day: [] } }),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <SterlingWatchList />
      </QueryClientProvider>
    );

    expect(screen.getByText('Nothing here.')).toBeInTheDocument();
    expect(screen.getByText('Use the search bar to add instruments.')).toBeInTheDocument();

    const syncButtons = screen.getAllByRole('button', { name: /Sync open positions from Kite/i });
    expect(syncButtons.length).toBe(2);
    expect(syncButtons[0]).not.toBeDisabled();
    expect(syncButtons[1]).not.toBeDisabled();
  });

  it('does not flicker button text when positions query is background fetching', () => {
    localStorage.setItem('sterling.kite.watchlist.v1', '[]');
    localStorage.setItem('sterling.kite.watchlist.manual-empty.v1', '1');

    // Spy on useKitePositions to simulate background polling isFetching = true
    vi.spyOn(useKiteModule, 'useKitePositions').mockReturnValue({
      data: { net: [], day: [] },
      isLoading: false,
      isFetching: true, // Simulating 1-second interval background polling
      isError: false,
      refetch: vi.fn().mockResolvedValue({ data: { net: [], day: [] } }),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <SterlingWatchList />
      </QueryClientProvider>
    );

    // Both the header sync button and prompt sync button should remain stable and enabled
    const syncButtons = screen.getAllByRole('button', { name: /Sync open positions from Kite/i });
    expect(syncButtons.length).toBe(2);
    expect(syncButtons[0]).not.toBeDisabled();
    expect(syncButtons[1]).not.toBeDisabled();
    expect(screen.queryByText('Syncing…')).not.toBeInTheDocument();
  });

  it('auto-seeds open positions into watchlist when watchlist is empty and not manually emptied', async () => {
    localStorage.removeItem('sterling.kite.watchlist.v1');
    localStorage.removeItem('sterling.kite.watchlist.manual-empty.v1');

    vi.spyOn(useKiteModule, 'useKitePositions').mockReturnValue({
      data: {
        net: [
          { tradingsymbol: 'NIFTY24DEC24000CE', exchange: 'NFO', quantity: 50, instrument_token: 12345, product: 'NRML' },
        ],
        day: [],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <SterlingWatchList />
      </QueryClientProvider>
    );

    // Should automatically seed the instrument and not display the empty state
    await waitFor(() => {
      expect(screen.queryByText('Nothing here.')).not.toBeInTheDocument();
      expect(screen.getByText('1 / 50')).toBeInTheDocument();
    });
  });

  it('updates reactively when sterling-watchlist-changed event is dispatched from another component', async () => {
    localStorage.setItem('sterling.kite.watchlist.v1', '[]');
    localStorage.setItem('sterling.kite.watchlist.manual-empty.v1', '1');

    vi.spyOn(useKiteModule, 'useKitePositions').mockReturnValue({
      data: { net: [], day: [] },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn().mockResolvedValue({ data: { net: [], day: [] } }),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <SterlingWatchList />
      </QueryClientProvider>
    );

    expect(screen.getByText('Nothing here.')).toBeInTheDocument();

    // Dispatch event from another simulated component/pane
    const newItems = [
      { symbol: 'NSE:INFY', name: 'INFY', exchange: 'NSE', lot_size: 1 },
    ];
    act(() => {
      window.dispatchEvent(new CustomEvent('sterling-watchlist-changed', { detail: newItems }));
    });

    await waitFor(() => {
      expect(screen.queryByText('Nothing here.')).not.toBeInTheDocument();
      expect(screen.getByText('INFY')).toBeInTheDocument();
      expect(screen.getByText('1 / 50')).toBeInTheDocument();
    });
  });
});

