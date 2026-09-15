import { describe, it, expect, beforeEach } from 'vitest';
import {
  useKiteStatusCardStore,
  openKiteStatusCard,
  closeKiteStatusCard,
  toggleKiteStatusCard,
} from '../useKiteStatusCardStore';

beforeEach(() => {
  useKiteStatusCardStore.setState({ isOpen: false });
});

describe('useKiteStatusCardStore', () => {
  it('defaults to closed', () => {
    expect(useKiteStatusCardStore.getState().isOpen).toBe(false);
  });

  it('opens and closes via helper actions', () => {
    openKiteStatusCard();
    expect(useKiteStatusCardStore.getState().isOpen).toBe(true);

    closeKiteStatusCard();
    expect(useKiteStatusCardStore.getState().isOpen).toBe(false);
  });

  it('toggles state correctly', () => {
    toggleKiteStatusCard();
    expect(useKiteStatusCardStore.getState().isOpen).toBe(true);

    toggleKiteStatusCard();
    expect(useKiteStatusCardStore.getState().isOpen).toBe(false);
  });
});
