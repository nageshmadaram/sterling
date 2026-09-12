import React from 'react';
import { ENGINE_ORDER, useKiteSettings } from '../../store/useKiteSettings';
import { ENGINE_LABEL, type EngineId } from './board/boardTypes';
import { useEngineEnabled } from '../../hooks/useEngineToggles';
import { SettingsDraftBar } from './config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';

/**
 * Which signal board opens first, and what order the engine tabs sit in.
 *
 * One preference about one list, so one card. The board used to hardcode
 * `useState('supertrend')` and render the tabs in whatever order the array was
 * written in — an operator who trades one engine paid a click on every visit
 * and read four tabs they never use before reaching theirs.
 *
 * A switched-OFF engine still appears here, dimmed. It is a preference about
 * order, not about what runs, and hiding an engine from the list would mean the
 * order silently rearranges itself the moment someone toggles one on.
 */
const HINT: Partial<Record<EngineId, string>> = {
  supertrend: 'Triple SuperTrend on spot and premium charts.',
  navigator: 'AVWAP structure, ranges and options flow.',
  adaptive_edge: 'Order-flow scalping. Signals only.',
  gamma_move: 'Open-interest unwind at a confirmed level.',
  intraday: 'Pivot break, MA ribbon and VWAP SuperTrend on 5-minute candles.',
  snapback: 'Fades a stretched breakout on daily bars. Measured negative.',
};

const card: React.CSSProperties = {
  marginBottom: 16, padding: 18, background: 'var(--k-bg)',
  border: '1px solid var(--k-border)', borderRadius: 9,
  boxShadow: '0 1px 2px rgba(0,0,0,.025)',
};

export function SignalEngineOrderSettings() {
  const storedDefault = useKiteSettings((s) => s.defaultSignalEngine || 'supertrend');
  const storedOrder = useKiteSettings((s) => s.engineOrder);
  const setDefault = useKiteSettings((s) => s.setDefaultSignalEngine);
  const setOrder = useKiteSettings((s) => s.setEngineOrder);
  const enabled = useEngineEnabled();

  const [draftDefault, setDraftDefault] = React.useState<string | null>(null);
  const [draftOrder, setDraftOrder] = React.useState<string[] | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const current = draftDefault ?? storedDefault;
  const order = draftOrder ?? (storedOrder.length ? storedOrder : ENGINE_ORDER);
  const dirty = draftDefault != null || draftOrder != null;
  useUnsavedDraftGuard('signalEngineOrder', dirty);

  // Every engine the board knows, in the working order, with anything the
  // preference has not heard of appended rather than dropped.
  const list = React.useMemo(() => {
    const seen = new Set(order);
    return [...order.filter((id) => ENGINE_ORDER.includes(id)),
            ...ENGINE_ORDER.filter((id) => !seen.has(id))];
  }, [order]);

  const move = (id: string, delta: number) => {
    const next = [...list];
    const from = next.indexOf(id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= next.length) return;
    next.splice(to, 0, next.splice(from, 1)[0]);
    setDraftOrder(next);
  };

  const apply = () => {
    if (draftDefault != null) setDefault(draftDefault);
    if (draftOrder != null) setOrder(draftOrder);
    setDraftDefault(null);
    setDraftOrder(null);
  };
  const discard = () => { setDraftDefault(null); setDraftOrder(null); };
  const reset = () => {
    if (!resetConfirm) { setResetConfirm(true); return; }
    setDraftDefault('supertrend');
    setDraftOrder([...ENGINE_ORDER]);
    setResetConfirm(false);
  };

  return (
    <>
      <SettingsDraftBar
        dirty={dirty}
        onApply={apply}
        onDiscard={discard}
        onReset={reset}
        resetConfirm={resetConfirm}
        hasDraft={dirty}
      />
      <section style={card}>
        <div style={{ fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase',
                      color: 'var(--k-ink-5)', marginBottom: 4 }}>
          Signal board
        </div>
        <div style={{ color: 'var(--k-ink-5)', fontSize: 11, lineHeight: 1.55,
                      marginBottom: 14 }}>
          Which engine&rsquo;s table opens first, and the order the tabs sit in.
          A switched-off engine stays listed so the order does not rearrange
          itself when you turn one on.
        </div>

        <div role="radiogroup" aria-label="Default signal table">
          {list.map((id, i) => {
            const engineId = id as EngineId;
            const on = enabled[engineId as keyof typeof enabled] !== false;
            const isDefault = current === id;
            return (
              <div
                key={id}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 10px', marginBottom: 6, borderRadius: 7,
                  border: `1px solid ${isDefault ? 'var(--k-orange)' : 'var(--k-border)'}`,
                  background: isDefault
                    ? 'color-mix(in srgb, var(--k-orange) 7%, transparent)'
                    : 'transparent',
                  opacity: on ? 1 : 0.55,
                }}
              >
                <span style={{ color: 'var(--k-ink-5)', fontSize: 11, width: 16,
                               fontVariantNumeric: 'tabular-nums' }}>
                  {i + 1}
                </span>
                <button
                  type="button"
                  role="radio"
                  aria-checked={isDefault}
                  aria-label={`Open ${ENGINE_LABEL[engineId] ?? id} first`}
                  onClick={() => setDraftDefault(id)}
                  style={{
                    flex: 1, textAlign: 'left', background: 'transparent',
                    border: 'none', cursor: 'pointer', padding: 0,
                    color: 'var(--k-text)', fontSize: 12,
                  }}
                >
                  <span style={{ fontWeight: isDefault ? 600 : 400 }}>
                    {ENGINE_LABEL[engineId] ?? id}
                  </span>
                  {isDefault && (
                    <span style={{ color: 'var(--k-orange)', fontSize: 10,
                                   marginLeft: 8 }}>OPENS FIRST</span>
                  )}
                  {!on && (
                    <span style={{ color: 'var(--k-ink-5)', fontSize: 10,
                                   marginLeft: 8 }}>off</span>
                  )}
                  <div style={{ color: 'var(--k-ink-5)', fontSize: 10.5,
                                marginTop: 2 }}>
                    {HINT[engineId] ?? ''}
                  </div>
                </button>
                <button
                  type="button"
                  aria-label={`Move ${ENGINE_LABEL[engineId] ?? id} up`}
                  disabled={i === 0}
                  onClick={() => move(id, -1)}
                  style={arrow(i === 0)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  aria-label={`Move ${ENGINE_LABEL[engineId] ?? id} down`}
                  disabled={i === list.length - 1}
                  onClick={() => move(id, 1)}
                  style={arrow(i === list.length - 1)}
                >
                  ↓
                </button>
              </div>
            );
          })}
        </div>

        <div style={{ color: 'var(--k-ink-5)', fontSize: 10.5, lineHeight: 1.5,
                      marginTop: 8 }}>
          If the engine you pick is switched off, the board opens the first one
          that is on — a tab that is not rendered cannot be selected, and
          landing on an empty pane would look like a fault.
        </div>
      </section>
    </>
  );
}

const arrow = (disabled: boolean): React.CSSProperties => ({
  width: 26, height: 26, borderRadius: 6, cursor: disabled ? 'default' : 'pointer',
  border: '1px solid var(--k-border)', background: 'transparent',
  color: disabled ? 'var(--k-ink-5)' : 'var(--k-text)',
  opacity: disabled ? 0.4 : 1, fontSize: 12, lineHeight: 1,
});

export default SignalEngineOrderSettings;
