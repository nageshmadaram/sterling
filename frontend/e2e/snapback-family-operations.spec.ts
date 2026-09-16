import { expect, test } from '@playwright/test';

/**
 * The Family Operations screen is the only surface a non-technical operator is
 * expected to read and act on. Two things must hold end to end:
 *
 *   - when the backend cannot answer, the screen says so rather than rendering
 *     stale or zeroed numbers, which would read as "nothing is wrong";
 *   - stopping new trades takes effect immediately, and resuming them requires a
 *     second, explicit confirmation.
 */


/**
 * The full server payload shape. The panel renders many of these fields, so a
 * partial fixture makes the component throw during render and the test then
 * reports "the Family screen never appeared" — a symptom two steps removed from
 * the cause.
 */
const BASE_OPERATIONS = {
  system_status: 'HEALTHY',
  mode: 'PAPER',
  strategy: 'Snapback 1.0.5',
  runtime_sha: 'abc123',
  build_sha: 'abc123',
  strategy_manifest: '602d28f8',
  evidence: 'INCONCLUSIVE',
  evidence_missing_requirements: ['no promotion evaluation has been recorded yet'],
  broker_connected: true,
  market_data_fresh: false,
  runner_alive: true,
  backup_ok: null,
  alert_transport_configured: true,
  last_report: null,
  allocated_capital: 1000000,
  cumulative_net_pnl: null,
  mean_net_pnl_per_trade: null,
  current_exposure_inr: null,
  open_positions_count: 0,
  observed_sessions: 0,
  completed_trades: 0,
  exit_pending: 0,
  drawdown_pct: null,
  new_trades_halted: false,
  live_blocked: true,
  reconciliation_clean: true,
  reconciliation_mismatches: [],
  unresolved_errors: [],
  generated_at: '2026-09-17T09:00:00+00:00',
};

function operationsRoute(page: any, overrides: Record<string, unknown> = {}) {
  return page.route('**/api/v1/snapback/family/operations', (route: any) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...BASE_OPERATIONS, ...overrides }),
    }),
  );
}

async function openFamilyTab(page: any) {
  await page.goto('/');

  // Each step asserts before it acts. Swallowing a failure here (the original
  // `.click().catch(() => {})`) made a missing More button report itself as a
  // missing Family tab — which is exactly the defect it was hiding.
  // Addressed by test id, not by accessible name: several unrelated controls in
  // this workspace are also called "More", and an ambiguous locator fails as a
  // strict-mode violation rather than as the thing the test is about.
  const more = page.getByTestId('rail-more');
  await expect(more).toBeVisible({ timeout: 30_000 });
  await more.click();

  const family = page.getByTestId('more-tab-family');
  await expect(family).toBeVisible({ timeout: 15_000 });
  await family.click();

  await expect(
    page.getByTestId('family-operations').or(page.getByTestId('family-unreachable')),
  ).toBeVisible({ timeout: 15_000 });
}

test.describe('Snapback family operations', () => {
  test('an unreachable backend is shown as unreachable, not as healthy zeros', async ({ page }) => {
    await page.route('**/api/v1/snapback/family/operations', (route) =>
      route.fulfill({ status: 503, body: '{"detail":"unavailable"}' }),
    );

    await openFamilyTab(page);

    await expect(page.getByTestId('family-unreachable')).toBeVisible();
    await expect(page.getByTestId('cumulative-pnl')).toHaveCount(0);
  });

  test('the panel renders the operator summary', async ({ page }) => {
    await operationsRoute(page);

    await openFamilyTab(page);

    await expect(page.getByTestId('system-status')).toContainText('HEALTHY');
    await expect(page.getByTestId('mode')).toContainText('PAPER');
    await expect(page.getByTestId('runtime-build')).toContainText('abc123');
  });

  test('resuming new trades needs a second confirmation', async ({ page }) => {
    const resumeCalls: string[] = [];

    await operationsRoute(page, { new_trades_halted: true });
    await page.route('**/api/v1/snapback/family/resume-new-trades*', (route) => {
      resumeCalls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"halted":false}' });
    });

    await openFamilyTab(page);

    await expect(page.getByTestId('halted-notice')).toBeVisible();

    // First click only arms the confirmation; it must not resume trading.
    await page.getByTestId('resume').click();
    expect(resumeCalls).toHaveLength(0);

    await page.getByTestId('confirm-resume').click();
    await expect.poll(() => resumeCalls.length).toBe(1);
  });

  test('stopping new trades takes effect on the first click', async ({ page }) => {
    const stopCalls: string[] = [];

    await operationsRoute(page);
    await page.route('**/api/v1/snapback/family/stop-new-trades*', (route) => {
      stopCalls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"halted":true}' });
    });

    await openFamilyTab(page);

    // Stopping is the safe direction, so it is deliberately one click.
    await page.getByTestId('stop-new-trades').click();
    await expect.poll(() => stopCalls.length).toBe(1);
  });
});
