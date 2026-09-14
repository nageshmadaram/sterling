import { useQueryClient } from '@tanstack/react-query';
import {
  useEngineConfig, usePatchEngineConfig, useRunScan,
} from '../../../hooks/useSterlingKiteEngine';
import { notifyOrder } from '../../../store/useKiteNotifications';
import type { EngineConfigModel } from '../../../types/kiteEngine';
import { FIELDS, type FieldKey } from './registry';

/**
 * The one way to change a Kite engine setting.
 *
 * Every surface used to roll its own `patch()`: the settings panel defaulted to
 * no rescan, the shared panel defaulted to always rescan, and the board header
 * passed the flag by hand — so `exit_mode` refreshed the board when changed on
 * one page and left it stale when changed on another. Here the decision comes
 * from the field's own registry entry, so the same change behaves the same way
 * wherever the user made it.
 *
 * It also invalidates the derived queries a config change makes stale. The old
 * mutation only wrote the config cache, which left an open setup chart and the
 * detail dock quoting the previous trail and exit rule until their own timers
 * caught up.
 */
export function useConfigPatch() {
  const { data: cfg } = useEngineConfig();
  const setCfg = usePatchEngineConfig();
  const runScan = useRunScan();
  const qc = useQueryClient();

  /**
   * Apply one or more registry-described fields.
   *
   * @param values  the fields to change
   * @param keys    which registry entries these are. Omit it and the keys are
   *                read off `values` itself, which is the safer default for a
   *                caller that patches a variable set of fields — naming one
   *                key by hand would apply that field's rescan policy to all of
   *                them.
   */
  const patch = (
    values: Partial<EngineConfigModel>,
    keys?: FieldKey | FieldKey[],
    messageOverride?: string,
  ) => {
    if (!cfg) return;
    const named = keys === undefined
      ? (Object.keys(values) as FieldKey[])
      : (Array.isArray(keys) ? keys : [keys]);
    const list = named.filter((key) => key in FIELDS);
    const rescan = list.some((key) => FIELDS[key].rescan);
    const message = messageOverride
      ?? (list.length === 1 ? `${FIELDS[list[0]].label} updated` : 'Settings updated');

    // Send ONLY what changed. Spreading `cfg` here would re-assert this
    // component's cached idea of every other field and revert anything that
    // moved since it was fetched.
    setCfg.mutate(values, {
      onSuccess: () => {
        notifyOrder({ kind: 'info', title: 'Settings updated', message });
        // A trail/exit change alters what the setup chart and the detail dock
        // should be drawing, so drop those caches rather than let them keep
        // quoting the previous rule.
        qc.invalidateQueries({ queryKey: ['kite-engine-setup'] });
        qc.invalidateQueries({ queryKey: ['kite-engine-detail'] });
        if (rescan) runScan.mutate();
        else qc.invalidateQueries({ queryKey: ['kite-engine-signals'] });
      },
      onError: (err) => {
        notifyOrder({
          kind: 'error',
          title: 'Settings NOT saved',
          message: `Could not apply settings: ${err?.message || 'save rejected'}.`,
        });
      },
    });
  };

  return { cfg, patch, saving: setCfg.isPending, rescanning: runScan.isPending };
}

/** Toggle a value in a list setting, never leaving the list empty. */
export function toggleInList<T extends string>(current: T[], value: T, fallback: T[]): T[] {
  const next = current.includes(value)
    ? current.filter((item) => item !== value)
    : [...current, value];
  return next.length ? next : fallback;
}
