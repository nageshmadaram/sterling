/**
 * The expiry window, on the one component every engine's Contracts section uses.
 *
 * Minimum days to expiry, maximum days to expiry and expiry day are now settings
 * on every strategy. Putting them in `ContractsGroup` rather than in each panel
 * is what makes that true rather than merely similar — three copies is how the
 * pages came to word the same idea three ways.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ContractsGroup } from '../ScanSettings';

vi.mock('../../../../hooks/useSterlingKiteEngine', () => ({
  useStockRegistry: () => ({ data: [] }),
}));

const base = { strikes: ['ATM' as const], indexExpiries: ['weekly' as const] };

describe('ContractsGroup — the shared expiry window', () => {
  it('renders all three controls when an engine passes them', () => {
    render(<ContractsGroup {...base} dteMin={1} dteMax={14} avoidExpiryDay
                           onChange={vi.fn()} />);
    const text = document.body.textContent ?? '';
    expect(text).toContain('Minimum days to expiry');
    expect(text).toContain('Maximum days to expiry');
    expect(text).toContain('Expiry day');
    expect(screen.getByRole('switch', { name: /avoid expiry-day entries/i })).toBeTruthy();
  });

  it('stays out of the way for a caller that has not adopted them', () => {
    /* Optional on purpose: a panel that passes nothing must render exactly what
       it rendered before, so adding the control cannot change another page. */
    render(<ContractsGroup {...base} onChange={vi.fn()} />);
    expect(document.body.textContent).not.toContain('Minimum days to expiry');
  });

  it('emits the shared field names, so every engine stores it the same way', () => {
    const onChange = vi.fn();
    render(<ContractsGroup {...base} dteMin={1} dteMax={14} avoidExpiryDay={false}
                           onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch', { name: /avoid expiry-day entries/i }));
    expect(onChange).toHaveBeenCalledWith({ avoid_expiry_day: true });
  });

  it('reflects the value it was given rather than a local default', () => {
    render(<ContractsGroup {...base} dteMin={3} dteMax={21} avoidExpiryDay={false}
                           onChange={vi.fn()} />);
    const values = [...document.querySelectorAll('input')].map((i) => i.value);
    expect(values).toContain('3');
    expect(values).toContain('21');
  });
});

/** Panel sources, read through Vite rather than node:fs — this project has no
 *  @types/node, and the raw glob is typed and works the same under vitest. */
const PANEL_SOURCE = import.meta.glob('../../*.tsx', { as: 'raw', eager: true }) as Record<string, string>;
const ENGINE_SOURCE = import.meta.glob('../../../*.tsx', { as: 'raw', eager: true }) as Record<string, string>;

const sourceOf = (bag: Record<string, string>, file: string): string => {
  const key = Object.keys(bag).find((k) => k.endsWith(`/${file}`));
  if (!key) throw new Error(`${file} not found`);
  return bag[key];
};

describe('every engine panel actually passes the window', () => {
  /**
   * A source check rather than a render, because rendering five panels needs
   * five sets of mocks and the thing at risk is simpler than that: a panel that
   * quietly stops passing the props, or a new engine that never starts. The
   * component tests above prove the props work; this proves they are supplied.
   */
  it.each([
    ['SuperTrendEnginePanel.tsx', PANEL_SOURCE],
    ['NavigatorSettingsPanel.tsx', PANEL_SOURCE],
    ['AdaptiveEdgeSettingsPanel.tsx', PANEL_SOURCE],
  ])('%s passes dteMin/dteMax/avoidExpiryDay to expiry/contracts controls', (file, dir) => {
    const src = sourceOf(dir, file);
    expect(src.includes('<ContractsGroup') || src.includes('<ExpirySettingsGroup')).toBe(true);
    for (const prop of ['dteMin=', 'dteMax=', 'avoidExpiryDay=']) {
      expect(src, `${file} does not pass ${prop}`).toContain(prop);
    }
  });

  it.each([
    'GammaMoveSettings.tsx',
  ])('%s carries the same three labels', (file) => {
    /* These build their own Contracts section rather than using
       ContractsGroup — they resolve a single contract rather than a ladder — so
       the shared vocabulary is checked by its wording instead. */
    const src = sourceOf(ENGINE_SOURCE, file);
    for (const label of ['Minimum days to expiry', 'Maximum days to expiry', 'Expiry day']) {
      expect(src, `${file} is missing "${label}"`).toContain(label);
    }
  });
});
