/**
 * Broker and strategy state in the footer.
 *
 * The property worth defending is restraint. Only SuperTrend publishes scan
 * timing; ORB and Adaptive Edge expose nothing about scanning, and Gamma Move and
 * the ATM bot expose a phase rather than a schedule. A strip promising
 * "scanning / next scan / last scan" for all five would be inventing four fifths
 * of itself, and an invented status on a trading dock is worse than a blank one.
 *
 * And the broker chip has three states, not two: connected, refused, and
 * COULD-NOT-ASK. Collapsing the third into "offline" is the same conflation that
 * produced a session-expired modal over a perfectly good session.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

let status: Record<string, unknown> = {};
let signals: Record<string, unknown> = {};

vi.mock('../../../hooks/useKite', () => ({ useKiteStatus: () => ({ data: status }) }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineSignals: () => ({ data: signals }),
  useEngineConfig: () => ({ data: { engine_enabled: true } }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: false } } } }),
}));
vi.mock('../../../hooks/useAdaptiveEdge', () => ({ useAdaptiveEdgeSnapshot: () => ({ data: null }) }));
vi.mock('../../../hooks/useGammaMove', () => ({ useGammaMoveSnapshot: () => ({ data: null }) }));

import { KiteFooterStatus } from '../KiteFooterStatus';

beforeEach(() => {
  cleanup();
  status = { connected: true, user_name: 'Madaram' };
  signals = { scanning: false, auto_scan: true, market_open: true };
});

describe('the broker chip', () => {
  it('reads connected when it is', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE')).toBeInTheDocument();
  });

  it('distinguishes "could not ask" from "refused"', () => {
    // Three states, not two. The stored token is intact in the middle one.
    status = { connected: false, transient: true };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE ?')).toBeInTheDocument();
    expect(screen.queryByText('KITE OFF'), 'not called offline').toBeNull();

    cleanup();
    status = { connected: false };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE OFF')).toBeInTheDocument();
  });

  it('says the token is untouched when the check merely failed', () => {
    status = { connected: false, transient: true };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTitle(/nothing has expired/i)).toBeInTheDocument();
  });

  it('opens the session panel even while connected', () => {
    // Worth being able to check on purpose, not only when something has broken.
    const onOpenSession = vi.fn();
    render(<KiteFooterStatus onOpenSession={onOpenSession} />);
    fireEvent.click(screen.getByText('KITE'));
    expect(onOpenSession).toHaveBeenCalled();
  });
});

describe('the strategy chips', () => {
  it('lists every strategy', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    for (const label of ['ST', 'NAV', 'AE', 'GM']) {
      expect(screen.getByText(label), label).toBeInTheDocument();
    }
  });

  it('shows what SuperTrend is scanning, since it is the one that reports it', () => {
    signals = { scanning: true, scanning_label: 'TCS OCT 2300 PE', auto_scan: true, market_open: true };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('TCS OCT 2300 PE')).toBeInTheDocument();
  });

  it('says "market closed" rather than pretending a schedule is live', () => {
    signals = { scanning: false, auto_scan: true, market_open: false };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('market closed')).toBeInTheDocument();
  });

  it('marks a switched-off engine as off', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTitle(/^NAV — off · off$/)).toBeInTheDocument();
  });
});
