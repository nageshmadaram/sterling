import { useEffect, useMemo, useRef, useState } from "react";
import { fetchPcrDesk, sessionIsoOf } from "../../lib/pcr/fetchPcr";
import {
  BAND_COPY,
  FLOW_MOVE_MIN,
  SESSION_CLOSE_MIN,
  SESSION_OPEN_MIN,
  buildGrid,
  describeFlow,
  expiryKind,
  flowPath,
  formatExpiry,
  formatPcr,
  hhmmToMinutes,
  isValidPrint,
  lastValidSlot,
  liveAction,
  pcrBand,
  putShare,
  readBook,
  readPcr,
  type PcrAction,
} from "../../lib/pcr/slots";
import {
  PCR_INDICES,
  type PcrDeskPayload,
  type PcrIndex,
  type PcrMetric,
  type PcrSlot,
} from "../../lib/pcr/types";
import { formatIstIsoDate, getIstParts } from "../../lib/astro/time";
import { useKiteQuote } from "../../hooks/useKite";
import { underlyingQuoteKey } from "./board/boardTypes";
import { PCR_CSS } from "./pcrCss";
import {
  IndexTile, Path, ideaKind, playHint, moveTxt, stanceLab, fmtLtp, nowMinutes,
  type DeskRow, type TileField, type SectionId, type ColId, type Prefs,
  SECTIONS, TABLE_COLS, loadPrefs, savePrefs, expiryLong,
} from "./pcrWidgets";

export function PcrPane() {
  const [payload, setPayload] = useState<PcrDeskPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState<PcrIndex>("NIFTY");
  const [metric, setMetric] = useState<PcrMetric>("oi");
  const [now, setNow] = useState<Date | null>(null);
  const [liveIso, setLiveIso] = useState("");
  const [sessionIso, setSessionIso] = useState("");
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const prefsRef = useRef<HTMLDivElement>(null);
  const writePrefs = (fn: (p: Prefs) => Prefs) => {
    setPrefs((p) => {
      const next = fn(p);
      savePrefs(next);
      return next;
    });
  };

  useEffect(() => {
    if (!prefsOpen) return;
    const onDown = (e: MouseEvent) => {
      if (prefsRef.current && !prefsRef.current.contains(e.target as Node)) setPrefsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setPrefsOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [prefsOpen]);

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setNow((prev) => {
        if (!prev) return d;
        const a = getIstParts(prev);
        const b = getIstParts(d);
        if (a.hour === b.hour && a.minute === b.minute) return prev;
        return d;
      });
    };
    tick();
    const id = window.setInterval(tick, 5_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchPcrDesk(null)
        .then((data) => {
          if (cancelled) return;
          setPayload(data);
          setError(null);
          const iso = sessionIsoOf(data);
          if (iso) {
            setLiveIso(iso);
            setSessionIso(iso);
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "PCR feed unavailable");
        });
    };
    load();
    const id = window.setInterval(load, 8_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const map: Record<string, PcrIndex> = { "1": "NIFTY", "2": "BANKNIFTY", "3": "FINNIFTY", "4": "SENSEX", "5": "MIDCPNIFTY" };
      const pick = map[e.key];
      if (pick) {
        setIndex(pick);
        writePrefs((p) => {
          const on = p.indices.includes(pick);
          return { ...p, indices: on ? p.indices.filter((id) => id !== pick) : [...p.indices, pick] };
        });
      }
      if (e.key === "a" || e.key === "A") writePrefs((p) => ({ ...p, indices: PCR_INDICES.map((u) => u.id) }));
      if (e.key === "p" || e.key === "P") writePrefs((p) => ({ ...p, path: !p.path }));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const kiteSyms = useMemo(() => PCR_INDICES.map((u) => underlyingQuoteKey(u.id)), []);
  const { data: liveQuotes } = useKiteQuote(kiteSyms, true, 3_000);
  const todayIso = now ? formatIstIsoDate(now) : liveIso;
  const series = payload?.series[index];
  const nowMin = now ? nowMinutes(now) : null;
  const inLiveSession = Boolean(
    nowMin != null && nowMin >= SESSION_OPEN_MIN && nowMin < SESSION_CLOSE_MIN,
  );
  const gridNowMin = inLiveSession ? nowMin : SESSION_CLOSE_MIN;
  const metricBoards = useMemo(() => {
    if (!payload) return null;
    const make = (m: PcrMetric) => {
      const out = {} as Record<PcrIndex, PcrSlot[]>;
      for (const u of PCR_INDICES) {
        const row = payload.series[u.id];
        if (!row) continue;
        out[u.id] = buildGrid(row.marks, row.latest, gridNowMin, m);
      }
      return out;
    };
    return { oi: make("oi"), volume: make("volume"), changeOi: make("changeOi") };
  }, [payload, gridNowMin]);
  const boards = metricBoards?.[metric] ?? null;
  const grid = boards?.[index] ?? [];
  const axis = boards?.NIFTY ?? boards?.[PCR_INDICES[0].id] ?? [];

  const deskRows = useMemo(() => {
    if (!metricBoards) return [];
    return PCR_INDICES.map((u) => {
      const oi = metricBoards.oi[u.id] ?? [];
      const book = readBook(u.short, oi, metricBoards.volume[u.id] ?? [], metricBoards.changeOi[u.id] ?? []);
      const last = lastValidSlot(oi, "oi");
      const raw = book.book?.to ?? last?.pcr ?? payload?.series[u.id]?.livePcr ?? payload?.series[u.id]?.latest?.pcr ?? null;
      const pcr = isValidPrint(raw, "oi") ? raw : null;
      const filled = oi.filter((s) => isValidPrint(s.pcr, "oi"));
      const prev = filled.length >= 2 ? filled[filled.length - 2] : null;
      const delta =
        pcr != null && prev?.pcr != null
          ? Math.round((pcr - prev.pcr) * 100) / 100
          : (last?.delta ?? null);
      const row = payload?.series[u.id];
      const q = liveQuotes?.[underlyingQuoteKey(u.id)];
      const liveLtp = typeof q?.last_price === "number" && q.last_price > 0 ? q.last_price : null;
      const close = typeof q?.ohlc?.close === "number" && q.ohlc.close > 0 ? q.ohlc.close : null;
      const liveChg =
        liveLtp != null && close != null
          ? Math.round(((liveLtp - close) / close) * 10000) / 100
          : (typeof q?.change === "number" ? q.change : null);
      const kind = row ? expiryKind(row.expiry, sessionIso || todayIso) : "weekly";
      const expiry = row
        ? `${kind === "today" ? "Today" : kind === "weekly" ? "Wk" : "Mo"} ${formatExpiry(row.expiry)}`
        : "—";
      const insight = readPcr(oi, liveChg ?? row?.spot.changePer ?? null);
      const action = insight.action;
      const band = last?.band ?? pcrBand(pcr);
      const flow =
        pcr != null && last?.hhmm
          ? describeFlow(u.short, last.hhmm, pcr, delta ?? 0, metric)
          : null;
      const flowAction: PcrAction = flow?.action ?? "Wait";
      const flowWhy = flow?.why ?? "";
      return {
        id: u.id,
        name: u.short,
        pcr,
        action,
        why: book.book?.why ?? "Waiting on the OI print.",
        path: book.book ? flowPath(book.book) : "—",
        move: book.book?.move ?? delta,
        vol: book.volumeStance,
        doi: book.deltaStance,
        spot: liveLtp ?? row?.spot.ltp ?? null,
        spotChg: liveChg ?? row?.spot.changePer ?? null,
        delta,
        putPct: putShare(pcr),
        expiry,
        expiryLong: row ? expiryLong(row.expiry, kind) : "—",
        maxPain: row?.spot.maxPain ?? null,
        hhmm: last?.hhmm ?? "",
        band,
        insight,
        flowAction,
        flowWhy,
      };
    });
  }, [metricBoards, payload, sessionIso, todayIso, liveQuotes]);
  const picked = prefs.indices;
  const pathOn = prefs.path;
  const showAll = picked.length === PCR_INDICES.length;
  const cols = PCR_INDICES.filter((u) => picked.includes(u.id));
  const sumRows = deskRows.filter((r) => picked.includes(r.id));
  const heatSlots = showAll || cols.length !== 1 ? axis : (boards?.[cols[0].id] ?? grid);
  const insight = useMemo(() => readPcr(grid, series?.spot.changePer ?? null), [grid, series?.spot.changePer]);
  const tape = useMemo(() => {
    const src = cols.length ? cols : PCR_INDICES;
    const out: { id: string; name: string; action: PcrAction; why: string; clock: string; hhmm: string }[] = [];
    for (const u of src) {
      const row = (metricBoards?.oi[u.id] ?? []);
      for (const s of row) {
        if (s.delta == null || Math.abs(s.delta) < FLOW_MOVE_MIN) continue;
        if (!isValidPrint(s.pcr, "oi")) continue;
        const line = describeFlow(u.short, s.hhmm, s.pcr, s.delta, "oi");
        out.push({ id: `${u.id}-${s.hhmm}`, name: line.name, action: line.action, why: line.why, clock: line.clock, hhmm: line.hhmm });
      }
    }
    return out.sort((a, b) => hhmmToMinutes(b.hhmm) - hhmmToMinutes(a.hhmm)).slice(0, 8);
  }, [metricBoards, cols]);
  const showSec = (id: SectionId) => prefs.sections[id];
  const showTile = (id: TileField) => prefs.tile[id] !== false;
  const showCol = (id: ColId) => prefs.cols[id];
  const toggleSec = (id: SectionId) => writePrefs((p) => ({ ...p, sections: { ...p.sections, [id]: !p.sections[id] } }));
  const toggleCol = (id: ColId) => writePrefs((p) => ({ ...p, cols: { ...p.cols, [id]: !p.cols[id] } }));
  const toggleIndex = (id: PcrIndex) => {
    setIndex(id);
    writePrefs((p) => {
      const on = p.indices.includes(id);
      return { ...p, indices: on ? p.indices.filter((x) => x !== id) : [...p.indices, id] };
    });
  };
  const notesOn = (prefs.layout === "table" && showSec("read")) || showSec("tape") || showSec("legend");

  return (
    <div className="kite-pcr">
      <style>{PCR_CSS}</style>
      <div className="kp-desk">
        <header className="kp-head">
          <div className="kp-head-row">
            <h1 className="kp-title">PCR Desk</h1>
            <div className="kp-tabs" role="tablist" aria-label="PCR metric">
              {([["oi", "OI"], ["volume", "Volume"], ["changeOi", "ΔOI"]] as const).map(([id, label]) => (
                <button key={id} type="button" role="tab" data-on={metric === id} onClick={() => setMetric(id)}>{label}</button>
              ))}
            </div>
            <div className="kp-tools" ref={prefsRef}>
              <button type="button" className="kp-gear" data-on={prefsOpen} aria-expanded={prefsOpen} aria-label="Settings" onClick={() => setPrefsOpen((v) => !v)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                  <path d="M4 7h10" />
                  <circle cx="16.5" cy="7" r="2.2" />
                  <path d="M20 12H10" />
                  <circle cx="7.5" cy="12" r="2.2" />
                  <path d="M4 17h10" />
                  <circle cx="16.5" cy="17" r="2.2" />
                </svg>
              </button>
              {prefsOpen ? (
                <div className="kp-prefs" role="dialog" aria-label="Settings">
                  <div className="kp-prefs-head">
                    <strong>Settings</strong>
                    <button type="button" className="kp-prefs-x" aria-label="Close settings" onClick={() => setPrefsOpen(false)}>×</button>
                  </div>
                  <div className="kp-prefs-body">
                    <h3>Layout</h3>
                    <div className="kp-pref-chips" role="tablist" aria-label="Layout">
                      <button type="button" data-on={prefs.layout === "tiles"} onClick={() => writePrefs((p) => ({ ...p, layout: "tiles" }))}>Tiles</button>
                      <button type="button" data-on={prefs.layout === "table"} onClick={() => writePrefs((p) => ({ ...p, layout: "table" }))}>Table</button>
                    </div>
                    <h3>Indices</h3>
                    <div className="kp-pref-chips" role="group" aria-label="Indices">
                      <button type="button" data-on={showAll} onClick={() => writePrefs((p) => ({ ...p, indices: showAll ? [] : PCR_INDICES.map((u) => u.id) }))}>All</button>
                      {PCR_INDICES.map((u) => (
                        <button key={u.id} type="button" data-on={picked.includes(u.id)} onClick={() => toggleIndex(u.id)}>{u.short}</button>
                      ))}
                    </div>
                    <h3>View</h3>
                    <div className="kp-pref-chips">
                      <button type="button" data-on={pathOn} onClick={() => writePrefs((p) => ({ ...p, path: !p.path }))}>Path</button>
                    </div>
                    <h3>Show</h3>
                    <div className="kp-pref-grid">
                      {SECTIONS.filter((s) => prefs.layout === "table" || s.id !== "read").map((s) => (
                        <label key={s.id}>
                          <input type="checkbox" checked={prefs.sections[s.id]} onChange={() => toggleSec(s.id)} />
                          {s.label}
                        </label>
                      ))}
                    </div>
                    {prefs.layout === "table" ? (
                      <>
                        <h3>Columns</h3>
                        <div className="kp-pref-grid">
                          {TABLE_COLS.map((c) => (
                            <label key={c.id}>
                              <input type="checkbox" checked={prefs.cols[c.id]} onChange={() => toggleCol(c.id)} />
                              {c.label}
                            </label>
                          ))}
                        </div>
                      </>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <div className="kp-body">
          {error && !payload ? <p className="kp-sub">{error}</p> : null}
          {pathOn ? (
            <div className="kp-stack" style={{ marginBottom: 10 }}>
              {(cols.length ? cols : PCR_INDICES).map((u) => {
                const row = payload?.series[u.id];
                const slots = boards?.[u.id] ?? [];
                if (!row) return null;
                return (
                  <div key={u.id}>
                    <p className="kp-kicker">{u.short}</p>
                    <Path slots={slots} marks={row.marks} />
                  </div>
                );
              })}
            </div>
          ) : null}
          {sumRows.length ? (
            <div className="kp-stack">
              {showSec("book") && prefs.layout === "tiles" ? (
                <div className={`kp-tiles${cols.length === 1 ? " one" : ""}`}>
                  {sumRows.map((row) => <IndexTile key={row.id} row={row} show={showTile} />)}
                </div>
              ) : null}
              {showSec("book") && prefs.layout === "table" ? (
                <div className="kp-sheet">
                  <table className="kp-book">
                    <thead>
                      <tr>
                        <th>Index</th>
                        {showCol("play") ? <th>Play</th> : null}
                        {showCol("pcr") ? <th className="num">OI PCR</th> : null}
                        {showCol("move") ? <th className="num">Move</th> : null}
                        {showCol("vol") ? <th className="mid">Vol</th> : null}
                        {showCol("doi") ? <th className="mid">ΔOI</th> : null}
                        {showCol("spot") ? <th className="num">Spot</th> : null}
                        {showCol("pc") ? <th className="mid">P/C</th> : null}
                        {showCol("expiry") ? <th>Expiry</th> : null}
                        {showCol("pain") ? <th className="num">Pain</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {sumRows.map((row) => {
                        const kind = ideaKind(row.action);
                        return (
                          <tr key={row.id}>
                            <th>{row.name}</th>
                            {showCol("play") ? (
                              <td className="kp-play-cell">
                                <span className={`kp-play kp-act ${kind}`}>{row.action}</span>
                                <span className="kp-tip">{playHint(row.action)}</span>
                              </td>
                            ) : null}
                            {showCol("pcr") ? (
                              <td className="kp-pcr-cell num">
                                <div className="kp-pcr-wrap">
                                  <span className="kp-pcr-val">{row.pcr != null ? formatPcr(row.pcr) : "—"}</span>
                                  {row.pcr != null && row.flowAction ? (
                                    <span
                                      className={`kp-flow-pill kp-act ${ideaKind(row.flowAction)}`}
                                      title={row.flowWhy || undefined}
                                    >
                                      {row.flowAction}
                                    </span>
                                  ) : null}
                                </div>
                              </td>
                            ) : null}
                            {showCol("move") ? (
                              <td className="kp-move num">
                                {row.path}
                                {row.move != null ? (
                                  <span className={(row.move ?? 0) > 0 ? "text-up" : (row.move ?? 0) < 0 ? "text-down" : ""}>
                                    {" "}{moveTxt(row.move)}
                                  </span>
                                ) : null}
                              </td>
                            ) : null}
                            {showCol("vol") ? <td className={`kp-st ${row.vol} mid`}>{stanceLab(row.vol)}</td> : null}
                            {showCol("doi") ? <td className={`kp-st ${row.doi} mid`}>{stanceLab(row.doi)}</td> : null}
                            {showCol("spot") ? (
                              <td className="num">
                                <span className="kp-spot">
                                  <span className="ltp">{fmtLtp(row.spot)}</span>
                                  {row.spotChg != null ? (
                                    <span className={`kp-chg ${row.spotChg >= 0 ? "text-up" : "text-down"}`}>
                                      {row.spotChg >= 0 ? "+" : ""}{row.spotChg.toFixed(2)}%
                                    </span>
                                  ) : null}
                                </span>
                              </td>
                            ) : null}
                            {showCol("pc") ? <td className="mid">{row.putPct == null ? "—" : `${Math.round(row.putPct * 100)}/${100 - Math.round(row.putPct * 100)}`}</td> : null}
                            {showCol("expiry") ? <td>{row.expiry}</td> : null}
                            {showCol("pain") ? <td className="num">{fmtLtp(row.maxPain)}</td> : null}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {showSec("heat") ? (
                <div className={`kp-sheet kp-sheet-heat${cols.length === 1 ? " one" : ""}`}>
                  <table>
                    <colgroup>
                      <col className="c-time" />
                      {cols.map((u) => <col key={u.id} />)}
                    </colgroup>
                    <thead>
                      <tr>
                        <th>Time</th>
                        {cols.map((u) => (
                          <th key={u.id} data-on={!showAll && index === u.id}>{u.short}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {heatSlots.map((slot, row) => (
                        <tr key={slot.hhmm} className="kp-heat-row" data-live={slot.live}>
                          <th>{slot.hhmm}</th>
                          {cols.map((u) => {
                            const s = boards?.[u.id]?.[row];
                            const flow =
                              s?.pcr != null && s.delta != null && Math.abs(s.delta) >= FLOW_MOVE_MIN
                                ? describeFlow(u.short, s.hhmm, s.pcr, s.delta, metric)
                                : null;
                            return (
                              <td key={u.id}>
                                <span className={`kp-heat kp-band-${s?.band ?? "empty"}`}>
                                  <span className="kp-heat-val">{s?.pcr == null ? "" : formatPcr(s.pcr)}</span>
                                  {flow ? (
                                    <span
                                      className={`kp-heat-flow-tag kp-act ${ideaKind(flow.action)}`}
                                      title={flow.why}
                                    >
                                      {flow.action}
                                    </span>
                                  ) : null}
                                </span>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : pathOn ? null : (
            <p className="kp-sub">{payload ? (picked.length ? "No F&O prints yet this session." : "Pick an index.") : "Loading put-call prints…"}</p>
          )}
          {payload && notesOn ? (
            <div className="kp-notes">
              {prefs.layout === "table" && showSec("read") ? (
                <div className="kp-card kp-read">
                  <p className="kp-kicker">Read · {PCR_INDICES.find((u) => u.id === index)?.short}</p>
                  <h2>{insight.headline}</h2>
                  <p className="kp-sub">{insight.reason}</p>
                  <div className="kp-read-play">
                    <span className={`kp-play-tag kp-act ${ideaKind(insight.action)}`}>{insight.action}</span>
                    <p className="kp-sub">{insight.play}</p>
                  </div>
                  <div className="kp-conv">
                    <div>
                      <div className="lab">Bias</div>
                      <div className={`val ${insight.bias === "Bullish" ? "text-up" : insight.bias === "Bearish" ? "text-down" : ""}`}>{insight.bias}</div>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div className="lab">Conviction {insight.conviction}</div>
                      <div className="kp-bar"><span style={{ width: `${insight.conviction}%` }} /></div>
                    </div>
                    <div>
                      <div className="lab">Regime</div>
                      <div className="val">{insight.regime}</div>
                    </div>
                  </div>
                </div>
              ) : null}
              {showSec("tape") ? (
                <aside className="kp-card kp-tape">
                  <p className="kp-kicker">Flow tape</p>
                  {tape.length ? (
                    <ul>
                      {tape.map((e) => (
                        <li key={e.id}>
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                            <b>{e.name} · <span className={`kp-act ${ideaKind(e.action)}`}>{e.action}</span></b>
                            <span className="kp-sub">{e.clock}</span>
                          </div>
                          <div className="kp-sub" style={{ marginTop: 3 }}>{e.why}</div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="kp-sub" style={{ marginTop: 10 }}>Quiet book — no 6-tick PCR jumps yet.</p>
                  )}
                </aside>
              ) : null}
              {showSec("legend") ? (
                <section className="kp-card kp-legend" aria-label="How to read PCR">
                  <p className="kp-kicker">How to read PCR</p>
                  <ul>
                    {(["positive", "highly-positive", "extreme-positive", "negative", "highly-negative", "extreme-negative"] as const).map((id) => (
                      <li key={id}>
                        <i className={`kp-swatch kp-band-${id}`} />
                        {BAND_COPY[id].hint}
                      </li>
                    ))}
                  </ul>
                  <p className="kp-sub" style={{ marginTop: 8 }}>
                    OI PCR is put open interest ÷ call open interest on the front weekly expiry (monthly when that is the listed series). Watch the 15-minute change, not a single print.
                  </p>
                </section>
              ) : null}
            </div>
          ) : null}
          <p className="kp-foot">Settings → Indices to pick Nifty / Bank / Fin / Sensex / Midcap. 1–5 toggle · A all · P path</p>
        </div>
      </div>
    </div>
  );
}
