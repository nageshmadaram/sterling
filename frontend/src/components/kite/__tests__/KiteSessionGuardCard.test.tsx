import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

let status: Record<string, unknown> = {};

vi.mock('../../../hooks/useKite', () => ({
  useKiteStatus: () => ({ data: status }),
  useKiteAuthBroadcast: () => {},
  useGenerateKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useOpenKiteLogin: () => ({ open: vi.fn(), opening: false, phase: 'idle', error: null, dismiss: vi.fn() }),
  useRefreshKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { KiteSessionGuard } from '../KiteSessionGuard';
import { useKiteStatusCardStore } from '../../../store/useKiteStatusCardStore';

function renderGuard(onOpenAccountSettings = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KiteSessionGuard onOpenAccountSettings={onOpenAccountSettings} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  useKiteStatusCardStore.setState({ isOpen: false });
  status = { connected: false, account_id: 'a1', user_name: 'TestUser' };
});

describe('KiteSessionGuard floating card popover', () => {
  it('auto-opens when Kite is offline on cold load', () => {
    renderGuard();
    expect(screen.getByText('Zerodha Kite Offline')).toBeInTheDocument();
    expect(screen.getByText('Session off')).toBeInTheDocument();
    expect(screen.getByText('Workspace')).toBeInTheDocument();
    expect(screen.getByText('Market off')).toBeInTheDocument();
  });

  it('can be toggled open via useKiteStatusCardStore when connected', () => {
    status = { connected: true, user_name: 'Madaram', account_id: 'a1' };
    renderGuard();
    expect(screen.queryByText('Zerodha Kite Active')).toBeNull();

    act(() => {
      useKiteStatusCardStore.getState().openCard();
    });

    expect(screen.getByText('Zerodha Kite Active')).toBeInTheDocument();
    expect(screen.getByText(/Signed in as Madaram/i)).toBeInTheDocument();
  });

  it('invokes onOpenAccountSettings and closes card when Account Settings link is clicked', () => {
    const onOpenAccountSettings = vi.fn();
    renderGuard(onOpenAccountSettings);

    const link = screen.getByText(/Account & API Settings/i);
    fireEvent.click(link);

    expect(onOpenAccountSettings).toHaveBeenCalledTimes(1);
    expect(useKiteStatusCardStore.getState().isOpen).toBe(false);
  });
});
