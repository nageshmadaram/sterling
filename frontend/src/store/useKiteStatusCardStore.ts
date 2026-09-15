import { create } from 'zustand';

interface KiteStatusCardStore {
  isOpen: boolean;
  openCard: () => void;
  closeCard: () => void;
  toggleCard: () => void;
}

export const useKiteStatusCardStore = create<KiteStatusCardStore>((set) => ({
  isOpen: false,
  openCard: () => set({ isOpen: true }),
  closeCard: () => set({ isOpen: false }),
  toggleCard: () => set((state) => ({ isOpen: !state.isOpen })),
}));

export const openKiteStatusCard = () => useKiteStatusCardStore.getState().openCard();
export const closeKiteStatusCard = () => useKiteStatusCardStore.getState().closeCard();
export const toggleKiteStatusCard = () => useKiteStatusCardStore.getState().toggleCard();
