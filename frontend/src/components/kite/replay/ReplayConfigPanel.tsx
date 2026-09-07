import React, { useRef } from 'react';
import { useReplayStore } from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { useFocusTrap } from './primitives/useFocusTrap';
import { fmtSessionDate, fmtTime } from './replayFormat';
import { MONEYNESS_LEGS, REPLAY_STRATEGIES } from './replayStrategies';
import { CORE_INDICES, CORE_STOCKS, MONEYNESS_LEGS, REPLAY_STRATEGIES } from './replayStrategies';
import { useAvailableDates, verdictForDate } from './useAvailableDates';
import * as Icons from './ReplayIcons';

const LOT_PRESETS = [1, 2, 5, 10, 25, 50];

/**
 * Configuration, as a sheet rather than a tab.
 *
 * It is used once per session, so it should not hold a quarter of the chrome
 * while a replay runs. Making the ENTRY POINT disabled while running is also
 * one disabled state instead of the nineteen the tab carried.
 */
export function ReplayConfigSheet() {
  const open = useReplayStore((s) => s.configOpen);
  const setOpen = useReplayStore((s) => s.setConfigOpen);
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const resetDraft = useReplayStore((s) => s.resetDraft);
  const toggleStrategy = useReplayStore((s) => s.toggleStrategy);
  const toggleMoneyness = useReplayStore((s) => s.toggleMoneyness);
  const toggleInstrument = useReplayStore((s) => s.toggleInstrument);
  const caps = useReplayStore((s) => s.status.capabilities);
  const echo = useReplayStore((s) => s.status.config);
  const transport = useReplayTransport();

  const ref = useRef<HTMLDivElement>(null);
  const { data: available } = useAvailableDates();
  useFocusTrap(ref, open, { onEscape: () => setOpen(false) });

  if (!open) return null;

  const verdict = verdictForDate(draft.date, available);
  const rangeInvalid = draft.endDate < draft.date;
  const timesInvalid = draft.endTime <= draft.startTime;
  const lotsInvalid = draft.lots < 1 || draft.lots > 500;
  const blocked = rangeInvalid || timesInvalid || lotsInvalid || verdict.level === 'error';

  const allStrategies = draft.strategies.includes('all');
  const allLegs = draft.moneyness.includes('ALL');
  const allInstruments = !draft.instruments || draft.instruments.length === 0;
  const frictionSupported = caps?.friction === true;

  // The engine's own values, if it reported them. A mismatch here is how the
  // next silently-ignored config field gets caught.
  const echoMismatch =
    frictionSupported && echo?.friction_mode != null && echo.friction_mode !== draft.frictionMode;

  /* Lives INSIDE the dock and lays its cards out horizontally.
     A vertical drawer fought the dock's height: on a ~300px docked replay the
     cards had nowhere to go. Across the dock's width there is plenty of room,
     so the cards become columns and the panel scrolls sideways if they run out
     of it. */
  return (
    <>
      <div className="rd-sheet-scrim" onMouseDown={() => setOpen(false)} />
      <div
        ref={ref}
        className="rd-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Replay configuration"
        data-testid="replay-config-sheet"
      >
        <header className="rd-sheet-head">
          <Icons.Config size={14} />
          Replay configuration
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            style={{ marginLeft: 'auto' }}
            onClick={() => setOpen(false)}
            aria-label="Close configuration"
          >
            <Icons.Close size={12} />
          </button>
        </header>

        <div className="rd-sheet-body">
          {/* Session is read-only here. It is edited in ONE place — the picker —
              because two editors for one value is how they drift apart. */}
          <details className="rd-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Session</span>
                <span className="rd-card-desc">Edited in the session picker on the view bar.</span>
              </span>
              <span className="rd-card-meta">{fmtSessionDate(draft.date, true)}</span>
            </summary>
            <div className="rd-card-body">
              <div className="rd-note">
                {fmtSessionDate(draft.date)} · {fmtTime(draft.startTime, 5)}–{fmtTime(draft.endTime, 5)}
                {draft.endDate !== draft.date && ` · through ${fmtSessionDate(draft.endDate, true)}`}
              </div>
              {verdict.level === 'warn' && <div className="rd-field-warn">{verdict.message}</div>}
              {rangeInvalid && <div className="rd-field-error">End date is before the start date.</div>}
              {timesInvalid && <div className="rd-field-error">End time must be after the start time.</div>}
              {draft.endDate !== draft.date && caps?.multi_day === false && (
                <div className="rd-field-warn">
                  This engine replays one session at a time; a multi-day range will be refused.
                </div>
              )}
            </div>
          </details>

          <details className="rd-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Strategies</span>
                <span className="rd-card-desc">Which strategies emit signals during the replay.</span>
              </span>
              <span className="rd-card-meta">{allStrategies ? 'ALL' : `${draft.strategies.length}`}</span>
            </summary>
            <div className="rd-card-body">
              <label className="rd-opt">
                <input
                  type="checkbox"
                  checked={allStrategies}
                  onChange={() => setDraft({ strategies: ['all'] })}
                  onChange={() => {
                    if (allStrategies) {
                      setDraft({ strategies: [REPLAY_STRATEGIES[0].id] });
                    } else {
                      setDraft({ strategies: ['all'] });
                    }
                  }}
                />
                <span>All strategies</span>
              </label>
              {REPLAY_STRATEGIES.map((s) => (
                <label className="rd-opt" key={s.id}>
                  <input
                    type="checkbox"
                    checked={allStrategies || draft.strategies.includes(s.id)}
                    onChange={() => toggleStrategy(s.id)}
                  />
                  <span style={{ color: s.tone, display: 'inline-flex' }}>
                    <span className="rd-dot-tone" />
                  </span>
                  <span>{s.label}</span>
                </label>
              ))}
            </div>
          </details>

          <details className="rd-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Instruments</span>
                <span className="rd-card-desc">Indices and stocks scanned during replay.</span>
              </span>
              <span className="rd-card-meta">{allInstruments ? 'ALL' : `${draft.instruments.length}`}</span>
            </summary>
            <div className="rd-card-body">
              <label className="rd-opt">
                <input
                  type="checkbox"
                  checked={allInstruments}
                  onChange={() => {
                    if (!allInstruments) {
                      setDraft({ instruments: [] });
                    }
                  }}
                />
                <span>All instruments (Full Universe)</span>
              </label>
              <div style={{ maxHeight: 180, overflowY: 'auto' }}>
                <div style={{ fontSize: '10px', color: 'var(--k-dim)', padding: '4px 0 2px', textTransform: 'uppercase' }}>
                  Indices
                </div>
                {CORE_INDICES.map((inst) => (
                  <label className="rd-opt" key={inst.id}>
                    <input
                      type="checkbox"
                      checked={allInstruments || draft.instruments.includes(inst.id)}
                      onChange={() => toggleInstrument(inst.id)}
                    />
                    <span>{inst.id} <span style={{ color: 'var(--k-dim)', fontSize: '11px' }}>({inst.label})</span></span>
                  </label>
                ))}
                <div style={{ fontSize: '10px', color: 'var(--k-dim)', padding: '6px 0 2px', textTransform: 'uppercase' }}>
                  Stocks
                </div>
                {CORE_STOCKS.map((inst) => (
                  <label className="rd-opt" key={inst.id}>
                    <input
                      type="checkbox"
                      checked={allInstruments || draft.instruments.includes(inst.id)}
                      onChange={() => toggleInstrument(inst.id)}
                    />
                    <span>{inst.id}</span>
                  </label>
                ))}
              </div>
            </div>
          </details>

          <details className="rd-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Position sizing</span>
                <span className="rd-card-desc">Strike selection and order size.</span>
              </span>
              <span className="rd-card-meta">{allLegs ? 'ALL' : draft.moneyness.join(', ')} · {draft.lots}L</span>
            </summary>
            <div className="rd-card-body">
              <div className="rd-chip-row">
                <button
                  type="button"
                  className="rd-btn rd-btn-sm"
                  data-variant={allLegs ? 'primary' : undefined}
                  onClick={() => setDraft({ moneyness: ['ALL'] })}
                >
                  All legs
                </button>
                {MONEYNESS_LEGS.map((leg) => (
                  <button
                    key={leg.id}
                    type="button"
                    className="rd-btn rd-btn-sm"
                    title={leg.hint}
                    aria-pressed={allLegs || draft.moneyness.includes(leg.id)}
                    data-variant={!allLegs && draft.moneyness.includes(leg.id) ? 'primary' : undefined}
                    onClick={() => toggleMoneyness(leg.id)}
                  >
                    {leg.label}
                  </button>
                ))}
              </div>

              <div className="rd-field">
                <span className="rd-field-label">Lots</span>
                <div className="rd-chip-row">
                  {LOT_PRESETS.map((l) => (
                    <button
                      key={l}
                      type="button"
                      className="rd-btn rd-btn-sm"
                      aria-pressed={draft.lots === l}
                      data-variant={draft.lots === l ? 'primary' : undefined}
                      onClick={() => setDraft({ lots: l })}
                    >
                      {l}L
                    </button>
                  ))}
                  <input
                    type="number"
                    className="rd-input"
                    style={{ width: 76 }}
                    min={1}
                    max={500}
                    value={draft.lots}
                    aria-label="Lots"
                    aria-invalid={lotsInvalid}
                    onChange={(e) => setDraft({ lots: Number(e.target.value) })}
                  />
                </div>
                {lotsInvalid && <div className="rd-field-error">Lots must be between 1 and 500.</div>}
              </div>
            </div>
          </details>

          {/* The section that used to lie. It now either drives a real model or
              says plainly that there is none. */}
          <details className="rd-card" data-disabled={!frictionSupported}>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Execution model</span>
                <span className="rd-card-desc">
                  {frictionSupported
                    ? 'How fills are priced against the signal price.'
                    : 'Not available in this build of the replay engine.'}
                </span>
              </span>
              <span className="rd-card-meta" data-tone={frictionSupported ? undefined : 'off'}>
                {frictionSupported
                  ? draft.frictionMode === 'realistic'
                    ? 'REALISTIC'
                    : 'IDEAL'
                  : 'NOT AVAILABLE'}
              </span>
            </summary>
            <div className="rd-card-body">
              {!frictionSupported ? (
                <div className="rd-note" data-tone="warn">
                  This replay engine fills at the exact signal price. Spread and slippage
                  modelling is not implemented, so no execution cost will be reported.
                </div>
              ) : (
                <>
                  <div className="rd-chip-row">
                    <label className="rd-opt" style={{ width: 'auto' }}>
                      <input
                        type="radio"
                        name="rd-friction"
                        checked={draft.frictionMode === 'realistic'}
                        onChange={() => setDraft({ frictionMode: 'realistic' })}
                      />
                      <span>Realistic — buy at ask, sell at bid</span>
                    </label>
                    <label className="rd-opt" style={{ width: 'auto' }}>
                      <input
                        type="radio"
                        name="rd-friction"
                        checked={draft.frictionMode === 'ideal'}
                        onChange={() => setDraft({ frictionMode: 'ideal' })}
                      />
                      <span>Ideal — fills at the signal price</span>
                    </label>
                  </div>

                  {draft.frictionMode === 'realistic' && (
                    <>
                      <div className="rd-grid-2">
                        <div className="rd-field">
                          <label className="rd-field-label" htmlFor="rd-idx-spread">Index spread %</label>
                          <input
                            id="rd-idx-spread"
                            type="number" step="0.05" min={0} max={20}
                            className="rd-input"
                            value={draft.indexSpreadPct}
                            onChange={(e) => setDraft({ indexSpreadPct: Number(e.target.value) })}
                          />
                        </div>
                        <div className="rd-field">
                          <label className="rd-field-label" htmlFor="rd-stk-spread">Stock spread %</label>
                          <input
                            id="rd-stk-spread"
                            type="number" step="0.05" min={0} max={20}
                            className="rd-input"
                            value={draft.stockSpreadPct}
                            onChange={(e) => setDraft({ stockSpreadPct: Number(e.target.value) })}
                          />
                        </div>
                      </div>
                      <div className="rd-field">
                        <label className="rd-field-label" htmlFor="rd-slip">Slippage % (each leg)</label>
                        <input
                          id="rd-slip"
                          type="number" step="0.05" min={0} max={10}
                          className="rd-input"
                          style={{ width: 120 }}
                          value={draft.slippagePct}
                          onChange={(e) => setDraft({ slippagePct: Number(e.target.value) })}
                        />
                      </div>
                    </>
                  )}

                  <div className="rd-note">
                    These values are sent to the replay engine and echoed back on the status
                    payload. If the echo differs, the engine's values are the ones in force.
                  </div>
                  {echoMismatch && (
                    <div className="rd-field-warn">
                      The engine is running in “{echo?.friction_mode}” mode, not “{draft.frictionMode}”.
                      Restart the replay to apply your change.
                    </div>
                  )}
                </>
              )}
            </div>
          </details>

          <details className="rd-card">
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Advanced</span>
                <span className="rd-card-desc">Bar resolution and instrument scope.</span>
              </span>
              <span className="rd-card-meta">{draft.resolution}</span>
            </summary>
            <div className="rd-card-body">
              <div className="rd-field">
                <span className="rd-field-label">Bar resolution</span>
                <div className="rd-chip-row">
                  {(caps?.resolutions ?? ['5m']).map((r) => (
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
              <div className="rd-note">
                Leaving the instrument list empty replays the full default watchlist.
              </div>
            </div>
          </details>
        </div>

        <footer className="rd-sheet-foot">
          <button type="button" className="rd-btn" data-variant="ghost" onClick={resetDraft}>
            Reset to defaults
          </button>
          <div className="rd-sheet-foot-right">
            <button type="button" className="rd-btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="rd-btn"
              data-variant="primary"
              disabled={blocked}
              onClick={() => {
                setOpen(false);
                void transport.start();
              }}
              data-testid="replay-apply-start"
            >
              <Icons.Play size={12} /> Apply &amp; start
            </button>
          </div>
        </footer>
      </div>
    </>
  );
}
