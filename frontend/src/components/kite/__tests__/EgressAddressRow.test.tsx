/**
 * The address an operator copies into the broker's site.
 *
 * It must never imply an address is registered when none is, and never present
 * a value Sterling has not recorded as if it had been verified.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { EgressAddressRow, type EgressStatus } from '../EgressAddressRow';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...args: unknown[]) => get(...args) } }));

function status(overrides: Partial<EgressStatus> = {}): EgressStatus {
  return {
    expected_ip: '203.0.113.7',
    observed_ip: '203.0.113.7',
    observed_at: '2026-09-18T12:00:00Z',
    router_generation: null,
    verified: true,
    reason: '203.0.113.7 matches the registered address',
    changed_from: null,
    record_command: 'sterlingctl egress record <ip>',
    ...overrides,
  };
}

function renderRow() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    // The offline case deliberately rejects. Without a sink the rejection is
    // reported as unhandled and fails the run for the wrong reason.
    queryCache: new QueryCache({ onError: () => undefined }),
  });
  return render(
    <QueryClientProvider client={client}>
      <EgressAddressRow />
    </QueryClientProvider>,
  );
}

beforeEach(() => get.mockReset());

describe('EgressAddressRow', () => {
  it('shows the observed address with a copy control', async () => {
    get.mockResolvedValue(status());
    renderRow();
    await waitFor(() => expect(screen.getAllByText('203.0.113.7').length).toBeGreaterThan(0));
    expect(screen.getByLabelText('copy observed outbound IP')).toBeTruthy();
    expect(screen.getByLabelText('egress VERIFIED')).toBeTruthy();
  });

  it('says NOT RECORDED rather than showing a blank', async () => {
    get.mockResolvedValue(status({ observed_ip: null, verified: null, reason: 'never observed' }));
    renderRow();
    await waitFor(() => expect(screen.getByText(/NOT RECORDED/)).toBeTruthy());
    expect(screen.getByText(/sterlingctl egress record/)).toBeTruthy();
  });

  it('says UNSET when no address is registered with the broker', async () => {
    get.mockResolvedValue(status({ expected_ip: null, verified: null }));
    renderRow();
    await waitFor(() => expect(screen.getByText(/UNSET/)).toBeTruthy());
  });

  it('an unverified address is never labelled verified', async () => {
    get.mockResolvedValue(status({ verified: null }));
    renderRow();
    await waitFor(() => expect(screen.getByLabelText('egress UNVERIFIED')).toBeTruthy());
  });

  it('a mismatch is called out, not softened', async () => {
    get.mockResolvedValue(
      status({ observed_ip: '198.51.100.9', verified: false, reason: 'does not match' }),
    );
    renderRow();
    await waitFor(() => expect(screen.getByLabelText('egress MISMATCH')).toBeTruthy());
  });

  it('a changed address tells the operator to re-approve it', async () => {
    get.mockResolvedValue(status({ changed_from: '198.51.100.9' }));
    renderRow();
    await waitFor(() => expect(screen.getByText(/re-approve it with the broker/)).toBeTruthy());
  });

  // The unavailable path is covered where it can be asserted without a thrown
  // mock: FamilyOperationsPanel's unreachable test and the Playwright spec.
  // Throwing from this module's mock fails the file even with no render at all.
});
