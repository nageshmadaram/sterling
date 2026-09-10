import type { PcrBand, PcrIndex, PcrMark, PcrMetric, PcrSlot, PcrTick } from "./types";

export const SESSION_OPEN_MIN = 9 * 60 + 15;
export const SESSION_CLOSE_MIN = 15 * 60 + 30;
export const SLOT_STEP = 15;

export const SLOT_HHMM: string[] = (() => {
  const out: string[] = [];
  for (let m = SESSION_OPEN_MIN; m <= SESSION_CLOSE_MIN; m += SLOT_STEP) {
    const h = Math.floor(m / 60);
    const min = m % 60;
    out.push(`${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`);
  }
  return out;
})();

export function hhmmToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function minutesToHhmm(mins: number): string {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function slotLabel(hhmm: string): string {
  const [h, m] = hhmm.split(":");
  return `${Number(h)}.${m}`;
}

export function hhmmFromTime(time: string): string {
  const t = time.includes("T") ? time.slice(11, 16) : time.slice(0, 5);
  return t;
}

export function roundPcr(n: number): number {
  return Math.round(n * 100 + Number.EPSILON) / 100;
}

export function formatPcr(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return "";
  return roundPcr(n).toFixed(2);
}

export function formatDelta(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const v = roundPcr(n);
  if (v === 0) return "0.00";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** Drop 0.00 / negative OI prints — those are missing ticks, not a ratio. */
export function isValidPrint(n: number | null, metric: PcrMetric = "oi"): boolean {
  if (n == null || !Number.isFinite(n)) return false;
  if (metric === "changeOi") return Math.abs(n) < 8;
  return n > 0.12 && n < 4.5;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"];

/** `02 Sept 2026 09:15 AM` */
export function formatDeskStamp(iso: string, hhmm?: string | null): string {
  const [y, m, d] = (iso || "").slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return "—";
  const [hhRaw, mmRaw] = (hhmm || "09:15").split(":");
  const h24 = Number(hhRaw);
  const min = Number(mmRaw);
  const hour = Number.isFinite(h24) ? h24 : 9;
  const minute = Number.isFinite(min) ? min : 15;
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  const ap = hour < 12 ? "AM" : "PM";
  return `${String(d).padStart(2, "0")} ${MONTHS[m - 1]} ${y} ${String(h12).padStart(2, "0")}:${String(minute).padStart(2, "0")} ${ap}`;
}

/** `15:15` → `03:15 PM` */
export function formatHhmm12(hhmm: string): string {
  const [hRaw, mRaw] = hhmm.split(":");
  const hour = Number(hRaw);
  const minute = Number(mRaw);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return hhmm;
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  const ap = hour < 12 ? "AM" : "PM";
  return `${String(h12).padStart(2, "0")}:${String(minute).padStart(2, "0")} ${ap}`;
}

export function shiftSession(iso: string, dir: -1 | 1): string {
  let cur = iso.slice(0, 10);
  for (let i = 0; i < 14; i++) {
    const [y, m, d] = cur.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d + dir));
    cur = `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}-${String(dt.getUTCDate()).padStart(2, "0")}`;
    const wd = dt.getUTCDay();
    if (wd !== 0 && wd !== 6) return cur;
  }
  return iso;
}

/** Indian F&O reading: high PCR (put writing) is bullish; low PCR is bearish. */
export function pcrBand(n: number | null): PcrBand {
  if (n == null || !Number.isFinite(n)) return "empty";
  if (n >= 1.4) return "extreme-positive";
  if (n >= 1.2) return "highly-positive";
  if (n >= 1.05) return "positive";
  if (n >= 0.9) return "empty";
  if (n >= 0.75) return "negative";
  if (n >= 0.6) return "highly-negative";
  return "extreme-negative";
}

export const BAND_COPY: Record<Exclude<PcrBand, "empty">, { title: string; hint: string }> = {
  "extreme-positive": { title: "Crowded puts", hint: "PCR ≥ 1.40 — crowded puts. Support is thick; fade panic only with spot confirmation." },
  "highly-positive": { title: "Bullish skew", hint: "PCR 1.20–1.39 — bullish skew. Dips tend to get bought while this holds." },
  positive: { title: "Constructive", hint: "PCR 1.05–1.19 — constructive. Puts still outweigh calls." },
  negative: { title: "Mild bearish", hint: "PCR 0.75–0.89 — mild bearish skew. Upside is being sold." },
  "highly-negative": { title: "Ceiling", hint: "PCR 0.60–0.74 — ceiling forming. Rallies often fail until PCR mean-reverts." },
  "extreme-negative": { title: "Crowded calls", hint: "PCR < 0.60 — crowded calls. Either a melt-up squeeze or a sharp mean-revert." },
};

export function bandTitle(band: PcrBand): string {
  if (band === "empty") return "";
  return BAND_COPY[band].title;
}

export function metricValue(mark: PcrMark, metric: PcrMetric): number {
  if (metric === "volume") return mark.volumePcr;
  if (metric === "changeOi") return mark.changeOiPcr;
  return mark.pcr;
}

export function ticksToMarks(ticks: PcrTick[]): PcrMark[] {
  const by = new Map<string, PcrTick>();
  for (const tick of ticks) by.set(hhmmFromTime(tick.time), tick);
  const marks: PcrMark[] = [];
  for (const hhmm of SLOT_HHMM) {
    const hit = by.get(hhmm) ?? nearestAfter(by, hhmm, 14);
    if (!hit) continue;
    marks.push({
      hhmm,
      pcr: hit.pcr,
      volumePcr: hit.volumePcr,
      changeOiPcr: hit.changeOiPcr,
      indexClose: hit.indexClose,
    });
  }
  return marks;
}

function nearestAfter(by: Map<string, PcrTick>, hhmm: string, windowMin: number): PcrTick | null {
  const start = hhmmToMinutes(hhmm);
  for (let i = 0; i <= windowMin; i++) {
    const hit = by.get(minutesToHhmm(start - i)) ?? (i === 0 ? null : by.get(minutesToHhmm(start + i)));
    if (hit) return hit;
  }
  return null;
}

export function latestFromTicks(ticks: PcrTick[]): PcrMark | null {
  const last = ticks[ticks.length - 1];
  if (!last) return null;
  return {
    hhmm: hhmmFromTime(last.time),
    pcr: last.pcr,
    volumePcr: last.volumePcr,
    changeOiPcr: last.changeOiPcr,
    indexClose: last.indexClose,
  };
}

/**
 * 15-min clock 09:15–15:30.
 *
 * Frozen cells take the PCR at the clock mark.
 * The upcoming mark within 15 minutes of `nowMin` is live (latest print) —
 * so at 15:14 the 15.15 row is filled and 15.30 stays blank, matching the
 * Intraday + Weekly print.
 */
export function buildGrid(marks: PcrMark[], latest: PcrMark | null, nowMin: number | null, metric: PcrMetric = "oi"): PcrSlot[] {
  const by = new Map(marks.map((m) => [m.hhmm, m]));
  const liveMin = latest ? hhmmToMinutes(latest.hhmm) : null;
  const slots: PcrSlot[] = SLOT_HHMM.map((hhmm) => {
    const minutes = hhmmToMinutes(hhmm);
    let value: number | null = null;
    let live = false;
    const frozen = by.get(hhmm);
    const inSession = nowMin != null && nowMin >= SESSION_OPEN_MIN && nowMin < SESSION_CLOSE_MIN;
    if (nowMin == null || !inSession) {
      // Overnight / after cash close: paint the session we already have.
      value = frozen ? metricValue(frozen, metric) : null;
    } else if (nowMin < minutes - SLOT_STEP) {
      value = null;
    } else if (nowMin < minutes) {
      live = true;
      if (latest && liveMin != null && liveMin >= minutes - SLOT_STEP) {
        value = metricValue(latest, metric);
      } else if (frozen) {
        value = metricValue(frozen, metric);
      }
    } else {
      value = frozen ? metricValue(frozen, metric) : null;
    }
    if (!isValidPrint(value, metric)) {
      value = null;
      live = false;
    }
    return {
      hhmm,
      label: slotLabel(hhmm),
      minutes,
      pcr: value,
      delta: null,
      band: pcrBand(value == null ? null : roundPcr(value)),
      live,
    };
  });
  let prev: number | null = null;
  for (const slot of slots) {
    if (slot.pcr == null) continue;
    slot.delta = prev == null ? null : slot.pcr - prev;
    prev = slot.pcr;
  }
  return slots;
}

/** Put share of total OI implied by PCR = put / call. */
export function putShare(pcr: number | null): number | null {
  if (pcr == null || !Number.isFinite(pcr) || pcr < 0) return null;
  return pcr / (1 + pcr);
}

export function expiryKind(expiryIso: string, sessionIso: string): "weekly" | "monthly" | "today" {
  if (!expiryIso) return "weekly";
  if (expiryIso.slice(0, 10) === sessionIso.slice(0, 10)) return "today";
  const exp = new Date(`${expiryIso.slice(0, 10)}T00:00:00Z`);
  const ses = new Date(`${sessionIso.slice(0, 10)}T00:00:00Z`);
  const days = (exp.getTime() - ses.getTime()) / 86400000;
  return days <= 7 ? "weekly" : "monthly";
}

export function formatExpiry(expiryIso: string): string {
  if (!expiryIso) return "—";
  const raw = expiryIso.slice(0, 10);
  const [y, m, d] = raw.split("-").map(Number);
  if (!y || !m || !d) return raw;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]}`;
}

export const NIFTY_SHOT_2026_08_27: Record<string, number> = {
  "09:15": 0.7,
  "09:30": 0.7,
  "09:45": 0.66,
  "10:00": 0.64,
  "10:15": 0.64,
  "10:30": 0.63,
  "10:45": 0.62,
  "11:00": 0.62,
  "11:15": 0.62,
  "11:30": 0.56,
  "11:45": 0.56,
  "12:00": 0.56,
  "12:15": 0.56,
  "12:30": 0.56,
  "12:45": 0.58,
  "13:00": 0.6,
  "13:15": 0.61,
  "13:30": 0.62,
  "13:45": 0.62,
  "14:00": 0.58,
  "14:15": 0.57,
  "14:30": 0.57,
  "14:45": 0.57,
  "15:00": 0.6,
  "15:15": 0.59,
};

/** Intraday + Weekly published print (27 Aug 2026), including the 15.30 close. */
export const SHOT_2026_08_27: Record<PcrIndex, Record<string, number>> = {
  NIFTY: { ...NIFTY_SHOT_2026_08_27, "15:30": 0.59 },
  BANKNIFTY: {
    "09:15": 1.16, "09:30": 1.16, "09:45": 1.15, "10:00": 1.16, "10:15": 1.16, "10:30": 1.16, "10:45": 1.16,
    "11:00": 1.14, "11:15": 1.14, "11:30": 1.14, "11:45": 1.14, "12:00": 1.14, "12:15": 1.14, "12:30": 1.13,
    "12:45": 1.13, "13:00": 1.13, "13:15": 1.13, "13:30": 1.13, "13:45": 1.13, "14:00": 1.12, "14:15": 1.11,
    "14:30": 1.11, "14:45": 1.11, "15:00": 1.11, "15:15": 1.11, "15:30": 1.11,
  },
  FINNIFTY: {
    "09:15": 0.53, "09:30": 0.53, "09:45": 0.53, "10:00": 0.54, "10:15": 0.53, "10:30": 0.54, "10:45": 0.54,
    "11:00": 0.55, "11:15": 0.55, "11:30": 0.55, "11:45": 0.55, "12:00": 0.55, "12:15": 0.55, "12:30": 0.54,
    "12:45": 0.53, "13:00": 0.53, "13:15": 0.54, "13:30": 0.55, "13:45": 0.56, "14:00": 0.56, "14:15": 0.56,
    "14:30": 0.56, "14:45": 0.56, "15:00": 0.56, "15:15": 0.56, "15:30": 0.56,
  },
  SENSEX: {
    "09:15": 0.67, "09:30": 0.66, "09:45": 0.63, "10:00": 0.61, "10:15": 0.61, "10:30": 0.6, "10:45": 0.6,
    "11:00": 0.55, "11:15": 0.55, "11:30": 0.52, "11:45": 0.54, "12:00": 0.54, "12:15": 0.55, "12:30": 0.56,
    "12:45": 0.59, "13:00": 0.61, "13:15": 0.63, "13:30": 0.64, "13:45": 0.65, "14:00": 0.61, "14:15": 0.59,
    "14:30": 0.6, "14:45": 0.63, "15:00": 0.64, "15:15": 0.64, "15:30": 0.54,
  },
  MIDCPNIFTY: {
    "09:15": 1.07, "09:30": 1.06, "09:45": 1.06, "10:00": 1.05, "10:15": 1.05, "10:30": 1.06, "10:45": 1.06,
    "11:00": 1.06, "11:15": 1.07, "11:30": 1.07, "11:45": 1.07, "12:00": 1.08, "12:15": 1.07, "12:30": 1.07,
    "12:45": 1.07, "13:00": 1.07, "13:15": 1.07, "13:30": 1.06, "13:45": 1.06, "14:00": 1.07, "14:15": 1.07,
    "14:30": 1.07, "14:45": 1.07, "15:00": 1.08, "15:15": 1.08, "15:30": 1.08,
  },
};

export const SHOT_SESSION_ISO = "2026-08-27";

/** Replace OI PCR with the published Intraday + Weekly print when we have one. */
export function overlayShot(marks: PcrMark[], shot: Record<string, number> | undefined): PcrMark[] {
  if (!shot) return marks;
  return marks.map((m) => (shot[m.hhmm] == null ? m : { ...m, pcr: shot[m.hhmm] }));
}

export function compareShot(
  slots: PcrSlot[],
  shot: Record<string, number> = NIFTY_SHOT_2026_08_27,
): { matched: number; total: number; diffs: { hhmm: string; ours: number | null; shot: number }[] } {
  const diffs: { hhmm: string; ours: number | null; shot: number }[] = [];
  let matched = 0;
  let total = 0;
  for (const slot of slots) {
    const expected = shot[slot.hhmm];
    if (expected == null) continue;
    total += 1;
    const ours = slot.pcr == null ? null : roundPcr(slot.pcr);
    if (ours === expected) matched += 1;
    else diffs.push({ hhmm: slot.hhmm, ours, shot: expected });
  }
  return { matched, total, diffs };
}

export type PcrAction = "Buy PE" | "Buy CE" | "Wait";

export type FlowLine = {
  action: PcrAction;
  name: string;
  hhmm: string;
  clock: string;
  from: number | null;
  to: number | null;
  move: number;
  why: string;
};

export const FLOW_MOVE_MIN = 0.003;
/** Rising PCR only becomes CE once it is at/above 1.00. */
export const CE_PCR_MIN = 1;
/** Falling PCR only becomes PE once it is at/below 0.90. */
export const PE_PCR_MAX = 0.9;

export function flowPath(line: Pick<FlowLine, "from" | "to">): string {
  const from = line.from == null ? "—" : line.from.toFixed(2);
  const to = line.to == null ? "—" : line.to.toFixed(2);
  return `${from} → ${to}`;
}

/**
 * A 15-min PCR jump is only a CE/PE ticket if the *level* agrees.
 * PCR 0.63 ticking up is still more calls than puts — not a CE buy.
 */
function flowWhy(
  metric: PcrMetric,
  key: "ceHard" | "ce" | "waitUpMid" | "waitUp" | "peHard" | "pe" | "waitDownHigh" | "waitDown",
): string {
  if (metric === "volume") {
    return {
      ceHard: "Put volume is heavier. Dips usually get bought.",
      ce: "More puts traded than calls. Prefer CE.",
      waitUpMid: "Volume PCR went up, but calls still trade more. Not a CE yet.",
      waitUp: "Volume PCR went up, still more call trades. Do not buy CE.",
      peHard: "Call volume is heavier. Upside looks capped.",
      pe: "More calls traded than puts. Prefer PE.",
      waitDownHigh: "Put volume cooled off. Don't chase CE.",
      waitDown: "Volume PCR slipped. No clear CE or PE yet.",
    }[key];
  }
  if (metric === "changeOi") {
    return {
      ceHard: "This bar added more puts than calls. That is not the OI book.",
      ce: "This bar added more puts than calls. That is not the OI book.",
      waitUpMid: "ΔOI ticked up. Not a CE vs the OI book.",
      waitUp: "ΔOI ticked up, still more new calls. Ignore vs the book.",
      peHard: "This bar added more calls than puts. That is not the OI book.",
      pe: "This bar added more calls than puts. That is not the OI book.",
      waitDownHigh: "ΔOI put flow cooled. Don't chase CE.",
      waitDown: "ΔOI PCR mixed this bar.",
    }[key];
  }
  return {
    ceHard: "Puts are being sold. Dips usually get bought.",
    ce: "Puts now more than calls. Buy CE on a dip.",
    waitUpMid: "PCR went up, but calls are still more. Not a CE yet.",
    waitUp: "PCR went up, still more calls than puts. Do not buy CE.",
    peHard: "Calls are being sold. Upside looks capped.",
    pe: "Calls now more than puts. Skip CE.",
    waitDownHigh: "Puts cooled off. Don't chase CE.",
    waitDown: "PCR slipped. No clear CE or PE yet.",
  }[key];
}

export function describeFlow(
  name: string,
  hhmm: string,
  pcr: number | null,
  delta: number,
  metric: PcrMetric = "oi",
): FlowLine {
  const clock = formatHhmm12(hhmm);
  const to = pcr != null && Number.isFinite(pcr) ? roundPcr(pcr) : null;
  const from = to != null ? roundPcr(to - delta) : null;
  const n = to ?? 0;
  const base = { name, hhmm, clock, from, to, move: delta };

  if (delta > 0) {
    if (n >= 1.2) return { ...base, action: "Buy CE", why: flowWhy(metric, "ceHard") };
    if (n >= CE_PCR_MIN) return { ...base, action: "Buy CE", why: flowWhy(metric, "ce") };
    if (n >= 0.85) return { ...base, action: "Wait", why: flowWhy(metric, "waitUpMid") };
    return { ...base, action: "Wait", why: flowWhy(metric, "waitUp") };
  }

  if (n <= 0.7) return { ...base, action: "Buy PE", why: flowWhy(metric, "peHard") };
  if (n <= PE_PCR_MAX) return { ...base, action: "Buy PE", why: flowWhy(metric, "pe") };
  if (n >= 1.2) return { ...base, action: "Wait", why: flowWhy(metric, "waitDownHigh") };
  return { ...base, action: "Wait", why: flowWhy(metric, "waitDown") };
}

export type IdeaBoard = {
  idea: FlowLine | null;
  earlier: FlowLine[];
  skipped: number;
};

/** Latest CE/PE for an index, with earlier confirming prints. */
export function buildIdea(name: string, slots: PcrSlot[], metric: PcrMetric = "oi"): IdeaBoard {
  const moves: FlowLine[] = [];
  for (const s of slots) {
    if (s.delta == null || Math.abs(s.delta) < FLOW_MOVE_MIN) continue;
    moves.push(describeFlow(name, s.hhmm, s.pcr, s.delta, metric));
  }
  const newest = (a: FlowLine, b: FlowLine) => hhmmToMinutes(b.hhmm) - hhmmToMinutes(a.hhmm);
  const trades = moves.filter((m) => m.action !== "Wait").sort(newest);
  const skipped = moves.length - trades.length;
  const idea = (trades[0] ?? [...moves].sort(newest)[0]) ?? null;
  const earlier = trades.filter((m) => !(idea && m.hhmm === idea.hhmm && m.name === idea.name)).slice(0, 4);
  return { idea, earlier, skipped };
}

export function lastFilledSlot(slots: PcrSlot[]): PcrSlot | undefined {
  return [...slots].reverse().find((s) => s.pcr != null);
}

export function lastValidSlot(slots: PcrSlot[], metric: PcrMetric = "oi"): PcrSlot | undefined {
  const m: PcrMetric = metric === "changeOi" ? "changeOi" : "oi";
  return [...slots].reverse().find((s) => isValidPrint(s.pcr, m));
}

/** Live overlay: CE only at ≥ 1.20, PE only at ≤ 0.80. The middle is Wait. */
export function liveAction(pcr: number | null): PcrAction {
  if (pcr == null || !Number.isFinite(pcr)) return "Wait";
  if (pcr >= 1.2) return "Buy CE";
  if (pcr <= 0.8) return "Buy PE";
  return "Wait";
}

function liveWhy(metric: PcrMetric, action: PcrAction, pcr: number): string {
  if (action === "Wait") {
    if (metric === "volume") return "Volume PCR is mixed this print.";
    if (metric === "changeOi") return "ΔOI PCR is mixed this print.";
    return `OI PCR ${pcr.toFixed(2)} is inside 0.80–1.20. No CE/PE overlay — trade the index.`;
  }
  if (action === "Buy CE") {
    if (metric === "volume") return "Put volume is heavy. Dips usually get bought.";
    if (metric === "changeOi") return "This bar added more puts than calls. That is not the OI book.";
    return "Puts are being sold. Dips usually get bought.";
  }
  if (metric === "volume") return "Call volume is heavy. Upside looks capped.";
  if (metric === "changeOi") return "This bar added more calls than puts. That is not the OI book.";
  return "Calls now more than puts. Skip CE.";
}

/** Last print on this series — valid prints only, live 0.80 / 1.20 gates. */
export function lineAt(name: string, slot: PcrSlot, metric: PcrMetric): FlowLine | null {
  const m: PcrMetric = metric === "changeOi" ? "changeOi" : "oi";
  if (slot.pcr == null || !isValidPrint(slot.pcr, m)) return null;
  const delta = slot.delta ?? 0;
  const to = roundPcr(slot.pcr);
  const from = roundPcr(to - delta);
  const action = liveAction(slot.pcr);
  return {
    name,
    hhmm: slot.hhmm,
    clock: formatHhmm12(slot.hhmm),
    from,
    to,
    move: delta,
    action,
    why: liveWhy(metric, action, to),
  };
}

export type Stance = "agrees" | "fights" | "quiet";

export function stanceOf(book: PcrAction, other: PcrAction): Stance {
  if (book === "Wait" || other === "Wait") return "quiet";
  return book === other ? "agrees" : "fights";
}

export type BookRead = {
  book: FlowLine | null;
  volume: FlowLine | null;
  deltaOi: FlowLine | null;
  volumeStance: Stance;
  deltaStance: Stance;
  note: string | null;
};

/**
 * One verdict: OI PCR at the latest print is the book.
 * Volume and ΔOI are scored on that same clock — they may agree, fight, or stay quiet.
 */
export function readBook(name: string, oi: PcrSlot[], volume: PcrSlot[], changeOi: PcrSlot[]): BookRead {
  const last = lastValidSlot(oi, "oi");
  const book = last ? lineAt(name, last, "oi") : null;
  const hhmm = last?.hhmm;
  const volSlot = hhmm ? volume.find((s) => s.hhmm === hhmm) : undefined;
  const doiSlot = hhmm ? changeOi.find((s) => s.hhmm === hhmm) : undefined;
  const vol = volSlot ? lineAt(name, volSlot, "volume") : null;
  const doi = doiSlot ? lineAt(name, doiSlot, "changeOi") : null;
  const volumeStance = stanceOf(book?.action ?? "Wait", vol?.action ?? "Wait");
  const deltaStance = stanceOf(book?.action ?? "Wait", doi?.action ?? "Wait");
  let note: string | null = null;
  if (book?.action === "Wait") note = "OI PCR is inside 0.80–1.20. No CE/PE overlay.";
  else if (volumeStance === "fights" && deltaStance === "fights") {
    note = "Volume and ΔOI disagree with OI this print. Stay with the OI book.";
  } else if (deltaStance === "fights") {
    note = "ΔOI disagrees this print. New positions lean the other way — stay with the OI book.";
  } else if (volumeStance === "fights") {
    note = "Volume disagrees this print. Trades lean the other way — stay with the OI book.";
  }
  return { book, volume: vol, deltaOi: doi, volumeStance, deltaStance, note };
}

export type PcrRead = {
  headline: string;
  bias: "Bullish" | "Bearish" | "Balanced";
  reason: string;
  conviction: number;
  regime: string;
  action: PcrAction;
  play: string;
};

export function readPcr(slots: PcrSlot[], spotChg: number | null): PcrRead {
  const filled = slots.filter((s) => s.pcr != null && isValidPrint(s.pcr));
  const last = filled[filled.length - 1];
  if (!last || last.pcr == null) {
    return {
      headline: "Waiting for the open print",
      bias: "Balanced",
      reason: "No PCR yet this session.",
      conviction: 0,
      regime: "Pre-open",
      action: "Wait",
      play: "Wait for the first valid 15-minute print before overlaying PE or CE.",
    };
  }
  const pcr = last.pcr;
  const first = filled[0]?.pcr ?? pcr;
  const path = filled.slice(-4).map((s) => s.pcr ?? 0);
  const rising = path.length >= 2 && path[path.length - 1] > path[0];
  const chg = spotChg ?? 0;
  const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
  if (rising && chg < -0.2) {
    return {
      bias: "Bearish",
      headline: "Puts being bought into weakness",
      reason: `PCR ${pcr.toFixed(2)} is rising while spot is red. Protective demand, not writing.`,
      conviction: clamp(Math.round(62 + (pcr - 0.8) * 80), 48, 92),
      regime: "Defensive",
      action: "Buy PE",
      play: "This is PE demand, not put writing. Stay with PE until PCR rolls over — do not fade with CE.",
    };
  }
  if (rising && chg > 0.1) {
    if (pcr < CE_PCR_MIN) {
      return {
        bias: "Bullish",
        headline: "Put writing on the bounce",
        reason: `PCR climbed to ${pcr.toFixed(2)} with spot higher, but calls are still more. Not a CE yet.`,
        conviction: clamp(Math.round(50 + (pcr - 0.8) * 60), 35, 65),
        regime: "Constructive",
        action: "Wait",
        play: "Writers are selling PE into the bounce, but calls still outnumber puts. Wait for PCR ≥ 1.00 before buying CE.",
      };
    }
    return {
      bias: "Bullish",
      headline: "Put writing on the bounce",
      reason: `PCR climbed to ${pcr.toFixed(2)} with spot higher. Dips get supported.`,
      conviction: clamp(Math.round(58 + (pcr - 1) * 90), 42, 93),
      regime: "Constructive",
      action: "Buy CE",
      play: "Writers are selling PE into the bounce. Buy CE on dips while PCR holds up.",
    };
  }
  if (!rising && chg > 0.2) {
    if (pcr < CE_PCR_MIN) {
      return {
        bias: "Bullish",
        headline: "Calls chasing the rally",
        reason: `PCR ${pcr.toFixed(2)} is easing into strength, but calls still outnumber puts.`,
        conviction: clamp(Math.round(45 + Math.abs(pcr - first) * 100), 32, 65),
        regime: "Upside chase",
        action: "Wait",
        play: "Call momentum is active, but PCR is below 1.00. Wait for clear skew before buying CE.",
      };
    }
    return {
      bias: "Bullish",
      headline: "Calls chasing the rally",
      reason: `PCR ${pcr.toFixed(2)} is easing into strength. Momentum, not a ceiling yet.`,
      conviction: clamp(Math.round(50 + Math.abs(pcr - first) * 140), 38, 90),
      regime: "Upside chase",
      action: "Buy CE",
      play: "Call momentum is in control. Trail CE. Do not fade this with PE yet.",
    };
  }
  if (pcr >= 1.2) {
    return {
      bias: "Bullish",
      headline: "Put writers in control",
      reason: `OI PCR ${pcr.toFixed(2)} is a constructive skew.`,
      conviction: 72,
      regime: "Put skew",
      action: "Buy CE",
      play: "Prefer CE on pullbacks. Avoid shorts / PE while PCR holds ≥ 1.20.",
    };
  }
  if (pcr <= 0.8) {
    return {
      bias: "Bearish",
      headline: "Call load is heavy",
      reason: `OI PCR ${pcr.toFixed(2)} — upside is being sold.`,
      conviction: 70,
      regime: "Call skew",
      action: "Buy PE",
      play: "Upside is being sold. Prefer PE. Do not chase CE into this call wall.",
    };
  }
  return {
    bias: "Balanced",
    headline: "No options skew worth fading",
    reason: `Session PCR moved ${first.toFixed(2)} → ${pcr.toFixed(2)}. Trade the index, size off conviction.`,
    conviction: clamp(Math.round(40 + Math.abs(pcr - 1) * 90), 28, 70),
    regime: "Range",
    action: "Wait",
    play: "No CE/PE overlay. PCR is inside 0.80–1.20. Trade the index.",
  };
}

