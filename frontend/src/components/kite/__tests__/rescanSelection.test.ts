/**
 * Which strategies a re-scan covers.
 *
 * Two separate questions that must not be collapsed:
 *
 *   is this strategy RUNNING?      — server-side, decides whether it produces
 *                                    signals at all, for everyone
 *   is it in THIS button's sweep?  — local, and only about one press
 *
 * They share one historical-data budget and run one at a time, so a scan of five
 * engines costs five times a scan of one. An operator working a single strategy
 * should be able to stop paying for the other four without switching them off.
 *
 * The two are ANDed, never ORed: a stopped engine is skipped whatever is ticked,
 * because scanning it is work already declined.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

async function store() {
  vi.resetModules();
  return (await import('../../../store/useKiteSettings')).useKiteSettings;
}

beforeEach(() => localStorage.clear());

describe('the re-scan selection', () => {
  it('includes everything by default', async () => {
    const s = await store();
    // Absent means included. The map holds only EXCLUSIONS, so an engine added
    // tomorrow is covered rather than silently missing from every saved map.
    expect(s.getState().rescanStrategies).toEqual({});
    for (const e of ['supertrend', 'navigator', 'gamma_move', 'adaptive_edge']) {
      expect(s.getState().rescanStrategies[e], e).not.toBe(false);
    }
  });

  it('excludes one without touching the others', async () => {
    const s = await store();
    s.getState().toggleRescanStrategy('navigator');
    expect(s.getState().rescanStrategies.navigator).toBe(false);
    expect(s.getState().rescanStrategies.supertrend).toBeUndefined();
  });

  it('toggles back', async () => {
    const s = await store();
    s.getState().toggleRescanStrategy('navigator');
    s.getState().toggleRescanStrategy('navigator');
    expect(s.getState().rescanStrategies.navigator).toBe(true);
  });

  it('survives a reload, because a scan budget is not a per-session whim', async () => {
    const s = await store();
    s.getState().toggleRescanStrategy('gamma_move');
    const reloaded = await store();
    expect(reloaded.getState().rescanStrategies.gamma_move).toBe(false);
  });

  it('is stored under exclusions only, never a full map', async () => {
    // If this ever became "write every engine as true", adding an engine would
    // require a migration to include it — which is the trap this shape avoids.
    const s = await store();
    s.getState().toggleRescanStrategy('orb');
    expect(Object.keys(s.getState().rescanStrategies)).toEqual(['orb']);
  });
});
