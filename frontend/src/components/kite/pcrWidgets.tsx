import { formatDelta, formatExpiry, formatPcr, type PcrAction, type PcrRead, type Stance } from "../../lib/pcr/slots";
import { type PcrBand, type PcrIndex, type PcrSlot, PCR_INDICES } from "../../lib/pcr/types";
import { getIstParts } from "../../lib/astro/time";

export function nowMinutes(now: Date): number {
  const p = getIstParts(now);
  return p.hour * 60 + p.minute;
}

export function fmtLtp(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

export function ideaKind(action: PcrAction): "ce" | "pe" | "wait" {
  if (action === "Buy CE") return "ce";
  if (action === "Buy PE") return "pe";
  return "wait";
}

export function moveTxt(n: number): string {
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

export function stanceLab(s: Stance): string {
  if (s === "agrees") return "agrees";
  if (s === "fights") return "fights";
  return "—";
}

export function playHint(action: PcrAction): string {
  if (action === "Buy PE") return "Skip CE";
  if (action === "Buy CE") return "Skip PE";
  return "0.80–1.20";
}

export function bandLine(band: PcrBand): string {
  if (band === "extreme-positive") return "extreme positive";
  if (band === "highly-positive") return "highly positive";
  if (band === "positive") return "constructive";
  if (band === "negative") return "mild bearish";
  if (band === "highly-negative") return "highly negative";
  if (band === "extreme-negative") return "crowded calls";
  return "balanced";
}

export function expiryLong(expiryIso: string, kind: "weekly" | "monthly" | "today"): string {
  if (!expiryIso) return "—";
  const [y, m, d] = expiryIso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return formatExpiry(expiryIso);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][dt.getUTCDay()];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"];
  const k = kind === "today" ? "Today" : kind === "monthly" ? "Monthly" : "Weekly";
  return `${k} · ${wd}, ${d} ${months[m - 1]}`;
}

export const PREF_KEY = "sterling.pcr.desk.v1";

export const TILE_FIELDS = [
  { id: "print", label: "PCR print" },
  { id: "stamp", label: "Time / band" },
  { id: "split", label: "Puts / Calls" },
  { id: "headline", label: "Headline" },
  { id: "reason", label: "Read" },
  { id: "play", label: "Play" },
  { id: "bias", label: "Bias" },
  { id: "conviction", label: "Conviction" },
  { id: "regime", label: "Regime" },
  { id: "tape", label: "Flow tape" },
  { id: "delta", label: "Δ 15m" },
  { id: "spot", label: "Spot" },
  { id: "pain", label: "Max pain" },
  { id: "expiry", label: "Expiry" },
  { id: "putOi", label: "Put OI" },
  { id: "callOi", label: "Call OI" },
] as const;

export const SECTIONS = [
  { id: "book", label: "Book" },
  { id: "heat", label: "Heat" },
  { id: "tape", label: "Flow tape" },
  { id: "legend", label: "How to read" },
  { id: "read", label: "Read card" },
] as const;

export const TABLE_COLS = [
  { id: "play", label: "Play" },
  { id: "pcr", label: "OI PCR" },
  { id: "move", label: "Move" },
  { id: "vol", label: "Vol" },
  { id: "doi", label: "ΔOI" },
  { id: "spot", label: "Spot" },
  { id: "pc", label: "P/C" },
  { id: "expiry", label: "Expiry" },
  { id: "pain", label: "Pain" },
] as const;

export type TileField = (typeof TILE_FIELDS)[number]["id"];
export type SectionId = (typeof SECTIONS)[number]["id"];
export type ColId = (typeof TABLE_COLS)[number]["id"];
export type Layout = "table" | "tiles";

export type Prefs = {
  layout: Layout;
  sections: Record<SectionId, boolean>;
  tile: Record<TileField, boolean>;
  cols: Record<ColId, boolean>;
  indices: PcrIndex[];
  path: boolean;
};

export const DEFAULT_PREFS: Prefs = {
  layout: "tiles",
  sections: { book: true, heat: true, tape: true, legend: true, read: true },
  tile: Object.fromEntries(TILE_FIELDS.map((f) => [f.id, true])) as Prefs["tile"],
  cols: Object.fromEntries(TABLE_COLS.map((c) => [c.id, true])) as Prefs["cols"],
  indices: PCR_INDICES.map((u) => u.id),
  path: false,
};

function clonePrefs(p: Prefs): Prefs {
  return {
    layout: p.layout,
    sections: { ...p.sections },
    tile: { ...p.tile },
    cols: { ...p.cols },
    indices: [...p.indices],
    path: p.path,
  };
}

export function normalizeIndices(raw: unknown): PcrIndex[] {
  if (!Array.isArray(raw)) return [...DEFAULT_PREFS.indices];
  return DEFAULT_PREFS.indices.filter((id) => (raw as unknown[]).includes(id));
}

export function savePrefs(p: Prefs): void {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(p));
  } catch {
    /* private mode / quota */
  }
}

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (!raw) return clonePrefs(DEFAULT_PREFS);
    const p = JSON.parse(raw) as Partial<Prefs>;
    return {
      layout: p.layout === "table" ? "table" : "tiles",
      sections: { ...DEFAULT_PREFS.sections, ...p.sections },
      tile: { ...DEFAULT_PREFS.tile, ...p.tile },
      cols: { ...DEFAULT_PREFS.cols, ...p.cols },
      indices: Object.prototype.hasOwnProperty.call(p, "indices")
        ? normalizeIndices(p.indices)
        : [...DEFAULT_PREFS.indices],
      path: Boolean(p.path),
    };
  } catch {
    return clonePrefs(DEFAULT_PREFS);
  }
}

export type FlowTapeItem = {
  id: string;
  name: string;
  action: PcrAction;
  why: string;
  clock: string;
  hhmm: string;
  from?: number | null;
  to?: number | null;
  move?: number;
};

export type DeskRow = {
  id: PcrIndex;
  name: string;
  pcr: number | null;
  action: PcrAction;
  why: string;
  path: string;
  move: number | null;
  vol: Stance;
  doi: Stance;
  spot: number | null;
  spotChg: number | null;
  delta: number | null;
  putPct: number | null;
  expiry: string;
  expiryLong: string;
  maxPain: number | null;
  hhmm: string;
  band: PcrBand;
  insight: PcrRead;
  flowAction?: PcrAction;
  flowWhy?: string;
  tape?: FlowTapeItem[];
};

export function IndexTile({ row, show }: { row: DeskRow; show: (id: TileField) => boolean }) {
  const put = row.putPct == null ? null : Math.round(row.putPct * 100);
  const call = put == null ? null : 100 - put;
  const kind = ideaKind(row.action);
  return (
    <article className="kp-tile">
      <div className="kp-tile-top">
        <span className="kp-tile-name">{row.name}</span>
      </div>
      {show("print") || show("play") || show("stamp") ? (
        <div className="kp-tile-hero">
          {show("print") ? (
            <div className={`kp-tile-pcr kp-band-${row.band}`}>
              {row.pcr != null ? formatPcr(row.pcr) : "—"}
            </div>
          ) : null}
          {show("play") || show("stamp") ? (
            <div className="kp-tile-side">
              {show("play") ? (
                <div
                  className={`kp-tile-action kp-act ${kind}`}
                  title={row.flowWhy || row.insight.reason || undefined}
                >
                  {row.action}
                </div>
              ) : null}
              {show("stamp") ? (
                <div className="kp-tile-stamp">
                  <span className="kp-tile-time">{row.hhmm || "—"}</span>
                  {row.band !== "empty" || row.pcr != null ? (
                    <span className="kp-tile-band"> · {bandLine(row.band)}</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {show("split") && put != null ? (
        <div className="kp-split-wrap">
          <div className="kp-split-lab"><span>Puts {put}%</span><span>Calls {call}%</span></div>
          <div className="kp-split">
            <span className="kp-split-put" style={{ width: `${put}%` }} />
            <span className="kp-split-call" style={{ width: `${call}%` }} />
          </div>
        </div>
      ) : null}
      {show("headline") ? <h2>{row.insight.headline}</h2> : null}
      {show("reason") ? <p className="kp-sub">{row.insight.reason}</p> : null}
      {show("play") ? (
        <div className="kp-read-play">
          <span className={`kp-play-tag kp-act ${ideaKind(row.insight.action)}`}>{row.insight.action}</span>
          <p className="kp-sub">{row.insight.play}</p>
        </div>
      ) : null}
      {(show("bias") || show("conviction") || show("regime")) ? (
        <div className="kp-conv">
          {show("bias") ? (
            <div>
              <div className="lab">Bias</div>
              <div className={`val ${row.insight.bias === "Bullish" ? "text-up" : row.insight.bias === "Bearish" ? "text-down" : ""}`}>{row.insight.bias}</div>
            </div>
          ) : null}
          {show("conviction") ? (
            <div style={{ flex: 1 }}>
              <div className="lab">Conviction {row.insight.conviction}</div>
              <div className="kp-bar"><span style={{ width: `${row.insight.conviction}%` }} /></div>
            </div>
          ) : null}
          {show("regime") ? (
            <div>
              <div className="lab">Regime</div>
              <div className="val">{row.insight.regime}</div>
            </div>
          ) : null}
        </div>
      ) : null}
      {show("tape") && row.tape && row.tape.length > 0 ? (
        <div className="kp-tile-tape">
          <div className="kp-tile-tape-title">
            <span>Flow tape</span>
            <span className="kp-sub">{row.tape[0].clock}</span>
          </div>
          <div className="kp-tile-tape-list">
            {row.tape.slice(0, 2).map((e) => (
              <div key={e.id} className="kp-tile-tape-item">
                <div className="kp-tile-tape-top">
                  <span className={`kp-act ${ideaKind(e.action)}`}>{e.action}</span>
                  <span className="kp-tile-tape-path">
                    {e.from != null && e.to != null ? `${e.from.toFixed(2)} → ${e.to.toFixed(2)}` : ""}
                    {e.move != null ? ` (${e.move > 0 ? "+" : ""}${e.move.toFixed(2)})` : ""}
                  </span>
                  <span className="kp-sub" style={{ marginLeft: "auto" }}>{e.clock}</span>
                </div>
                <div className="kp-sub kp-tile-tape-why">{e.why}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div className="kp-stats">
        {show("delta") ? (
          <div>
            <div className="lab">Δ 15m</div>
            <div className={`val ${(row.delta ?? 0) > 0 ? "text-up" : (row.delta ?? 0) < 0 ? "text-down" : ""}`}>{formatDelta(row.delta)}</div>
          </div>
        ) : null}
        {show("spot") ? (
          <div>
            <div className="lab">Spot</div>
            <div className="val">
              {fmtLtp(row.spot)}
              {row.spotChg != null ? (
                <span className={row.spotChg >= 0 ? "text-up" : "text-down"}>
                  {" "}{row.spotChg >= 0 ? "+" : ""}{row.spotChg.toFixed(2)}%
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
        {show("pain") ? (
          <div>
            <div className="lab">Max pain</div>
            <div className="val">{fmtLtp(row.maxPain)}</div>
          </div>
        ) : null}
        {show("expiry") ? (
          <div>
            <div className="lab">Expiry</div>
            <div className="val">{row.expiryLong}</div>
          </div>
        ) : null}
        {show("putOi") ? (
          <div>
            <div className="lab">Put OI</div>
            <div className="val">—</div>
          </div>
        ) : null}
        {show("callOi") ? (
          <div>
            <div className="lab">Call OI</div>
            <div className="val">—</div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export function Path({ slots, marks }: { slots: PcrSlot[]; marks: { hhmm: string; indexClose: number }[] }) {
  const by = new Map(marks.map((m) => [m.hhmm, m.indexClose]));
  const pts = slots.filter((s) => s.pcr != null);
  if (pts.length < 2) {
    return <p className="kp-muted">Path fills as 15-minute prints land.</p>;
  }
  const w = 640;
  const h = 220;
  const pcrs = pts.map((s) => s.pcr as number);
  const spots = pts.map((s) => by.get(s.hhmm)).filter((v): v is number => v != null && v > 0);
  const pLo = Math.min(...pcrs, 0.5);
  const pHi = Math.max(...pcrs, 1.4);
  const sLo = spots.length ? Math.min(...spots) : 0;
  const sHi = spots.length ? Math.max(...spots) : 1;
  const py = (v: number) => h - 16 - ((v - pLo) / (pHi - pLo || 1)) * (h - 32);
  const sy = (v: number) => h - 16 - ((v - sLo) / (sHi - sLo || 1)) * (h - 32);
  const x = (i: number) => 8 + (i / (pts.length - 1)) * (w - 16);
  const pPath = pts.map((s, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${py(s.pcr as number).toFixed(1)}`).join(" ");
  const sPath = pts
    .map((s, i) => {
      const sp = by.get(s.hhmm);
      if (sp == null) return null;
      return `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${sy(sp).toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  return (
    <div className="kp-card kp-path">
      <p className="kp-kicker">PCR vs spot</p>
      <svg viewBox={`0 0 ${w} ${h}`} className="kp-path-svg" role="img" aria-label="PCR versus spot">
        <path d={pPath} className="kp-path-pcr" />
        {sPath ? <path d={sPath} className="kp-path-spot" /> : null}
      </svg>
      <div className="kp-path-key">
        <span><i className="kp-key-pcr" /> PCR</span>
        <span><i className="kp-key-spot" /> Spot</span>
      </div>
    </div>
  );
}
