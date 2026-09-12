/**
 * The signal-board preference: which engine opens first, and the tab order.
 *
 * The load-bearing assertions are the two fallbacks. An engine missing from a
 * saved order must sort to the END rather than vanish — otherwise a preference
 * saved before a new engine existed hides it. And a default naming a
 * switched-off engine must not strand the board on a tab that is not rendered.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ENGINE_ORDER, orderEngines } from '../../../store/useKiteSettings';

vi.mock('../../../hooks/useEngineToggles', () => ({
  useEngineEnabled: () => ({
    supertrend: true, navigator: true, gamma_move: true,
    adaptive_edge: true, intraday: true, snapback: false,
  }),
}));

import { SignalEngineOrderSettings } from '../SignalEngineOrderSettings';
import { useKiteSettings } from '../../../store/useKiteSettings';

beforeEach(() => {
  useKiteSettings.setState({ defaultSignalEngine: 'supertrend', engineOrder: [] });
});

describe('orderEngines', () => {
  const tabs = ENGINE_ORDER.map((id) => ({ id }));

  it('follows the operator’s order', () => {
    const out = orderEngines(tabs, ['intraday', 'snapback']);
    expect(out[0].id).toBe('intraday');
    expect(out[1].id).toBe('snapback');
  });

  it('puts an engine the preference has never heard of at the END, not nowhere', () => {
    // A preference saved before an engine existed must not hide it.
    const out = orderEngines(tabs, ['snapback']);
    expect(out[0].id).toBe('snapback');
    expect(out.map((t) => t.id)).toEqual(
      expect.arrayContaining(ENGINE_ORDER));
    expect(out).toHaveLength(ENGINE_ORDER.length);
  });

  it('falls back to the built-in order when nothing is saved', () => {
    expect(orderEngines(tabs, []).map((t) => t.id)).toEqual(ENGINE_ORDER);
  });

  it('keeps the relative built-in order among unlisted engines', () => {
    const out = orderEngines(tabs, ['intraday']).map((t) => t.id);
    const rest = ENGINE_ORDER.filter((id) => id !== 'intraday');
    expect(out[0]).toBe('intraday');
    expect(out.slice(1)).toEqual(rest);
  });
});

describe('Signal board settings card', () => {
  it('lists every engine, including switched-off ones', () => {
    render(<SignalEngineOrderSettings />);
    // Off engines stay listed: hiding them would rearrange the order the
    // moment someone toggles one on.
    expect(screen.getByRole('radio', { name: /Open Snapback first/ }))
      .toBeInTheDocument();
    expect(screen.getByText('off')).toBeInTheDocument();
  });

  it('marks which one opens first', () => {
    render(<SignalEngineOrderSettings />);
    expect(screen.getByRole('radio', { name: /Open SuperTrend first/ }))
      .toHaveAttribute('aria-checked', 'true');
  });

  it('does not write until Apply', () => {
    render(<SignalEngineOrderSettings />);
    fireEvent.click(screen.getByRole('radio', { name: /Open Intraday first/ }));
    expect(useKiteSettings.getState().defaultSignalEngine).toBe('supertrend');
    fireEvent.click(screen.getByRole('button', { name: /Apply/i }));
    expect(useKiteSettings.getState().defaultSignalEngine).toBe('intraday');
  });

  it('reorders and persists the whole list', () => {
    render(<SignalEngineOrderSettings />);
    fireEvent.click(screen.getByRole('button', { name: /Move Intraday up/ }));
    fireEvent.click(screen.getByRole('button', { name: /Apply/i }));
    const saved = useKiteSettings.getState().engineOrder;
    expect(saved.indexOf('intraday')).toBeLessThan(saved.indexOf('gamma_move'));
    expect(saved).toHaveLength(ENGINE_ORDER.length);
  });

  it('cannot move the first engine up or the last one down', () => {
    render(<SignalEngineOrderSettings />);
    expect(screen.getByRole('button', { name: /Move SuperTrend up/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Move Snapback down/ })).toBeDisabled();
  });

  it('discards a draft without writing it', () => {
    render(<SignalEngineOrderSettings />);
    fireEvent.click(screen.getByRole('radio', { name: /Open Gamma Move first/ }));
    fireEvent.click(screen.getByRole('button', { name: /Discard/i }));
    expect(useKiteSettings.getState().defaultSignalEngine).toBe('supertrend');
  });
});
