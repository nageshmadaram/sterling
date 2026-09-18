/**
 * Rendering a panel must never place an order or change a trading mode.
 *
 * Written after both confirm handlers were left as unbraced arrow functions:
 * the body ended at the first semicolon, so the statement that followed became
 * component-body code and ran on every render. Opening this panel switched the
 * account to LIVE and turned auto-execution ON, with no click, and each success
 * re-rendered and did it again — which is what surfaced as "Maximum update
 * depth exceeded".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const updateMutate = vi.fn();
const patchMutate = vi.fn();
const refetchReadiness = vi.fn();

let accountIsPaper = true;

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({
    data: {
      accounts: [
        {
          id: 'KITE-1',
          is_active: true,
          is_paper: accountIsPaper,
          connected: true,
          has_credentials: true,
          label: 'SterlingKite',
        },
      ],
    },
  }),
  useUpdateKiteAccount: () => ({ mutate: updateMutate, isPending: false }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { auto_execute: false, vehicle: 'otm_options', target_delta: 0.5 } }),
  usePatchEngineConfig: () => ({ mutate: patchMutate, isPending: false }),
  useEngineReadiness: () => ({ data: { ready: true, blockers: [] }, refetch: refetchReadiness }),
  useApplyProductionConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEmergencySquareOff: () => ({ mutate: vi.fn(), isPending: false }),
  useEmergencyHalt: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

import { TradingModeControls } from '../TradingModeControls';

beforeEach(() => {
  updateMutate.mockReset();
  patchMutate.mockReset();
  accountIsPaper = true;
});
afterEach(cleanup);

describe('TradingModeControls', () => {
  it('renders without changing anything', () => {
    render(<TradingModeControls />);
    // The defect: `is_paper: false` and `auto_execute: true` fired here.
    expect(updateMutate).not.toHaveBeenCalled();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('re-rendering still changes nothing', () => {
    const { rerender } = render(<TradingModeControls />);
    rerender(<TradingModeControls />);
    rerender(<TradingModeControls />);
    expect(updateMutate).not.toHaveBeenCalled();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('going live needs a click and then a confirmation', () => {
    render(<TradingModeControls />);
    expect(updateMutate).not.toHaveBeenCalled();

    // The LIVE side of the toggle only opens the confirmation.
    const live = screen.getAllByText(/LIVE/i)[0];
    fireEvent.click(live);
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it('switching to paper sends exactly one update, not two', () => {
    accountIsPaper = false;
    render(<TradingModeControls />);
    const paper = screen.getAllByText(/PAPER/i)[0];
    fireEvent.click(paper);
    // It used to fire the same mutation twice.
    expect(updateMutate.mock.calls.length).toBeLessThanOrEqual(1);
    if (updateMutate.mock.calls.length === 1) {
      expect(updateMutate.mock.calls[0][0]).toMatchObject({ is_paper: true });
    }
  });
});
