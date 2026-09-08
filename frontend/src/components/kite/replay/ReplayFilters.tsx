import React, { useRef, useState } from 'react';
import { useReplayState, useReplayStore } from '../../../hooks/useReplayStore';
import { ReplayPopover } from './primitives/ReplayPopover';
import * as Icons from './ReplayIcons';

/**
 * Filter & Execution Settings Popover.
 *
 * Houses:
 * 1. Execution model (Realistic vs Ideal, index/stock spreads, slippage)
 * 2. Advanced: Bar resolution (1m, 3m, 5m, etc.)
 * 3. Reset to defaults
 *
 * Session date, market hours, strategies, and position sizing are now integrated
 * directly as Source/Exit-style dropdowns in the bar itself. Instruments are removed.
 */
export function ReplayFilters() {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const caps = useReplayStore((s) => s.status.capabilities);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);

  const locked = state !== 'idle';
  const frictionSupported = caps?.friction !== false;
  const resolutions = caps?.resolutions ?? ['1m', '3m', '5m', '15m'];

  const isDefaultSettings =
    draft.frictionMode === 'realistic' &&
    draft.indexSpreadPct === 0.5 &&
    draft.stockSpreadPct === 1.5 &&
    draft.slippagePct === 0.25 &&
    draft.resolution === '5m';

  return (
    <>
      <button
        type="button"
        ref={anchor}
        className="rd-btn"
        disabled={locked}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title={locked ? 'Stop the replay to change execution settings' : 'Execution model and advanced settings'}
        data-testid="replay-filters-trigger"
      >
        <Icons.Filter size={13} />
        Settings
        <Icons.ChevronDown size={11} />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Execution and engine settings"
        anchorRef={anchor}
        width={300}
        align="end"
      >
        {/* ── Execution model ─────────────────────────────────────── */}
        <div className="rd-pop-section">
          <div className="rd-pop-head">Execution model</div>
          {frictionSupported ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label className="rd-opt" style={{ width: 'auto' }}>
                <input
                  type="radio"
                  name="rd-pop-friction"
                  checked={draft.frictionMode === 'realistic'}
                  onChange={() => setDraft({ frictionMode: 'realistic' })}
                />
                <span>Realistic — buy at ask, sell at bid</span>
              </label>
              <label className="rd-opt" style={{ width: 'auto' }}>
                <input
                  type="radio"
                  name="rd-pop-friction"
                  checked={draft.frictionMode === 'ideal'}
                  onChange={() => setDraft({ frictionMode: 'ideal' })}
                />
                <span>Ideal — fills at signal price</span>
              </label>

              {draft.frictionMode === 'realistic' && (
                <div className="rd-grid-2" style={{ marginTop: 8 }}>
                  <div className="rd-field">
                    <label className="rd-field-label" htmlFor="rd-idx-spread">Index spread %</label>
                    <input
                      id="rd-idx-spread"
                      type="number"
                      step="0.05"
                      min={0}
                      max={20}
                      className="rd-input"
                      value={draft.indexSpreadPct}
                      onChange={(e) => setDraft({ indexSpreadPct: Number(e.target.value) })}
                    />
                  </div>
                  <div className="rd-field">
                    <label className="rd-field-label" htmlFor="rd-stk-spread">Stock spread %</label>
                    <input
                      id="rd-stk-spread"
                      type="number"
                      step="0.05"
                      min={0}
                      max={20}
                      className="rd-input"
                      value={draft.stockSpreadPct}
                      onChange={(e) => setDraft({ stockSpreadPct: Number(e.target.value) })}
                    />
                  </div>
                  <div className="rd-field" style={{ gridColumn: '1 / -1' }}>
                    <label className="rd-field-label" htmlFor="rd-slip">Slippage % (each leg)</label>
                    <input
                      id="rd-slip"
                      type="number"
                      step="0.01"
                      min={0}
                      max={5}
                      className="rd-input"
                      value={draft.slippagePct}
                      onChange={(e) => setDraft({ slippagePct: Number(e.target.value) })}
                    />
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ fontSize: '11px', color: 'var(--k-dim)' }}>
              Friction and spread modelling is not supported by the current engine.
            </div>
          )}
        </div>

        {/* ── Advanced: Bar resolution ─────────────────────────────── */}
        <div className="rd-pop-section">
          <div className="rd-pop-head">Bar resolution</div>
          <div className="rd-chip-row">
            {resolutions.map((r) => (
              <button
                key={r}
                type="button"
                className="rd-btn rd-btn-sm"
                aria-pressed={draft.resolution === r}
                data-variant={draft.resolution === r ? 'primary' : undefined}
                onClick={() => setDraft({ resolution: r })}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* ── Footer ──────────────────────────────────────────────── */}
        <div className="rd-pop-footer" style={{ padding: '8px 12px', borderTop: '1px solid var(--k-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            disabled={isDefaultSettings}
            onClick={() => {
              setDraft({
                frictionMode: 'realistic',
                indexSpreadPct: 0.5,
                stockSpreadPct: 1.5,
                slippagePct: 0.25,
                resolution: '5m',
              });
            }}
            style={isDefaultSettings ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
            title={isDefaultSettings ? 'Settings are at default values' : 'Reset execution settings to defaults'}
          >
            Reset to defaults
          </button>
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            onClick={() => setOpen(false)}
          >
            Done
          </button>
        </div>
      </ReplayPopover>
    </>
  );
}

/** The applied narrowings chip display (now simplified) */
export function ReplayFilterChips() {
  return null;
}
