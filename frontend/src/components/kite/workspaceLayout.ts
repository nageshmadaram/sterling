export type WorkspacePaneId = 'watchlist' | 'dashboard' | 'signals' | 'terminal';
export type WorkspaceSlotId = 'left' | 'center' | 'right' | 'bottom';
export type WorkspaceFocusMode = 'half' | 'maximized' | 'fullscreen';
export type WorkspaceHalfSide = 'left' | 'right';

export interface WorkspaceSizes {
  left: number;
  right: number;
  bottom: number;
}

export interface WorkspaceViewport {
  width: number;
  height: number;
}

export interface WorkspaceFocus {
  pane: WorkspacePaneId;
  mode: WorkspaceFocusMode;
  side?: WorkspaceHalfSide;
}

export interface WorkspaceLayoutState {
  version: 2;
  slots: Record<WorkspaceSlotId, WorkspacePaneId>;
  minimized: WorkspacePaneId[];
  sizes: WorkspaceSizes;
  locked: boolean;
}

export type WorkspaceLayout = WorkspaceLayoutState;

export type WorkspacePresetId = 'classic' | 'chart' | 'execution';

export const WORKSPACE_LAYOUT_KEY = 'sterling:kite-workspace:v2';
export const WORKSPACE_PANES: WorkspacePaneId[] = ['watchlist', 'dashboard', 'signals', 'terminal'];
export const WORKSPACE_SLOTS: WorkspaceSlotId[] = ['left', 'center', 'right', 'bottom'];

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayoutState = {
  version: 2,
  slots: {
    left: 'watchlist',
    center: 'dashboard',
    right: 'signals',
    bottom: 'terminal',
  },
  minimized: [],
  // Classic mirrors Sterling's original signals-forward desktop workspace:
  // a comfortable watchlist, a compact main pane, and a wide execution pane.
  sizes: { left: 420, right: 1290, bottom: 220 },
  locked: false,
};

const PRESET_SIZES: Record<WorkspacePresetId, WorkspaceSizes> = {
  classic: DEFAULT_WORKSPACE_LAYOUT.sizes,
  chart: { left: 280, right: 720, bottom: 220 },
  execution: { left: 260, right: 700, bottom: 280 },
};

const CHART_FOCUS_SLOTS: Record<WorkspaceSlotId, WorkspacePaneId> = {
  // The terminal keeps a real slot so restoring it remains predictable, but the
  // preset docks it in the footer. That leaves a true two-column workspace:
  // chart + watchlist on the left and an uninterrupted signal board on the right.
  left: 'terminal',
  center: 'dashboard',
  right: 'signals',
  bottom: 'watchlist',
};

const isPane = (value: unknown): value is WorkspacePaneId =>
  typeof value === 'string' && WORKSPACE_PANES.includes(value as WorkspacePaneId);

function finite(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function uniquePanes(values: unknown): WorkspacePaneId[] {
  if (!Array.isArray(values)) return [];
  return [...new Set(values.filter(isPane))];
}

/**
 * Parse persisted workspace data defensively. Every pane is guaranteed to occur
 * exactly once in the returned slot map, even when storage was manually edited,
 * produced by an older build, or only partially written.
 */
export function sanitizeWorkspaceLayout(raw: unknown): WorkspaceLayoutState {
  if (!raw || typeof raw !== 'object') return cloneDefaultWorkspaceLayout();
  const candidate = raw as Partial<WorkspaceLayoutState>;
  const rawSlots = candidate.slots && typeof candidate.slots === 'object'
    ? candidate.slots as Partial<Record<WorkspaceSlotId, unknown>>
    : {};
  const used = new Set<WorkspacePaneId>();
  const slots = {} as Record<WorkspaceSlotId, WorkspacePaneId>;

  for (const slot of WORKSPACE_SLOTS) {
    const pane = rawSlots[slot];
    if (isPane(pane) && !used.has(pane)) {
      slots[slot] = pane;
      used.add(pane);
      continue;
    }
    const fallback = WORKSPACE_PANES.find((id) => !used.has(id))!;
    slots[slot] = fallback;
    used.add(fallback);
  }

  const sizes = candidate.sizes && typeof candidate.sizes === 'object'
    ? candidate.sizes as Partial<WorkspaceSizes>
    : {};

  return {
    version: 2,
    slots,
    minimized: uniquePanes(candidate.minimized),
    sizes: {
      left: finite(sizes.left, DEFAULT_WORKSPACE_LAYOUT.sizes.left),
      right: finite(sizes.right, DEFAULT_WORKSPACE_LAYOUT.sizes.right),
      bottom: finite(sizes.bottom, DEFAULT_WORKSPACE_LAYOUT.sizes.bottom),
    },
    locked: candidate.locked === true,
  };
}

export function cloneDefaultWorkspaceLayout(): WorkspaceLayoutState {
  return {
    ...DEFAULT_WORKSPACE_LAYOUT,
    slots: { ...DEFAULT_WORKSPACE_LAYOUT.slots },
    minimized: [],
    sizes: { ...DEFAULT_WORKSPACE_LAYOUT.sizes },
  };
}

export function paneSlot(
  state: Pick<WorkspaceLayoutState, 'slots'>,
  pane: WorkspacePaneId,
): WorkspaceSlotId {
  return WORKSPACE_SLOTS.find((slot) => state.slots[slot] === pane) ?? 'center';
}

/** Move a pane by swapping it with the pane already occupying the target slot. */
export function movePaneToSlot(
  state: WorkspaceLayoutState,
  pane: WorkspacePaneId,
  target: WorkspaceSlotId,
): WorkspaceLayoutState {
  const source = paneSlot(state, pane);
  if (source === target) return state;
  const displaced = state.slots[target];
  return {
    ...state,
    slots: {
      ...state.slots,
      [source]: displaced,
      [target]: pane,
    },
  };
}

export function minimizePane(state: WorkspaceLayoutState, pane: WorkspacePaneId): WorkspaceLayoutState {
  if (state.minimized.includes(pane)) return state;
  return { ...state, minimized: [...state.minimized, pane] };
}

export function restorePane(state: WorkspaceLayoutState, pane: WorkspacePaneId): WorkspaceLayoutState {
  if (!state.minimized.includes(pane)) return state;
  return { ...state, minimized: state.minimized.filter((id) => id !== pane) };
}

export function restoreAllPanes(state: WorkspaceLayoutState): WorkspaceLayoutState {
  return state.minimized.length ? { ...state, minimized: [] } : state;
}

/**
 * Clamp splitter sizes against both ergonomic pane minimums and the current
 * workspace. Sidebars share the available width while reserving a useful center.
 */
export function clampWorkspaceSizes(
  sizes: WorkspaceSizes,
  viewport: { width: number; height: number },
  visible: { left: boolean; right: boolean; bottom: boolean } = { left: true, right: true, bottom: true },
): WorkspaceSizes {
  const width = Math.max(320, finite(viewport.width, 1280));
  const height = Math.max(240, finite(viewport.height, 800));
  const minCenter = Math.min(420, Math.max(260, width * 0.28));
  const minSide = Math.min(240, width * 0.2);
  const maxSide = Math.max(minSide, width - minCenter);
  let left = Math.min(maxSide, Math.max(minSide, finite(sizes.left, DEFAULT_WORKSPACE_LAYOUT.sizes.left)));
  let right = Math.min(maxSide, Math.max(minSide, finite(sizes.right, DEFAULT_WORKSPACE_LAYOUT.sizes.right)));

  if (visible.left && visible.right) {
    const allowed = Math.max(minSide * 2, width - minCenter);
    const combined = left + right;
    if (combined > allowed) {
      const scale = allowed / combined;
      left = Math.max(minSide, left * scale);
      right = Math.max(minSide, right * scale);
      const excess = left + right - allowed;
      if (excess > 0) {
        if (left >= right) left -= excess;
        else right -= excess;
      }
    }
  }

  const minBottom = Math.min(110, height * 0.2);
  const maxBottom = Math.max(minBottom, height - Math.min(300, height * 0.42));
  const bottom = Math.min(maxBottom, Math.max(minBottom, finite(sizes.bottom, DEFAULT_WORKSPACE_LAYOUT.sizes.bottom)));

  return {
    left: Math.round(left),
    right: Math.round(right),
    bottom: Math.round(bottom),
  };
}

export function applyWorkspacePreset(
  state: WorkspaceLayoutState,
  preset: WorkspacePresetId,
  viewport?: WorkspaceViewport,
): WorkspaceLayoutState {
  const next = cloneDefaultWorkspaceLayout();
  if (preset === 'chart') {
    next.slots = { ...CHART_FOCUS_SLOTS };
    next.minimized = ['terminal'];
    if (viewport) {
      const width = Math.max(320, finite(viewport.width, 1440));
      const height = Math.max(240, finite(viewport.height, 920));
      next.sizes = {
        left: PRESET_SIZES.chart.left,
        right: Math.round(width * 0.5),
        bottom: Math.round(Math.min(260, Math.max(180, height * 0.24))),
      };
    } else {
      next.sizes = { ...PRESET_SIZES.chart };
    }
  } else {
    next.sizes = { ...PRESET_SIZES[preset] };
  }
  return {
    ...next,
    locked: state.locked,
  };
}

export interface StorageReader {
  getItem(key: string): string | null;
}

/** Load v2 data, or migrate the fixed-pane v1 localStorage keys in-place. */
export function loadWorkspaceLayout(storage: StorageReader): WorkspaceLayoutState {
  try {
    const saved = storage.getItem(WORKSPACE_LAYOUT_KEY);
    if (saved) return sanitizeWorkspaceLayout(JSON.parse(saved));
  } catch {
    // Continue with legacy migration/defaults.
  }

  const next = cloneDefaultWorkspaceLayout();
  const numberFrom = (key: string, fallback: number) => {
    const value = Number(storage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  };
  next.sizes = {
    left: numberFrom('kite_sidebar_width', next.sizes.left),
    right: numberFrom('kite_right_sidebar_width', next.sizes.right),
    bottom: numberFrom('kite_bottombar_height', next.sizes.bottom),
  };
  if (storage.getItem('kite_sidebar_open') === 'false') next.minimized.push('watchlist');
  if (storage.getItem('kite_right_sidebar_open') === 'false') next.minimized.push('signals');
  if (storage.getItem('kite_bottombar_open') === 'false' || storage.getItem('kite_terminal_mode') === 'minimized') {
    next.minimized.push('terminal');
  }
  next.locked = storage.getItem('kite_layout_locked') === 'true';
  return sanitizeWorkspaceLayout(next);
}
