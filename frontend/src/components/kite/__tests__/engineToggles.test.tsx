/**
 * Every engine's running switch, and the dock following it.
 *
 * "What is running" answered for two engines out of six: SuperTrend had a switch,
 * Navigator had a "Configure →" link, and ORB, Gamma Move, Adaptive Edge and ATM
 * had nothing — the only way to stop one was to find its own settings page. And a
 * stopped engine still got a dock tab, on the reasoning that a missing tab is
 * harder to read than a stopped one.
 *
 * Both surfaces now come from `useEngineToggles` / `useEngineEnabled`, so they
 * cannot drift apart the way this repo's engine lists keep doing.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const setNavigator = vi.fn();
const patchSuperTrend = vi.fn();
let navRecord: unknown = { config: { enabled: true, auto_execute_originated: false }, revision: 3 };

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true, auto_execute: false } }),
  usePatchEngineConfig: () => ({ mutate: patchSuperTrend, isPending: false }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: navRecord ? { record: navRecord } : undefined }),
  useSetNavigatorConfig: () => ({ mutate: setNavigator, isPending: false }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: { config: { enabled: true } } }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../TradingModeControls', () => ({ TradingModeControls: () => <div>mode controls</div> }));

import { TradingModePanel } from '../TradingModePanel';

const ENGINES = [
  'SuperTrend engine', 'Value-Flow Navigator',
  'Gamma Move', 'Adaptive Edge',
];

/**
 * By ROLE, not by text.
 *
 * These engine names also appear in the "Included in re-scan" list further down
 * the same panel, so `getByText` finds two of several and throws. The running
 * control is a `role="switch"` carrying the engine's name as its `aria-label`;
 * the re-scan rows are plain checkboxes. The role separates them exactly.
 */
function switchFor(label: string): HTMLElement {
  return screen.getByRole('switch', { name: label });
}

beforeEach(() => {
  vi.clearAllMocks();
  navRecord = { config: { enabled: true, auto_execute_originated: false }, revision: 3 };
});
afterEach(cleanup);

describe('What is running', () => {
  it('names every engine', () => {
    render(<TradingModePanel />);
    for (const label of ENGINES) expect(switchFor(label)).toBeInTheDocument();
  });

  it('offers a switch for each of them', () => {
    render(<TradingModePanel />);
    for (const label of ENGINES) expect(switchFor(label)).toBeInTheDocument();
  });

  it('no longer sends the operator elsewhere to stop Navigator', () => {
    render(<TradingModePanel />);
    expect(screen.queryByRole('button', { name: /Configure/i })).toBeNull();
  });

  it('switches SuperTrend off through its own field name', () => {
    render(<TradingModePanel />);
    fireEvent.click(switchFor('SuperTrend engine'));
    // `engine_enabled`, not `enabled` — this engine predates the convention.
    expect(patchSuperTrend).toHaveBeenCalledWith({ engine_enabled: false });
  });

  it('sends Navigator its whole config and the revision it was read at', () => {
    render(<TradingModePanel />);
    fireEvent.click(switchFor('Value-Flow Navigator'));
    // Navigator writes under optimistic concurrency. A bare { enabled: false }
    // would be rejected — which is why this was a link and not a switch.
    expect(setNavigator).toHaveBeenCalledWith({
      config: { enabled: false, auto_execute_originated: false },
      expected_revision: 3,
    });
  });

  it('disables the switch for an engine whose config has not arrived', () => {
    // A toggle sends the opposite of the current value, so with no current value
    // there is nothing honest to send. Disabled beats guessing.
    navRecord = null;
    render(<TradingModePanel />);
    expect(switchFor('Value-Flow Navigator')).toBeDisabled();
  });

  it('reads as ON while a config is still loading, never as off', () => {
    navRecord = null;
    render(<TradingModePanel />);
    // The dock hides switched-off engines. If "not loaded" meant off, every tab
    // would blink out on page load and look like the operator's own setting.
    expect(switchFor('Value-Flow Navigator')).toBeChecked();
  });
});
