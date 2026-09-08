import React, { useMemo, useRef } from 'react';
import { useReplayStore } from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { useFocusTrap } from './primitives/useFocusTrap';
import { fmtSmartDate, fmtTime } from './replayFormat';
import { CORE_INDICES, CORE_STOCKS, MONEYNESS_LEGS, REPLAY_STRATEGIES } from './replayStrategies';
import { useAvailableDates, verdictForDate } from './useAvailableDates';
import * as Icons from './ReplayIcons';

const LOT_PRESETS = [1, 2, 5, 10, 25, 50];

/**
 * Slide-over configuration sheet — opens when the gear icon is clicked.
 *
 * Styled with the same card pattern as the config settings page:
 * `<details>` accordions with orange left border on open, smooth reveal.
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

  const { data: available } = useAvailableDates();
  const verdict = verdictForDate(draft.date, available);
  const rangeInvalid = draft.endDate < draft.date;
  const timesInvalid = draft.endTime <= draft.startTime;
  const lotsInvalid = draft.lots < 1 || draft.lots > 500;
  const blocked = rangeInvalid || timesInvalid || lotsInvalid || verdict.level === 'error';

  const allStrategies = draft.strategies.includes('all');
  const allLegs = draft.moneyness.includes('ALL');
  const allInstruments = !draft.instruments || draft.instruments.length === 0;
  const frictionSupported = caps?.friction === true;

  const cardRef = useRef<HTMLDivElement>(null);
  useFocusTrap(cardRef, open, { onEscape: () => setOpen(false) });

  if (!open) return null;

  return (
    <>
      <div className="rd-sheet-scrim" onMouseDown={() => setOpen(false)} />
      <div
        ref={cardRef}
        className="rd-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Replay configuration"
        data-testid="replay-config-sheet"
      >
        <header className="rd-sheet-head">
          <Icons.Config size={14} />
          <span style={{ fontWeight: 700 }}>Configure Replay</span>
          <span style={{ marginLeft: 'auto', color: 'var(--k-dim)', fontSize: 'var(--rd-fs-label)' }}>
            {fmtSmartDate(draft.date)} · {fmtTime(draft.startTime, 5)}–{fmtTime(draft.endTime, 5)}
          </span>
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            onClick={() => setOpen(false)}
            aria-label="Close configuration"
          >
            <Icons.Close size={12} />
          </button>
        </header>

        <div className="rd-sheet-body">
          {/* ── Session ─────────────────────────────────────────────── */}
          <details className="rd-settings-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Session</span>
                <span className="rd-card-desc">Replay date, range and market hours</span>
              </span>
              <span className="rd-card-meta">{fmtSmartDate(draft.date)}</span>
            </summary>
            <div className="rd-card-body">
              <div className="rd-grid-2" style={{ marginBottom: 8 }}>
                <div className="rd-field">
                  <label className="rd-field-label" htmlFor="rd-cfg-date-start">Start date</label>
                  <input
                    id="rd-cfg-date-start"
                    type="date"
                    className="rd-input"
                    value={draft.date}
                    min={available?.earliest ?? undefined}
                    max={available?.latest ?? undefined}
                    onChange={(e) => setDraft({ date: e.target.value, endDate: e.target.value })}
                  />
                </div>
                <div className="rd-field">
                  <label className="rd-field-label" htmlFor="rd-cfg-date-end">End (range)</label>
                  <input
                    id="rd-cfg-date-end"
                    type="date"
                    className="rd-input"
                    value={draft.endDate}
                    min={draft.date}
                    max={available?.latest ?? undefined}
                    aria-invalid={rangeInvalid}
                    onChange={(e) => setDraft({ endDate: e.target.value })}
                  />
                </div>
              </div>
              {rangeInvalid && <div className="rd-field-error">End date is before the start date.</div>}
              {verdict.level === 'warn' && <div className="rd-field-warn">{verdict.message}</div>}
              {verdict.level === 'error' && <div className="rd-field-error">{verdict.message}</div>}
              <div className="rd-grid-2" style={{ marginTop: 8 }}>
                <div className="rd-field">
                  <label className="rd-field-label" htmlFor="rd-cfg-time-start">From</label>
                  <input
                    id="rd-cfg-time-start"
                    type="time"
                    step={1}
                    className="rd-input"
                    value={draft.startTime}
                    onChange={(e) => setDraft({ startTime: e.target.value })}
                  />
                </div>
                <div className="rd-field">
                  <label className="rd-field-label" htmlFor="rd-cfg-time-end">To</label>
                  <input
                    id="rd-cfg-time-end"
                    type="time"
                    step={1}
                    className="rd-input"
                    value={draft.endTime}
                    aria-invalid={timesInvalid}
                    onChange={(e) => setDraft({ endTime: e.target.value })}
                  />
                </div>
              </div>
              {timesInvalid && <div className="rd-field-error">End time must be after the start time.</div>}
            </div>
          </details>

          {/* ── Strategies ──────────────────────────────────────────── */}
          <details className="rd-settings-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Strategies</span>
                <span className="rd-card-desc">Which strategies emit signals</span>
              </span>
              <span className="rd-card-meta">{allStrategies ? 'ALL' : `${draft.strategies.length}`}</span>
            </summary>
            <div className="rd-card-body">
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

          {/* ── Instruments ─────────────────────────────────────────── */}
          <details className="rd-settings-card">
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Instruments</span>
                <span className="rd-card-desc">Indices and stocks scanned</span>
              </span>
              <span className="rd-card-meta">{allInstruments ? 'ALL' : `${draft.instruments.length}`}</span>
            </summary>
            <div className="rd-card-body" style={{ maxHeight: 220, overflowY: 'auto' }}>
              <div style={{ fontSize: '10px', color: 'var(--k-dim)', padding: '2px 0', textTransform: 'uppercase' }}>Indices</div>
              {CORE_INDICES.map((inst) => (
                <label className="rd-opt" key={inst.id}>
                  <input
                    type="checkbox"
                    checked={allInstruments || draft.instruments.includes(inst.id)}
                    onChange={() => toggleInstrument(inst.id)}
                  />
                  <span>{inst.id}</span>
                </label>
              ))}
              <div style={{ fontSize: '10px', color: 'var(--k-dim)', padding: '6px 0 2px', textTransform: 'uppercase' }}>Stocks</div>
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
          </details>

          {/* ── Position sizing ─────────────────────────────────────── */}
          <details className="rd-settings-card" open>
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Position sizing</span>
                <span className="rd-card-desc">Strike selection and order size</span>
              </span>
              <span className="rd-card-meta">{allLegs ? 'ALL' : draft.moneyness.join(', ')} · {draft.lots}L</span>
            </summary>
            <div className="rd-card-body">
              <div className="rd-chip-row">
                {MONEYNESS_LEGS.map((leg) => (
                  <button
                    key={leg.id}
                    type="button"
                    className="rd-btn rd-btn-sm"
                    title={leg.hint}
                    aria-pressed={allLegs || draft.moneyness.includes(leg.id)}
                    data-variant={allLegs || draft.moneyness.includes(leg.id) ? 'primary' : undefined}
                    onClick={() => toggleMoneyness(leg.id)}
                  >
                    {leg.label}
                  </button>
                ))}
              </div>
              <div className="rd-field" style={{ marginTop: 8 }}>
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
                    min={1} max={500}
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

          {/* ── Execution model ─────────────────────────────────────── */}
          {frictionSupported && (
            <details className="rd-settings-card">
              <summary>
                <span className="rd-card-caret">›</span>
                <span className="rd-card-info">
                  <span className="rd-card-title">Execution model</span>
                  <span className="rd-card-desc">How fills are priced against the signal price</span>
                </span>
                <span className="rd-card-meta">{draft.frictionMode === 'realistic' ? 'REALISTIC' : 'IDEAL'}</span>
              </summary>
              <div className="rd-card-body">
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
                {draft.frictionMode === 'realistic' && (
                  <div className="rd-grid-2" style={{ marginTop: 8 }}>
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
                )}
              </div>
            </details>
          )}

          {/* ── Advanced ────────────────────────────────────────────── */}
          <details className="rd-settings-card">
            <summary>
              <span className="rd-card-caret">›</span>
              <span className="rd-card-info">
                <span className="rd-card-title">Advanced</span>
                <span className="rd-card-desc">Bar resolution</span>
              </span>
              <span className="rd-card-meta">{draft.resolution}</span>
            </summary>
            <div className="rd-card-body">
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
          </details>
        </div>

        {/* ── Footer ────────────────────────────────────────────────── */}
        <footer className="rd-sheet-foot">
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            onClick={resetDraft}
            title="Reset all configuration to defaults"
            style={{ opacity: 0.6 }}
          >
            Reset defaults
          </button>
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
            <button type="button" className="rd-btn" onClick={() => setOpen(false)}>
              Close
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
              <Icons.Play size={12} /> Apply & start
            </button>
          </div>
        </footer>
      </div>
    </>
  );
}
