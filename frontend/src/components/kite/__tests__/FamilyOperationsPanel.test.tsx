/**
 * The family screen renders server truth and nothing else.
 *
 * It must never compute eligibility, must never show a green screen when the backend
 * is unreachable, and must never turn an unknown value into a reassuring number.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { FamilyOperationsPanel } from '../FamilyOperationsPanel';
import type { SnapbackFamilyOperations } from '../../../hooks/useSnapbackFamilyOperations';

const get = vi.fn();
const post = vi.fn();

vi.mock('../../../utils/api', () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

function body(overrides: Partial<SnapbackFamilyOperations> = {}): SnapbackFamilyOperations {
  return {
    system_status: 'HEALTHY',
    mode: 'PAPER',
    strategy: 'Snapback 1.0.5',
    runtime_sha: 'abc123',
    build_sha: 'build-sha-1',
    strategy_manifest: 'manifest',
    evidence: 'INCONCLUSIVE',
    evidence_missing_requirements: [
      'independent sessions 0 of 60 required',
      'completed trades 0 of 300 required',
    ],
    broker_connected: true,
    market_data_fresh: false,
    runner_alive: true,
    backup_ok: null,
    alert_transport_configured: true,
    last_report: null,
    allocated_capital: 100000,
    cumulative_net_pnl: null,
    mean_net_pnl_per_trade: null,
    current_exposure_inr: null,
    open_positions_count: 0,
    exit_pending: 0,
    drawdown_pct: null,
    new_trades_halted: false,
    live_blocked: true,
    unresolved_errors: [],
    generated_at: '2026-09-16T12:00:00Z',
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FamilyOperationsPanel />
    </QueryClientProvider>,
  );
}

describe('FamilyOperationsPanel', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    post.mockResolvedValue({});
  });

  it('shows the system status, mode and evidence verdict', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    expect(await screen.findByTestId('system-status')).toHaveTextContent('HEALTHY');
    expect(screen.getByTestId('mode')).toHaveTextContent('PAPER');
    expect(screen.getByTestId('evidence')).toHaveTextContent('INCONCLUSIVE');
  });

  it('states plainly that live trading is blocked', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    expect(await screen.findByTestId('live-blocked')).toHaveTextContent('LIVE TRADING BLOCKED');
  });

  it('lists what the evidence is still missing', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    expect(await screen.findByText(/0 of 60 required/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 300 required/)).toBeInTheDocument();
  });

  it('shows UNKNOWN rather than zero for values it does not have', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    expect(await screen.findByTestId('cumulative-pnl')).toHaveTextContent('UNKNOWN');
    expect(screen.getByTestId('drawdown')).toHaveTextContent('UNKNOWN');
    expect(screen.getByTestId('exposure')).toHaveTextContent('UNKNOWN');
  });

  it('shows an unknown backup as UNKNOWN, not OK', async () => {
    get.mockResolvedValue(body({ backup_ok: null }));

    renderPanel();

    expect(await screen.findByTestId('flag-backup')).toHaveTextContent('UNKNOWN');
  });

  it('stops new trades when the button is pressed', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    fireEvent.click(await screen.findByTestId('stop-new-trades'));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/snapback/family/stop-new-trades'),
      ),
    );
  });

  it('requires a confirmation before resuming', async () => {
    get.mockResolvedValue(body({ new_trades_halted: true, system_status: 'HALTED', mode: 'HALTED' }));

    renderPanel();

    fireEvent.click(await screen.findByTestId('resume'));
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('confirm-resume'));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/snapback/family/resume-new-trades'),
      ),
    );
  });

  it('does not show a healthy screen when the backend is unreachable', async () => {
    get.mockRejectedValue(new Error('network down'));

    renderPanel();

    expect(await screen.findByTestId('family-unreachable')).toBeInTheDocument();
    expect(screen.queryByTestId('system-status')).toBeNull();
  });

  it('shows unresolved errors when the backend reports them', async () => {
    get.mockResolvedValue(body({ unresolved_errors: ['broker_disconnected'], system_status: 'DEGRADED' }));

    renderPanel();

    expect(await screen.findByTestId('unresolved-errors')).toHaveTextContent('broker_disconnected');
  });

  it('shows the executing build', async () => {
    get.mockResolvedValue(body());

    renderPanel();

    expect(await screen.findByTestId('runtime-build')).toHaveTextContent('build-sha-1');
  });

  it('exposes no strategy parameter controls', async () => {
    get.mockResolvedValue(body());

    renderPanel();
    await screen.findByTestId('family-operations');

    for (const label of [/target delta/i, /premium stop/i, /lookback/i, /runner/i]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
  });
});
