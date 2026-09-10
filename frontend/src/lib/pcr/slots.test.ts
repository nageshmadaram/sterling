import { describe, it, expect } from "vitest";
import {
  SLOT_HHMM,
  SHOT_2026_08_27,
  bandTitle,
  buildGrid,
  compareShot,
  describeFlow,
  buildIdea,
  liveAction,
  readBook,
  formatPcr,
  hhmmToMinutes,
  isValidPrint,
  overlayShot,
  pcrBand,
  putShare,
  readPcr,
  roundPcr,
  formatDeskStamp,
  slotLabel,
  ticksToMarks,
} from "./slots";
import { PCR_SNAPSHOT, snapshotSeries } from "./snapshot";
import { PCR_INDICES } from "./types";

describe("pcr slots", () => {
  it("prints a 15-min cash clock from 9.15 to 15.30", () => {
    expect(SLOT_HHMM.length).toBe(26);
    expect(SLOT_HHMM[0]).toBe("09:15");
    expect(SLOT_HHMM[SLOT_HHMM.length - 1]).toBe("15:30");
    expect(slotLabel("09:15")).toBe("9.15");
    expect(slotLabel("15:30")).toBe("15.30");
  });

  it("colours the screenshot legend bands", () => {
    expect(pcrBand(1.4)).toBe("extreme-positive");
    expect(pcrBand(1.2)).toBe("highly-positive");
    expect(pcrBand(1.05)).toBe("positive");
    expect(pcrBand(1.04)).toBe("empty");
    expect(pcrBand(1)).toBe("empty");
    expect(pcrBand(0.9)).toBe("empty");
    expect(pcrBand(0.89)).toBe("negative");
    expect(pcrBand(0.8)).toBe("negative");
    expect(pcrBand(0.75)).toBe("negative");
    expect(pcrBand(0.74)).toBe("highly-negative");
    expect(pcrBand(0.6)).toBe("highly-negative");
    expect(pcrBand(0.59)).toBe("extreme-negative");
    expect(pcrBand(0.56)).toBe("extreme-negative");
    expect(bandTitle("extreme-negative")).toBe("Crowded calls");
  });

  it("rounds to two decimals the way the print does", () => {
    expect(roundPcr(0.7005)).toBe(0.7);
    expect(formatPcr(0.7005)).toBe("0.70");
    expect(formatPcr(0.5575)).toBe("0.56");
    expect(formatPcr(0.5939)).toBe("0.59");
  });

  it("fills 15.15 as live at 15:14 and leaves 15.30 blank", () => {
    const nifty = PCR_SNAPSHOT.NIFTY;
    const grid = buildGrid(nifty.marks, { hhmm: "15:14", pcr: 0.5939, volumePcr: 1.04, changeOiPcr: 0.28, indexClose: 24133 }, 15 * 60 + 14);
    const s1515 = grid.find((s) => s.hhmm === "15:15");
    const s1530 = grid.find((s) => s.hhmm === "15:30");
    const s1500 = grid.find((s) => s.hhmm === "15:00");
    expect(s1515?.live).toBe(true);
    expect(roundPcr(s1515?.pcr ?? 0)).toBe(0.59);
    expect(s1530?.pcr).toBe(null);
    expect(s1500?.live).toBe(false);
    expect(roundPcr(s1500?.pcr ?? 0)).toBe(0.6);
  });

  it("paints the full session after cash close and before the next open", () => {
    const series = snapshotSeries("NIFTY");
    const after = buildGrid(series.marks, series.latest, 16 * 60);
    const pre = buildGrid(series.marks, series.latest, 8 * 60 + 10);
    expect(after.filter((s) => s.pcr != null).length).toBe(26);
    expect(pre.filter((s) => s.pcr != null).length).toBe(26);
    expect(after.find((s) => s.hhmm === "15:30")?.live).toBe(false);
  });

  it("matches the 27 Aug Nifty print on 25 of 25 published cells", () => {
    const series = snapshotSeries("NIFTY");
    const grid = buildGrid(series.marks, series.latest, 15 * 60 + 30);
    const cmp = compareShot(grid);
    expect(cmp.total).toBe(25);
    expect(cmp.matched, `only ${cmp.matched}/25 matched: ${JSON.stringify(cmp.diffs)}`).toBe(25);
  });

  it("matches every index on the 27 Aug Intraday + Weekly print", () => {
    for (const row of PCR_INDICES) {
      const series = snapshotSeries(row.id);
      const grid = buildGrid(series.marks, series.latest, 15 * 60 + 30);
      const cmp = compareShot(grid, SHOT_2026_08_27[row.id]);
      expect(cmp.total, row.id).toBe(26);
      expect(cmp.matched, `${row.id} ${cmp.matched}/26 ${JSON.stringify(cmp.diffs)}`).toBe(26);
    }
  });

  it("overlays shot PCR without dropping marks", () => {
    const raw = PCR_SNAPSHOT.NIFTY.marks;
    const over = overlayShot(raw, SHOT_2026_08_27.NIFTY);
    expect(over.length).toBe(raw.length);
    expect(roundPcr(over[0]?.pcr ?? 0)).toBe(0.7);
    expect(raw[0]?.pcr).toBe(0.7332);
  });

  it("keeps put share in (0,1) from PCR", () => {
    expect(Number(putShare(1)?.toFixed(2))).toBe(0.5);
    expect(putShare(0.59) ?? 0).toBeLessThan(0.4);
  });

  it("reads put writing vs protective buying from PCR vs spot", () => {
    expect(readPcr([], null).headline).toBe("Waiting for the open print");
    expect(readPcr([], null).action).toBe("Wait");
    const rising = [
      { hhmm: "09:15", label: "9.15", minutes: 555, pcr: 0.80, delta: null, band: "highly-negative" as const, live: false },
      { hhmm: "09:30", label: "9.30", minutes: 570, pcr: 0.90, delta: 0.10, band: "negative" as const, live: false },
      { hhmm: "09:45", label: "9.45", minutes: 585, pcr: 0.98, delta: 0.08, band: "negative" as const, live: true },
    ];
    expect(readPcr(rising, -0.4)).toMatchObject({ bias: "Bearish", headline: "Puts being bought into weakness", action: "Buy PE" });
    // PCR 0.98 is rising with spot up, but calls still outnumber puts (< 1.00) -> Wait
    expect(readPcr(rising, 0.3)).toMatchObject({ bias: "Bullish", headline: "Put writing on the bounce", action: "Wait" });
    // PCR >= 1.00 with spot up -> Buy CE
    const risingCrossed = [
      { hhmm: "09:15", label: "9.15", minutes: 555, pcr: 0.80, delta: null, band: "highly-negative" as const, live: false },
      { hhmm: "09:30", label: "9.30", minutes: 570, pcr: 0.95, delta: 0.15, band: "negative" as const, live: false },
      { hhmm: "09:45", label: "9.45", minutes: 585, pcr: 1.08, delta: 0.13, band: "positive" as const, live: true },
    ];
    expect(readPcr(risingCrossed, 0.3)).toMatchObject({ bias: "Bullish", headline: "Put writing on the bounce", action: "Buy CE" });
    const falling = [
      { hhmm: "09:15", label: "9.15", minutes: 555, pcr: 1.05, delta: null, band: "positive" as const, live: false },
      { hhmm: "09:30", label: "9.30", minutes: 570, pcr: 0.95, delta: -0.10, band: "negative" as const, live: true },
    ];
    expect(readPcr(falling, 0.5).headline).toBe("Calls chasing the rally");
    expect(readPcr(
      [{ hhmm: "09:15", label: "9.15", minutes: 555, pcr: 1.35, delta: null, band: "highly-positive" as const, live: true }],
      0,
    )).toMatchObject({ headline: "Put writers in control", action: "Buy CE" });
    expect(readPcr(
      [{ hhmm: "09:15", label: "9.15", minutes: 555, pcr: 0.62, delta: null, band: "highly-negative" as const, live: true }],
      0,
    )).toMatchObject({ headline: "Call load is heavy", action: "Buy PE" });
    expect(readPcr(
      [{ hhmm: "09:15", label: "9.15", minutes: 555, pcr: 0.95, delta: null, band: "negative" as const, live: true }],
      0,
    ).action).toBe("Wait");
  });

  it("formats the desk stamp as 02 Sept 2026 09:15 AM", () => {
    expect(formatDeskStamp("2026-09-02", "09:15")).toBe("02 Sept 2026 09:15 AM");
    expect(formatDeskStamp("2026-09-02", "15:30")).toBe("02 Sept 2026 03:30 PM");
    expect(isValidPrint(0)).toBe(false);
    expect(isValidPrint(0.74)).toBe(true);
    expect(isValidPrint(-2.32)).toBe(false);
  });

  it("says Buy CE or Buy PE in plain language on the flow tape", () => {
    const ce = describeFlow("Midcap", "14:45", 1.21, 0.11);
    expect(ce.action).toBe("Buy CE");
    expect(ce.why).toMatch(/Puts/i);
    expect(ce.why).not.toMatch(/thickening|taking share|call-heavy/i);
    const pe = describeFlow("Nifty", "15:15", 0.76, -0.07);
    expect(pe.action).toBe("Buy PE");
    expect(pe.clock).toBe("03:15 PM");
    const fakeCe = describeFlow("Fin", "15:30", 0.63, 0.07);
    expect(fakeCe.action).toBe("Wait");
    expect(fakeCe.why).toMatch(/more calls than puts/i);
    const slipped = describeFlow("Sensex", "12:45", 0.78, 0.09);
    expect(slipped.action).toBe("Wait");
  });

  it("writes volume PCR copy separately from OI", () => {
    const ce = describeFlow("Nifty", "14:45", 1.20, 0.14, "volume");
    expect(ce.action).toBe("Buy CE");
    expect(ce.why).toMatch(/volume/i);
    const pe = describeFlow("Nifty", "11:30", 0.76, -0.08, "volume");
    expect(pe.action).toBe("Buy PE");
    expect(pe.why).toMatch(/call volume|calls traded/i);
    const board = buildIdea("Nifty", [
      { hhmm: "14:45", label: "14.45", minutes: 885, pcr: 1.20, delta: 0.14, band: "highly-positive" as const, live: false },
    ], "volume");
    expect(board.idea?.why).toMatch(/volume/i);
  });

  it("does not keep a 2:45 CE as live when the last OI print is 0.85", () => {
    const slot = (
      hhmm: string,
      pcr: number,
      delta: number,
      band: "highly-negative" | "negative" | "positive" | "highly-positive",
    ) => ({ hhmm, label: hhmm, minutes: 0, pcr, delta, band, live: false });
    const oi = [
      slot("14:45", 1.20, 0.14, "highly-positive"),
      slot("15:15", 0.85, -0.05, "negative"),
    ];
    const volume = [
      slot("14:45", 1.10, 0.08, "positive"),
      slot("15:15", 0.91, -0.04, "negative"),
    ];
    const doi = [
      slot("14:45", 1.20, 0.14, "highly-positive"),
      slot("15:15", 0.88, -0.10, "negative"),
    ];
    const read = readBook("Nifty", oi, volume, doi);
    expect(read.book?.hhmm).toBe("15:15");
    expect(read.book?.action).toBe("Wait");
    expect(read.book?.to).toBe(0.85);
    expect(read.volume?.hhmm).toBe("15:15");
    expect(read.volumeStance).toBe("quiet");
    expect(read.deltaOi?.hhmm).toBe("15:15");
    expect(liveAction(0.85)).toBe("Wait");
    expect(liveAction(1.2)).toBe("Buy CE");
    expect(liveAction(0.76)).toBe("Buy PE");
  });

  it("picks one Sensex idea and ignores false CE jumps", () => {
    const slots = [
      { hhmm: "10:30", label: "10.30", minutes: 630, pcr: 0.87, delta: 0.09, band: "negative" as const, live: false },
      { hhmm: "10:45", label: "10.45", minutes: 645, pcr: 0.97, delta: 0.09, band: "negative" as const, live: false },
      { hhmm: "11:15", label: "11.15", minutes: 675, pcr: 0.80, delta: -0.12, band: "highly-negative" as const, live: false },
      { hhmm: "12:45", label: "12.45", minutes: 765, pcr: 0.78, delta: 0.09, band: "highly-negative" as const, live: false },
      { hhmm: "15:15", label: "15.15", minutes: 915, pcr: 0.88, delta: -0.07, band: "negative" as const, live: false },
    ];
    const board = buildIdea("Sensex", slots);
    expect(board.idea?.action).toBe("Buy PE");
    expect(board.idea?.hhmm).toBe("15:15");
    expect(board.idea?.why).not.toMatch(/Buy PE/);
    expect(board.earlier.map((e) => e.hhmm)).toEqual(["11:15"]);
    expect(board.skipped).toBe(3);
    const fin = buildIdea("Fin", [
      { hhmm: "15:30", label: "15.30", minutes: 930, pcr: 0.63, delta: 0.07, band: "extreme-negative" as const, live: true },
    ]);
    expect(fin.idea?.action).toBe("Wait");
    expect(fin.earlier).toEqual([]);
  });

  it("has a mark for every cash slot on the snapshot", () => {
    for (const id of ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"] as const) {
      expect(PCR_SNAPSHOT[id].marks.length, id).toBe(26);
    }
    expect(hhmmToMinutes("09:15")).toBe(555);
  });

  it("buckets 1-minute prints onto the 15-minute clock", () => {
    const ticks = Array.from({ length: 22 }, (_, i) => {
      const minute = 10 + i;
      return {
        time: `2026-09-03T09:${String(minute).padStart(2, "0")}:00`,
        pcr: 0.7,
        volumePcr: 1,
        changeOiPcr: 0.3,
        indexClose: 23900 + minute,
        expiry: "2026-09-08",
      };
    });
    const marks = ticksToMarks(ticks);
    const by = Object.fromEntries(marks.map((m) => [m.hhmm, m]));
    expect(by["09:15"]?.indexClose).toBe(23915);
    expect(by["09:30"]?.indexClose).toBe(23930);
  });

  it("keeps tile insight and flow tape consistent at balanced PCR (e.g. Sensex 0.96)", () => {
    const slots = [
      { hhmm: "12:00", label: "12.00", minutes: 720, pcr: 0.88, delta: null, band: "negative" as const, live: false },
      { hhmm: "12:15", label: "12.15", minutes: 735, pcr: 0.92, delta: 0.04, band: "negative" as const, live: false },
      { hhmm: "12:30", label: "12.30", minutes: 750, pcr: 0.96, delta: 0.04, band: "empty" as const, live: true },
    ];
    const tapeLine = describeFlow("Sensex", "12:30", 0.96, 0.04, "oi");
    expect(tapeLine.action).toBe("Wait");
    expect(tapeLine.why).toBe("PCR went up, but calls are still more. Not a CE yet.");

    const insight = readPcr(slots, 0.35);
    expect(insight.action).toBe("Wait");
    expect(insight.headline).toBe("Put writing on the bounce");
    expect(insight.reason).toContain("calls are still more. Not a CE yet.");
    expect(insight.play).toContain("Wait for PCR ≥ 1.00 before buying CE.");

    expect(insight.action).toBe(tapeLine.action);
  });
});