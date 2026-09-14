/**
 * "Offline Data" settings, shown inside the Connect tab.
 *
 * Settings here, browsing in the DATA tab. This page owns the decisions — where the data
 * lives, which universes to fetch, over what window — and shows the cost of those choices
 * before anything is downloaded.
 *
 * The download itself is deliberately NOT started from the browser. It is a 10-hour,
 * rate-limited job that must survive the tab closing, so the page hands over the exact
 * command instead of pretending a web request can own it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { c, tint } from '../../styles/terminalUI';
import { useDataLake, type TierPlan } from '../../hooks/useDataLake';
import { notifyOrder } from '../../store/useKiteNotifications';
import FolderPicker from './FolderPicker';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';

const cardStyle: React.CSSProperties = {
  background: c.surface,
  border: `1px solid ${c.border}`,
  borderRadius: 14,
  padding: 16,
  marginBottom: 14,
  fontFamily: c.fontFamily,
};

const label: React.CSSProperties = {
  color: c.dim,
  fontSize: 10.5,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
  marginBottom: 5,
};

const input: React.CSSProperties = {
  background: c.raised,
  border: `1px solid ${c.border}`,
  borderRadius: 8,
  color: c.bright,
  padding: '7px 10px',
  fontSize: 12.5,
  fontFamily: c.fontFamily,
};

const btn = (tone: string, solid = false): React.CSSProperties => ({
  background: solid ? tone : 'transparent',
  color: solid ? c.bg : tone,
  border: `1px solid ${tone}`,
  borderRadius: 9,
  padding: '7px 13px',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: c.fontFamily,
});

function Title({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ color: c.bright, fontSize: 14, fontWeight: 750 }}>{children}</div>
      {hint && <div style={{ color: c.dim, fontSize: 11.5, marginTop: 3, lineHeight: 1.6 }}>{hint}</div>}
    </div>
  );
}

function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      },
      () => setCopied(false),
    );
  }, [text]);
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', marginTop: 8 }}>
      <code style={{
        flex: 1, background: c.raised, border: `1px solid ${c.border}`, borderRadius: 8,
        color: c.cyan, padding: '8px 10px', fontSize: 11.5, fontFamily: MONO,
        overflowX: 'auto', whiteSpace: 'pre',
      }}>{text}</code>
      <button onClick={copy} style={btn(copied ? c.green : c.muted)}>{copied ? 'Copied' : 'Copy'}</button>
    </div>
  );
}

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function DataLakeSettingsPanel() {
  const { summary, loading, error, refresh, listVolumes, browse, setRoot, activateRoot, forgetRoot, tierPlan } =
    useDataLake('minute');

  const [pickerOpen, setPickerOpen] = useState(false);
  const [interval, setIntervalName] = useState('minute');
  const [frm, setFrm] = useState(() => iso(new Date(Date.now() - 182 * 86_400_000)));
  const [to, setTo] = useState(() => iso(new Date()));
  const [rate, setRate] = useState(2.5);
  const [stopAfter, setStopAfter] = useState('');
  const [plan, setPlan] = useState<TierPlan | null>(null);
  const [planError, setPlanError] = useState('');
  const [busy, setBusy] = useState(false);

  const available = summary?.available ?? false;
  const stats = summary?.stats ?? {};

  const loadPlan = useCallback(async () => {
    if (!available) return;
    setBusy(true);
    try {
      setPlan(await tierPlan(frm, to, interval, rate));
      setPlanError('');
    } catch (e) {
      setPlan(null);
      setPlanError(e instanceof Error ? e.message : 'could not cost the plan');
    } finally {
      setBusy(false);
    }
  }, [available, tierPlan, frm, to, interval, rate]);

  useEffect(() => { void loadPlan(); }, [loadPlan]);

  const shown = useMemo(
    () => (plan && stopAfter
      ? plan.tiers.slice(0, plan.tiers.findIndex((t) => t.universe === stopAfter) + 1)
      : plan?.tiers ?? []),
    [plan, stopAfter],
  );
  const cum = shown.length ? shown[shown.length - 1] : null;

  const command = [
    'kitelake download --tiers',
    `--interval ${interval}`,
    `--from ${frm}`,
    `--to ${to}`,
    rate !== 2.5 ? `--rate ${rate}` : '',
    stopAfter ? `--stop-after ${stopAfter}` : '',
    '--retry-failed',
  ].filter(Boolean).join(' ');

  const tight = cum && summary && summary.free_gib > 0 && summary.free_gib < cum.cumulative_gib;

  return (
    <div>
      {/* ── where the data lives ─────────────────────────────────────── */}
      <div style={cardStyle}>
        <Title hint="Storage is found by identity, not by path — a stamp file in the folder. Move the drive or let it remount somewhere else and it is still found; unplug it and reads pause with an explanation rather than an error.">
          Storage location
        </Title>

        {error && (
          <div style={{ color: c.red, fontSize: 12, marginBottom: 10 }}>Cannot reach the backend: {error}</div>
        )}

        {loading && <div style={{ color: c.dim, fontSize: 12 }}>checking…</div>}

        {!loading && available && (
          <>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ width: 8, height: 8, borderRadius: 4, background: c.green }} />
              <code style={{ color: c.bright, fontSize: 12, fontFamily: MONO }}>{summary?.root}</code>
              {summary?.label && <span style={{ color: c.dim, fontSize: 11 }}>[{summary.label}]</span>}
            </div>
            <div style={{ color: c.dim, fontSize: 11.5, marginTop: 6 }}>
              {summary?.free_gib} GiB free of {summary?.total_gib} GiB · {stats.gib ?? 0} GiB used by stored bars
            </div>
          </>
        )}

        {!loading && !available && summary && (
          <div style={{ background: tint(c.amber, 8), border: `1px solid ${tint(c.amber, 32)}`, borderRadius: 11, padding: 12 }}>
            <div style={{ color: c.amber, fontSize: 12.5, fontWeight: 700 }}>
              {summary.volume_present_unmounted ? 'The drive is connected but not mounted' : summary.reason}
            </div>
            <ul style={{ margin: '8px 0 0', paddingLeft: 18, color: c.text, fontSize: 11.5 }}>
              {(summary.guidance ?? []).map((g, i) => <li key={i} style={{ marginBottom: 3 }}>{g}</li>)}
            </ul>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          <button onClick={() => setPickerOpen(true)} style={btn(c.blue, !available)}>
            {available ? 'Change folder' : 'Choose folder'}
          </button>
          <button onClick={() => void refresh()} style={btn(c.muted)}>Re-check</button>
        </div>

        {(summary?.known ?? []).length > 1 && (
          <div style={{ marginTop: 14 }}>
            <div style={label}>Known folders</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {summary!.known.map((k) => {
                const active = k.lake_id === summary!.lake_id;
                return (
                  <div key={k.lake_id} style={{
                    display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between',
                    background: active ? tint(c.green, 10) : c.raised,
                    border: `1px solid ${active ? c.green : c.border}`, borderRadius: 9, padding: '7px 10px',
                  }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ color: c.bright, fontSize: 12 }}>{k.label || '(unnamed)'}</div>
                      <div style={{ color: c.dim, fontSize: 10.5, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {k.last_path}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      {!active && (
                        <button
                          onClick={async () => {
                            try {
                              await activateRoot(k.lake_id);
                              notifyOrder({ kind: 'info', title: 'Folder activated', message: 'Storage location updated.' });
                            } catch (err: any) {
                              notifyOrder({ kind: 'error', title: 'Activation failed', message: err?.message || 'Could not activate folder.' });
                            }
                          }}
                          style={btn(c.blue)}
                        >
                          Use
                        </button>
                      )}
                      <button
                        onClick={async () => {
                          try {
                            await forgetRoot(k.lake_id);
                            notifyOrder({ kind: 'info', title: 'Folder removed', message: 'Folder removed from known list.' });
                          } catch (err: any) {
                            notifyOrder({ kind: 'error', title: 'Could not remove folder', message: err?.message || 'Failed to forget folder.' });
                          }
                        }}
                        style={btn(c.muted)}
                        title="Removes it from this list only — the stored data is not touched"
                      >
                        Forget
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── what to download ─────────────────────────────────────────── */}
      <div style={cardStyle}>
        <Title hint="The three universes are nested — their union is exactly equity-all — so they run as tiers of one job. A later tier only fetches what the earlier ones did not, which is why the total is far below the sum of the parts.">
          What to download
        </Title>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <div>
            <div style={label}>Interval</div>
            <select value={interval} onChange={(e) => setIntervalName(e.target.value)} style={input}>
              {['minute', '3minute', '5minute', '15minute', '30minute', '60minute', 'day'].map((i) => (
                <option key={i} value={i}>{i}</option>
              ))}
            </select>
          </div>
          <div>
            <div style={label}>From</div>
            <input type="date" value={frm} onChange={(e) => setFrm(e.target.value)} style={input} />
          </div>
          <div>
            <div style={label}>To</div>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} style={input} />
          </div>
          <div>
            <div style={label}>Requests / sec</div>
            <input
              type="number" min={0.5} max={3} step={0.5} value={rate}
              onChange={(e) => setRate(Math.min(3, Math.max(0.5, Number(e.target.value) || 2.5)))}
              style={{ ...input, width: 78 }}
            />
          </div>
          <div>
            <div style={label}>Stop after</div>
            <select value={stopAfter} onChange={(e) => setStopAfter(e.target.value)} style={input}>
              <option value="">run all tiers</option>
              {(plan?.tiers ?? []).map((t) => (
                <option key={t.universe} value={t.universe}>{t.universe}</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ color: c.dim, fontSize: 11, marginBottom: 10 }}>
          Kite caps the historical endpoint at 3 requests/second, so every estimate below is
          bound by that, not by your connection or disk.
        </div>

        {busy && <div style={{ color: c.dim, fontSize: 12 }}>costing…</div>}
        {planError && !plan && (
          <div style={{ color: c.amber, fontSize: 11.5 }}>
            Cannot cost the plan — sync the instrument master first (<code>kitelake instruments</code>).
          </div>
        )}

        {plan && shown.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 11.5, color: c.text, minWidth: 520, width: '100%' }}>
              <thead>
                <tr style={{ color: c.dim, textAlign: 'right' }}>
                  <th style={{ textAlign: 'left', padding: '4px 8px' }}>Tier</th>
                  <th style={{ padding: '4px 8px' }}>Instruments</th>
                  <th style={{ padding: '4px 8px' }}>New</th>
                  <th style={{ padding: '4px 8px' }}>Requests</th>
                  <th style={{ padding: '4px 8px' }}>ETA</th>
                  <th style={{ padding: '4px 8px' }}>Size</th>
                  <th style={{ padding: '4px 8px' }}>Done by</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((t) => (
                  <tr key={t.universe} style={{ borderTop: `1px solid ${c.border2}`, textAlign: 'right' }}>
                    <td style={{ textAlign: 'left', padding: '5px 8px', color: c.bright }}>{t.tier}. {t.universe}</td>
                    <td style={{ padding: '5px 8px' }}>{t.instruments.toLocaleString()}</td>
                    <td style={{ padding: '5px 8px', color: c.cyan }}>+{t.new_instruments.toLocaleString()}</td>
                    <td style={{ padding: '5px 8px' }}>{t.requests_incremental.toLocaleString()}</td>
                    <td style={{ padding: '5px 8px' }}>{t.eta_incremental}</td>
                    <td style={{ padding: '5px 8px' }}>{t.est_gib_incremental.toFixed(2)} GiB</td>
                    <td style={{ padding: '5px 8px', color: c.bright }}>{t.cumulative_eta}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {cum && (
              <div style={{ color: c.dim, fontSize: 11, marginTop: 10, lineHeight: 1.6 }}>
                {cum.cumulative_requests.toLocaleString()} requests · {cum.cumulative_eta} · ~{cum.cumulative_gib.toFixed(1)} GiB
                {!stopAfter && (
                  <> — versus {plan.naive_requests.toLocaleString()} if the tiers did not share work
                    ({plan.requests_saved_by_dedup.toLocaleString()} requests skipped automatically).</>
                )}
                {tight && (
                  <div style={{ color: c.amber, marginTop: 5 }}>
                    Only {summary!.free_gib} GiB free — that may not be enough. Estimates are
                    upper bounds, since Kite omits candles for minutes with no trade.
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── running it ───────────────────────────────────────────────── */}
      <div style={cardStyle}>
        <Title hint="Downloads run from the terminal on purpose: this is a multi-hour rate-limited job that has to outlive the browser tab, and it resumes chunk-by-chunk if the token expires or the drive drops.">
          Run the download
        </Title>

        <div style={label}>1 · refresh the Kite session (tokens expire each morning)</div>
        <Command text="kitelake auth" />

        <div style={{ ...label, marginTop: 14 }}>2 · sync the instrument master (no login needed)</div>
        <Command text="kitelake instruments" />

        <div style={{ ...label, marginTop: 14 }}>3 · download, detached so it survives closing the terminal</div>
        <Command text={`setsid nohup ${command} > ~/kitelake-download.log 2>&1 &`} />

        <div style={{ ...label, marginTop: 14 }}>watch progress · verify · repair lost writes</div>
        <Command text={`kitelake status --interval ${interval}`} />
        <Command text={`kitelake verify --interval ${interval}`} />
        <Command text={`kitelake repair --interval ${interval} --dry-run`} />

        <div style={{ color: c.dim, fontSize: 11, marginTop: 12, lineHeight: 1.7 }}>
          <strong style={{ color: c.text }}>What Kite cannot give you:</strong> the historical API's
          finest interval is one minute — there is no second- or tick-level history at any
          price, and it cannot be backfilled. Live ticks can be recorded forward from now with{' '}
          <code style={{ color: c.cyan }}>kitelake ticks record</code>. Historical candles also
          need the paid add-on on your Kite Connect app, and the instrument master lists live
          contracts only, so expired option-chain history is not obtainable.
        </div>
      </div>

      <FolderPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onChoose={async (path, lbl) => {
          try {
            await setRoot(path, lbl);
            notifyOrder({ kind: 'info', title: 'Data lake folder set', message: `Storage location set to ${path}` });
          } catch (err: any) {
            notifyOrder({ kind: 'error', title: 'Could not set folder', message: err?.message || 'Failed to set lake root' });
          }
        }}
        listVolumes={listVolumes}
        browse={browse}
      />
    </div>
  );
}

export default DataLakeSettingsPanel;
