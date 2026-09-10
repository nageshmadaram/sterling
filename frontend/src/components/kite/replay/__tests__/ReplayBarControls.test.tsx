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

    // First option should be Yesterday/Previous Session matching last working day
    const prevSessionOpt = options[0];
    expect(prevSessionOpt).toBeTruthy();
    expect(prevSessionOpt.getAttribute('aria-selected')).toBe('true');

    // Click prior session
    const priorSessionOpt = options[1];
    act(() => {
      fireEvent.click(priorSessionOpt);
    });

    const updatedDraft = useReplayStore.getState().draft;
    expect(updatedDraft.date).not.toBe(lastDay);

    // Click first option again
    act(() => {
      fireEvent.click(trigger);
    });
    const reopenedOptions = screen.getAllByRole('option');
    act(() => {
      fireEvent.click(reopenedOptions[0]);
    });
    expect(useReplayStore.getState().draft.date).toBe(lastDay);
  });
});
