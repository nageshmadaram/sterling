import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useReplayStore } from '../../../../hooks/useReplayStore';
import { getLastMarketWorkingDay } from '../../../../lib/replay/marketSessions';
import { ReplaySessionDropdown } from '../ReplayBarControls';
import { primeStore } from './testUtils';

beforeEach(() => {
  localStorage.clear();
  primeStore();
});

describe('ReplaySessionDropdown', () => {
  it('renders trigger button showing active preset label', () => {
    const lastDay = getLastMarketWorkingDay();
    useReplayStore.getState().setDraft({ date: lastDay, endDate: lastDay });

    render(<ReplaySessionDropdown />);
    const trigger = screen.getByTestId('replay-session-trigger');
    expect(trigger).toBeTruthy();
    // For the last market working day, it should display "Yesterday" (or "Previous Session" if weekend/holiday)
    expect(trigger.textContent).toContain('SESSION');
  });

  it('opens popover and lists presets with session date hints', () => {
    const lastDay = getLastMarketWorkingDay();
    useReplayStore.getState().setDraft({ date: lastDay, endDate: lastDay });

    render(<ReplaySessionDropdown />);
    const trigger = screen.getByTestId('replay-session-trigger');
    act(() => {
      fireEvent.click(trigger);
    });

    const menu = screen.getByRole('listbox');
    expect(menu).toBeTruthy();

    const options = screen.getAllByRole('option');
    expect(options.length).toBeGreaterThanOrEqual(2);

    // Find the preset for the last completed session BY ITS DATE, not by its
    // position. `getDynamicMarketPresets` prepends a "Today" option on any
    // trading day after 09:00 IST, so asserting on `options[0]` made this test
    // pass overnight and at weekends and fail during market hours.
    const prevSessionOpt = options.find(
      (o) => o.getAttribute('aria-selected') === 'true',
    );
    expect(prevSessionOpt).toBeTruthy();
    expect(prevSessionOpt!.textContent).toMatch(/Yesterday|Previous Session/);

    // Click a different preset — the one after the selected session.
    const selectedIndex = options.indexOf(prevSessionOpt!);
    const priorSessionOpt = options[selectedIndex + 1];
    expect(priorSessionOpt).toBeTruthy();
    act(() => {
      fireEvent.click(priorSessionOpt);
    });

    const updatedDraft = useReplayStore.getState().draft;
    expect(updatedDraft.date).not.toBe(lastDay);

    // Re-select the last completed session, again by identity rather than
    // position.
    act(() => {
      fireEvent.click(trigger);
    });
    const reopened = screen
      .getAllByRole('option')
      .find((o) => /Yesterday|Previous Session/.test(o.textContent ?? ''));
    expect(reopened).toBeTruthy();
    act(() => {
      fireEvent.click(reopened!);
    });
    expect(useReplayStore.getState().draft.date).toBe(lastDay);
  });
});
