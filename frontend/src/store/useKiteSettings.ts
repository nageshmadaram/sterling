import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { KiteBrandIcon, KiteBrandIconSize } from '../utils/kiteBrandIcon';
import type { NavItem } from '../components/kite/KiteLayout';

/**
 * The built-in tab order, and the fallback whenever the stored one is empty or
 * does not mention an engine.
 *
 * Not imported from `boardTypes` on purpose: that module pulls in the board's
 * render types, and the store is loaded by everything.
 */
export const ENGINE_ORDER: string[] = [
  'supertrend', 'adaptive_edge', 'gamma_move', 'intraday', 'snapback',
];

/**
 * Sort a list of engine ids by the operator's order.
 *
 * An engine absent from the preference sorts AFTER every one present, keeping
 * its built-in position among the others — so a preference saved before an
 * engine existed still works and the new engine appears at the end rather than
 * disappearing.
 */
export function orderEngines<T extends { id: string }>(
  tabs: readonly T[], order: readonly string[],
): T[] {
  const pref = order.length ? order : ENGINE_ORDER;
  const rank = (id: string) => {
    const i = pref.indexOf(id);
    if (i >= 0) return i;
    const j = ENGINE_ORDER.indexOf(id);
    return pref.length + (j >= 0 ? j : ENGINE_ORDER.length);
  };
  return [...tabs].sort((a, b) => rank(a.id) - rank(b.id));
}

export type LoaderStyle = 'ubuntu' | 'mac' | 'material' | 'windows' | 'gnome' | 'kde' | 'minimal' | 'classic' | 'off';

export type MotionStyle = Exclude<LoaderStyle, 'classic' | 'off'>;

// showHoldings / showNotes / showGroupColors were removed on 2026-08-08: two
// separate toggle UIs wrote them and NOTHING rendered them, so they were three
// checkboxes that did nothing on either the watchlist or the signal board.
type ToggleShowKey = 'showPriceChange' | 'showPriceChangePct' | 'showPriceDirection' | 'showExchange' | 'showLeg';

export interface KiteSettingsState {
  defaultSection: NavItem;
  setDefaultSection: (section: NavItem) => void;
  loaderStyle: LoaderStyle;
  brandIcon: KiteBrandIcon;
  brandIconSize: KiteBrandIconSize;
  recentBrandIcons: KiteBrandIcon[];
  engineSettingsLayout: 'tabs' | 'cards';
  chgType: 'close' | 'open';
  showPriceChange: boolean;
  showPriceChangePct: boolean;
  showPriceDirection: boolean;
  showExchange: boolean;
  showLeg: boolean;
  sortBy: string;
  legSort: { key: string; dir: string };
  signalLeftColumnOrder: string[];
  signalRightColumnOrder: string[];
  /**
   * Signal-table columns the operator has switched off, by column key.
   *
   * Per-column, and specific to the signal table. The five `show*` booleans
   * above are the watchlist panel's and group several columns behind one flag —
   * `premium` alone gates Entry, SL, TSL and Target — so a COLUMNS menu built
   * from them could not name the columns it was actually hiding. Ordering is
   * already per-table here (`signalLeftColumnOrder`); visibility now matches.
   */
  hiddenSignalCols: string[];
  setLoaderStyle: (s: LoaderStyle) => void;
  setBrandIcon: (icon: KiteBrandIcon) => void;
  setBrandIconSize: (size: KiteBrandIconSize) => void;
  setEngineSettingsLayout: (l: 'tabs' | 'cards') => void;
  setChgType: (t: 'close' | 'open') => void;
  toggleShow: (key: ToggleShowKey) => void;
  setSortBy: (s: string) => void;
  setLegSort: (sort: { key: string; dir: string }) => void;
  reorderSignalColumn: (group: 'left' | 'right', fromKey: string, toKey: string) => void;
  toggleSignalCol: (key: string) => void;
  showAllSignalCols: () => void;
  /**
   * Board capabilities, as choices rather than as one implementation's habits.
   *
   * SuperTrend's table grew three things the shared board never had: draggable
   * column headers, rows that scroll sideways on their own, and order buttons
   * sitting in the row. Keeping them meant keeping a second table; dropping them
   * meant deciding for the operator which ones they could live without.
   *
   * They are settings instead. Every one defaults ON, so nothing changes for
   * anyone who does not go looking, and the shared board can offer the same
   * three to every engine rather than one engine having them by accident of
   * which component it happens to render through.
   */
  boardDragColumns: boolean;
  boardRowScroll: boolean;
  boardRowActions: boolean;
  /**
   * Which component draws SuperTrend's rows.
   *
   * `shared` is `SignalBoard` — the same component the other four engines use,
   * which is what makes the two tables identical by construction rather than by
   * a list of matched properties. `classic` is the bespoke table this board grew
   * up with, kept because it is the only view that has ever been used against a
   * live account and I cannot see the shared one rendered behind the broker
   * login. It is a way back, not a second product.
   *
   * `classic` is still the DEFAULT. Two of the three original gaps are closed —
   * a signal that resolved to no contract now says so inline, and
   * `hoistLiveFromToday` reproduces the Active-now section — but one remains, and
   * it is the reason the default has not flipped:
   *
   * **Per-leg diagnostics.** The bespoke row distinguishes a trail close from a
   * red-counter close in words ("TSL exit" vs "counter exit"), marks a re-entry,
   * and prints the entry/stop premium snapshot inline. Twenty-five tests cover
   * those, and they matter: the gap between "the premium is through its trail"
   * and "the engine has not closed it yet" is exactly where an open drawdown
   * builds. Moving them behind a click would be losing them.
   *
   * Closing it needs one more capability on the shared board — inline per-row
   * marks supplied by the engine, alongside `renderRowActions`. Until then this
   * setting is a way to see the shared renderer, not a recommendation.
   */
  boardRenderer: 'shared' | 'classic';
  setBoardRenderer: (r: 'shared' | 'classic') => void;
  /**
   * List or cards, for the signal table.
   *
   * It lived as local state inside SuperTrend's pane, written straight to
   * localStorage under `kite_st_view_layout`. That was fine while the settings
   * drawer lived in the same component — but the drawer is common to every
   * engine and now opens from the pane's own title bar, so the setting has to be
   * somewhere both can read. Two copies of a persisted preference is one copy
   * too many.
   */
  signalViewLayout: 'grid' | 'list';
  setSignalViewLayout: (l: 'grid' | 'list') => void;
  /**
   * Which strategies the re-scan button includes.
   *
   * Distinct from whether a strategy is RUNNING, which is a server-side setting
   * that decides whether it produces signals at all. This is local and only about
   * one press: they share a single historical-data budget and run one at a time,
   * so a scan of five engines costs five times a scan of one. An operator working
   * a single strategy should be able to stop paying for the other four without
   * switching them off for everyone.
   *
   * A switched-off engine is skipped regardless of what is ticked here — the
   * server-side flag wins, and the two are ANDed, never ORed.
   */
  rescanStrategies: Record<string, boolean>;
  toggleRescanStrategy: (engine: string) => void;
  /**
   * Which signal board opens first, and the order the engine tabs sit in.
   *
   * Both are one preference about the same list, so they live together. The
   * board used to hardcode `useState<EngineId>('supertrend')` and render the
   * tabs in the order the array happened to be written in — so an operator who
   * only trades one engine paid a click on every visit and read four tabs they
   * do not use before reaching it.
   *
   * `engineOrder` is a partial list on purpose: an engine missing from it sorts
   * after the ones present, in the built-in order. That way a preference saved
   * before a new engine existed keeps working and the new engine simply appears
   * at the end, rather than vanishing because it was not in the saved array.
   */
  defaultSignalEngine: string;
  setDefaultSignalEngine: (engine: string) => void;
  engineOrder: string[];
  setEngineOrder: (order: string[]) => void;
  moveEngine: (engine: string, delta: number) => void;
  toggleBoardCapability: (key: BoardCapabilityKey) => void;
  resetSignalTableSettings: () => void;
}

export type BoardCapabilityKey = 'boardDragColumns' | 'boardRowScroll' | 'boardRowActions';

export const useKiteSettings = create<KiteSettingsState>()(
  persist(
    (set) => ({
      defaultSection: 'dashboard',
      setDefaultSection: (section) => set({ defaultSection: section }),
      defaultSignalEngine: 'supertrend',
      setDefaultSignalEngine: (engine) => set({ defaultSignalEngine: engine }),
      engineOrder: [],
      setEngineOrder: (order) => set({ engineOrder: [...order] }),
      moveEngine: (engine, delta) => set((st) => {
        const base = st.engineOrder.length ? [...st.engineOrder] : [...ENGINE_ORDER];
        const from = base.indexOf(engine);
        if (from < 0) return {};
        const to = from + delta;
        if (to < 0 || to >= base.length) return {};
        const [item] = base.splice(from, 1);
        base.splice(to, 0, item);
        return { engineOrder: base };
      }),
      loaderStyle: 'ubuntu',
      brandIcon: 'phoenix',
      brandIconSize: 'medium',
      recentBrandIcons: [],
      engineSettingsLayout: 'tabs',
      chgType: 'close',
      // ON by default: these describe how the board behaves today, so a fresh
      // install and an existing one look the same.
      boardDragColumns: true,
      boardRowScroll: true,
      boardRowActions: true,
      boardRenderer: 'classic',
      signalViewLayout: 'list',
      // Everything included by default: an operator who has never opened this
      // should get the behaviour the button has always had.
      rescanStrategies: {},
      showPriceChange: true,
      showPriceChangePct: true,
      showPriceDirection: true,
      showExchange: true,
      showLeg: true,
      sortBy: 'Custom',
      legSort: { key: '', dir: '' },
      signalLeftColumnOrder: ['exc', 'leg', 'entry', 'sl', 'tsl', 'exit', 'target'],
      signalRightColumnOrder: ['chg', 'chgPct', 'dir', 'ltp', 'time', 'trade', 'chart'],
      hiddenSignalCols: [],
      setLoaderStyle: (s) => set({ loaderStyle: s === 'classic' ? 'material' : s === 'off' ? 'minimal' : s }),
      setBrandIcon: (icon) => set((state) => ({
        brandIcon: icon,
        recentBrandIcons: [icon, ...state.recentBrandIcons.filter((i) => i !== icon)].slice(0, 5),
      })),
      setBrandIconSize: (size) => set({ brandIconSize: size }),
      setEngineSettingsLayout: (l) => set({ engineSettingsLayout: l }),
      setChgType: (t) => set({ chgType: t }),
      toggleShow: (key) => set((state) => ({ [key]: !state[key] })),
      toggleBoardCapability: (key) => set((state) => ({ [key]: !state[key] })),
      setBoardRenderer: (r) => set({ boardRenderer: r }),
      setSignalViewLayout: (l) => set({ signalViewLayout: l }),
      // Absent means included, so the map only ever holds exclusions. That keeps
      // a new engine included the day it appears, rather than silently missing
      // from everyone's saved map.
      toggleRescanStrategy: (engine) => set((state) => ({
        rescanStrategies: {
          ...state.rescanStrategies,
          [engine]: state.rescanStrategies[engine] === false,
        },
      })),
      setSortBy: (s) => set({ sortBy: s }),
      setLegSort: (sort) => set({ legSort: sort }),
      reorderSignalColumn: (group, fromKey, toKey) => set((state) => {
        const field = group === 'left' ? 'signalLeftColumnOrder' : 'signalRightColumnOrder';
        const order = [...state[field]];
        const fromIdx = order.indexOf(fromKey);
        const toIdx = order.indexOf(toKey);
        if (fromIdx === -1 || toIdx === -1) return {};
        order.splice(fromIdx, 1);
        order.splice(toIdx, 0, fromKey);
        return { [field]: order } as Partial<KiteSettingsState>;
      }),
      toggleSignalCol: (key) => set((state) => ({
        hiddenSignalCols: state.hiddenSignalCols.includes(key)
          ? state.hiddenSignalCols.filter((k) => k !== key)
          : [...state.hiddenSignalCols, key],
      })),
      showAllSignalCols: () => set({ hiddenSignalCols: [] }),
      resetSignalTableSettings: () => set({
        defaultSection: 'dashboard',
        boardDragColumns: true,
        boardRowScroll: true,
        boardRowActions: true,
        boardRenderer: 'classic',
        signalViewLayout: 'list',
        rescanStrategies: {},
        showPriceChange: true,
        showPriceChangePct: true,
        showPriceDirection: true,
        showExchange: true,
        showLeg: true,
        legSort: { key: '', dir: '' },
        signalLeftColumnOrder: ['exc', 'leg', 'entry', 'sl', 'tsl', 'exit', 'target'],
        signalRightColumnOrder: ['chg', 'chgPct', 'dir', 'ltp', 'time', 'trade', 'chart'],
        hiddenSignalCols: [],
      }),
    }),
    {
      name: 'kite-settings',
      version: 9,
      migrate: (persisted: any) => {
        const loaderStyle = (() => {
          const legacy = persisted?.loaderStyle;
          if (legacy === 'classic') return 'material';
          if (legacy === 'off') return 'minimal';
          const known = ['mac', 'ubuntu', 'material', 'windows', 'gnome', 'kde', 'minimal'];
          return known.includes(legacy) ? legacy : 'ubuntu';
        })();
        let next = { ...persisted, loaderStyle };

        // v4 adds the Time column to the signal table's right group.
        //
        // Bumping the default array is not enough: this order is persisted, so
        // anyone who has used the app already has a stored array without 'time'
        // and would simply never see the column. Appended rather than inserted,
        // so an operator's own column arrangement survives.
        const right = next.signalRightColumnOrder;
        if (Array.isArray(right) && !right.includes('time')) {
          next = { ...next, signalRightColumnOrder: [...right, 'time'] };
        }

        // v5 adds the three board capabilities. A stored state predating them has
        // the keys absent, and `undefined` is falsy — so without this every
        // existing user would open the app to find dragging, row scrolling and
        // the in-row order buttons all switched off, having chosen nothing.
        for (const key of ['boardDragColumns', 'boardRowScroll', 'boardRowActions'] as const) {
          if (typeof next[key] !== 'boolean') next = { ...next, [key]: true };
        }

        // v6 introduces the shared renderer as an OPT-IN, not the default.
        //
        // Switching it on by default would have been the wrong kind of "done":
        // the shared board groups by trading day, so the classic table's
        // "Active now" and "Today (ended)" buckets vanish, and a signal whose
        // strike resolved to nothing loses the message explaining why. Twenty-
        // seven existing tests said so. Those describe behaviour, not markup, so
        // the answer is to close the gaps before flipping the default — not to
        // update the tests and call the difference gone.
        if (next.boardRenderer !== 'shared' && next.boardRenderer !== 'classic') {
          next = { ...next, boardRenderer: 'classic' };
        }

        // v8 puts Trade and Chart in the signal table's column list.
        //
        // They were defined in `SIGNAL_RIGHT_COLUMNS` when Buy/Sell/chart became
        // columns on the shared board, and the bespoke table's picker is built
        // from this persisted ORDER — which did not contain them. So the entries
        // existed, the picker never listed them, and the answer to "where is the
        // show/hide option for Buy and Sell" was: nowhere, on the renderer that
        // is still the default. Defining a column is not offering it.
        //
        // Appended, like 'time' above, so an operator's own arrangement survives.
        const right8 = next.signalRightColumnOrder;
        if (Array.isArray(right8)) {
          const missing = ['trade', 'chart'].filter((k) => !right8.includes(k));
          if (missing.length) next = { ...next, signalRightColumnOrder: [...right8, ...missing] };
        }

        // v7 adopts the layout choice from the standalone key the pane used to
        // write. Read it rather than resetting to the default: someone who chose
        // cards should not silently be put back on the list because the setting
        // moved house.
        if (next.signalViewLayout !== 'grid' && next.signalViewLayout !== 'list') {
          let adopted: 'grid' | 'list' = 'list';
          try {
            const legacy = localStorage.getItem('kite_st_view_layout');
            if (legacy === 'grid' || legacy === 'list') adopted = legacy;
          } catch { /* storage unavailable — the default stands */ }
          next = { ...next, signalViewLayout: adopted };
        }

        // v9 adds defaultSection setting.
        const validSections: NavItem[] = ['dashboard', 'astro', 'pcr', 'openingLeaders', 'orders', 'holdings', 'positions', 'more', 'data', 'adaptiveEdge', 'backtest', 'connect', 'help'];
        if (!next.defaultSection || !validSections.includes(next.defaultSection)) {
          next = { ...next, defaultSection: 'dashboard' };
        }

        return next;
      },
    },
  ),
);
