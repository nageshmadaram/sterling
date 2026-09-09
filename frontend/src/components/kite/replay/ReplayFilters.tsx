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
    draft.resolution === '5m' &&
    (draft.adaptiveVersion ?? 'v2_hardened') === 'v2_hardened' &&
    (draft.adaptiveSource ?? 'both') === 'both';

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
        width={340}
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
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') setDraft({ indexSpreadPct: 0 });
                        else {
                          const n = parseFloat(v);
                          if (!isNaN(n)) setDraft({ indexSpreadPct: Math.max(0, Math.min(20, n)) });
                        }
                      }}
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
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') setDraft({ stockSpreadPct: 0 });
                        else {
                          const n = parseFloat(v);
                          if (!isNaN(n)) setDraft({ stockSpreadPct: Math.max(0, Math.min(20, n)) });
                        }
                      }}
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
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') setDraft({ slippagePct: 0 });
                        else {
                          const n = parseFloat(v);
                          if (!isNaN(n)) setDraft({ slippagePct: Math.max(0, Math.min(5, n)) });
                        }
                      }}
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

        {/* ── Adaptive Edge Engine Version ─────────────────────────── */}
        <div className="rd-pop-section">
          <div className="rd-pop-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Adaptive Edge Engine</span>
            <span style={{ fontSize: '10px', color: 'var(--k-dim)', fontWeight: 600 }}>
              {(draft.adaptiveVersion ?? 'v2_hardened') === 'v2_hardened' ? '🛡️ V2 Hardened' : '🕰️ V1 Legacy'}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label className="rd-opt" style={{ width: 'auto', alignItems: 'flex-start' }}>
              <input
                type="radio"
                name="rd-pop-ae-version"
                checked={(draft.adaptiveVersion ?? 'v2_hardened') === 'v2_hardened'}
                onChange={() => setDraft({ adaptiveVersion: 'v2_hardened' })}
                style={{ marginTop: 2 }}
              />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                <strong style={{ fontSize: '11.5px', color: 'var(--k-text)' }}>V2 Hardened (Production)</strong>
                <span style={{ fontSize: '10px', color: 'var(--k-dim)', lineHeight: 1.3 }}>
                  09:28 lockout, candle-body filter, 1.5R 50/50 partial scale, 4-bar decay stop
                </span>
              </span>
            </label>
            <label className="rd-opt" style={{ width: 'auto', alignItems: 'flex-start' }}>
              <input
                type="radio"
                name="rd-pop-ae-version"
                checked={draft.adaptiveVersion === 'v1_baseline'}
                onChange={() => setDraft({ adaptiveVersion: 'v1_baseline' })}
                style={{ marginTop: 2 }}
              />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                <strong style={{ fontSize: '11.5px', color: 'var(--k-text)' }}>V1 Legacy (Baseline)</strong>
                <span style={{ fontSize: '10px', color: 'var(--k-dim)', lineHeight: 1.3 }}>
                  Unrestricted 09:15 entries, all-or-nothing stop loss, standard volume surge
                </span>
              </span>
            </label>
          </div>
        </div>

        {/* ── Adaptive Edge Signal Source ──────────────────────────── */}
        <div className="rd-pop-section">
          <div className="rd-pop-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Adaptive Edge Source</span>
            <span style={{ fontSize: '10px', color: 'var(--k-dim)', fontWeight: 600 }}>
              {(draft.adaptiveSource ?? 'both') === 'both'
                ? '🌐 Both (Scan + Model)'
                : draft.adaptiveSource === 'ae_model'
                  ? '🧠 AE Model Only'
                  : '🔍 Spot Scan Only'}
            </span>
          </div>
          <div className="rd-chip-row">
            {[
              { id: 'both', label: 'Both' },
              { id: 'ae_model', label: 'AE Model' },
              { id: 'spot_scan', label: 'Spot Scan' },
            ].map((opt) => (
              <button
                key={opt.id}
                type="button"
                className="rd-btn rd-btn-sm"
                aria-pressed={(draft.adaptiveSource ?? 'both') === opt.id}
                data-variant={(draft.adaptiveSource ?? 'both') === opt.id ? 'primary' : undefined}
                onClick={() => setDraft({ adaptiveSource: opt.id as 'both' | 'ae_model' | 'spot_scan' })}
              >
                {opt.label}
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
                adaptiveVersion: 'v2_hardened',
                adaptiveSource: 'both',
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


