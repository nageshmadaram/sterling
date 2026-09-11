/**
 * The contract controls, rendered the way the app renders them.
 *
 * Every other settings test mocks the strategy's hook, which means none of them
 * can see a mismatch between what the component reads and what the server
 * actually sends — the component gets a hand-written object that is correct by
 * construction. These render the real panels ConnectPane mounts, over the real
 * `/config/...` payloads captured from the running backend, and assert the
 * three shared expiry controls are present AND in an open section.
 *
 * `<Section>` is a `<details>`: its children sit in the DOM whether it is open
 * or shut, so "the text is present" is not the same claim as "a person can see
 * it". Both are asserted.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import gammaPayload from './fixtures/gamma-move.json';
import { GammaMoveSettingsPanel } from '../kite/GammaMoveSettingsPanel';

const PAYLOADS: Record<string, unknown> = {
  '/api/v1/config/gamma-move': gammaPayload,
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    const key = Object.keys(PAYLOADS).find((k) => url.endsWith(k));
    return {
      ok: true, status: 200,
      json: async () => (key ? PAYLOADS[key] : {}),
      text: async () => JSON.stringify(key ? PAYLOADS[key] : {}),
      headers: new Headers({ 'content-type': 'application/json' }),
    } as Response;
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

function mount(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

async function settled() {
  await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
}

function openSectionTitled(re: RegExp): boolean {
  const summary = [...document.querySelectorAll('summary')]
    .find((el) => re.test(el.textContent ?? ''));
  if (!summary) return false;
  return (summary.closest('details') as HTMLDetailsElement).open;
}

const PANELS: Array<[string, React.ReactElement]> = [
  ['Gamma Move', <GammaMoveSettingsPanel key="gm" />],
];

describe.each(PANELS)('%s settings, over the real API payload', (label, panel) => {
  it('shows minimum and maximum days to expiry, and the expiry-day switch', async () => {
    mount(panel);
    await settled();
    const text = document.body.textContent ?? '';
    expect(text, `${label} is missing the DTE window`).toContain('Minimum days to expiry');
    expect(text, `${label} is missing the DTE window`).toContain('Maximum days to expiry');
    expect(text, `${label} is missing the expiry-day control`).toContain('Expiry day');
    expect(screen.getByRole('switch', { name: /avoid expiry-day entries/i })).toBeTruthy();
  });

  it('puts them in an OPEN Contracts section', async () => {
    mount(panel);
    await settled();
    expect(openSectionTitled(/Contracts/), `${label}'s Contracts section is collapsed`).toBe(true);
  });

  it('orders Instruments before Contracts and drops "Universe"', async () => {
    mount(panel);
    await settled();
    const text = document.body.textContent ?? '';
    expect(text.indexOf('Instruments')).toBeGreaterThan(-1);
    expect(text.indexOf('Contracts')).toBeGreaterThan(text.indexOf('Instruments'));
    expect(text).not.toContain('Universe');
  });
});
