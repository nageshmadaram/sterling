/**
 * Every engine board wires Buy/Sell and the chart.
 *
 * The shared board's side of this is covered next door: `trade` and `chart` are in
 * `BOARD_COLUMNS`, the picker can switch either off independently, and a cell
 * whose engine supplied no handler renders empty rather than crashing.
 *
 * That last property is what makes this test necessary. An engine board that
 * forgets to pass `renderTrade` gets a Trade column that is simply blank — no
 * error, no failing assertion, and a header promising a button that is not there.
 * `useBoardRowActions` builds both from a `BoardSignal` alone, so there is never a
 * reason for a board to omit them; the only way it happens is by being forgotten,
 * which is exactly the case a source-level check catches and a render test on one
 * engine does not.
 */
import { describe, it, expect } from 'vitest';
import adaptiveEdge from '../AdaptiveEdgeBoard.tsx?raw';
import gammaMove from '../GammaMoveBoard.tsx?raw';
import superTrendShared from '../../SuperTrendSharedBoard.tsx?raw';

const BOARDS: Array<[string, string]> = [
  ['Adaptive Edge', adaptiveEdge],
  ['Gamma Move', gammaMove],
  ['SuperTrend (shared)', superTrendShared],
];

describe('row actions on every engine board', () => {
  it('passes renderTrade, so Buy and Sell reach the row', () => {
    for (const [name, src] of BOARDS) {
      expect(src, `${name} does not pass renderTrade`).toMatch(/renderTrade=\{/);
    }
  });

  it('passes renderChart, so the signal opens its own chart', () => {
    for (const [name, src] of BOARDS) {
      expect(src, `${name} does not pass renderChart`).toMatch(/renderChart=\{/);
    }
  });

  it('builds them from the shared hook rather than its own copy', () => {
    // A per-engine copy is how the two drifted before: SuperTrend's pane had its
    // own Buy handler and the others had none. One builder means one behaviour —
    // including the exit-intent and lot-size handling that a hand-rolled copy
    // would have to remember.
    for (const [name, src] of BOARDS) {
      expect(src, `${name} should use useBoardRowActions`).toMatch(/useBoardRowActions\(/);
    }
  });

  it('asks for the shared column list, so neither column can go missing', () => {
    // `columns={...}` — not `requested={...}`, which was a declared-but-dead prop
    // four of these boards passed until it was removed.
    for (const [name, src] of BOARDS) {
      expect(src, `${name} should pass columns=`).toMatch(/columns=\{/);
      expect(src, `${name} still passes the dead requested prop`).not.toMatch(/requested=\{/);
    }
  });
});
