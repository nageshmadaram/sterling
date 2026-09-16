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

async function openFamilyTab(page: any) {
  await page.goto('/');
  await page.getByRole('button', { name: /^More$/i }).click().catch(() => {});
  await page.getByRole('button', { name: /^Family$/i }).click();
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
    await page.route('**/api/v1/snapback/family/operations', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_status: 'HEALTHY',
          mode: 'PAPER',
          evidence: 'COLLECTING',
          live_blocked_reason: null,
          allocated_capital: 1000000,
          cumulative_net_pnl: -1234.5,
          current_exposure_inr: 0,
          drawdown_pct: 1.2,
          open_positions_count: 0,
          exit_pending: 0,
          unresolved_errors: [],
          new_trades_halted: false,
          build_sha: 'abc123',
        }),
      }),
    );

    await openFamilyTab(page);

    await expect(page.getByTestId('system-status')).toContainText('HEALTHY');
    await expect(page.getByTestId('mode')).toContainText('PAPER');
    await expect(page.getByTestId('runtime-build')).toContainText('abc123');
  });

  test('resuming new trades needs a second confirmation', async ({ page }) => {
    let halted = true;
    const resumeCalls: string[] = [];

    await page.route('**/api/v1/snapback/family/operations', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_status: 'HEALTHY',
          mode: 'PAPER',
          evidence: 'COLLECTING',
          allocated_capital: 1000000,
          cumulative_net_pnl: 0,
          current_exposure_inr: 0,
          drawdown_pct: 0,
          open_positions_count: 0,
          exit_pending: 0,
          unresolved_errors: [],
          new_trades_halted: halted,
          build_sha: 'abc123',
        }),
      }),
    );
    await page.route('**/api/v1/snapback/family/resume-new-trades', (route) => {
      resumeCalls.push(route.request().url());
      halted = false;
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

    await page.route('**/api/v1/snapback/family/operations', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_status: 'HEALTHY',
          mode: 'PAPER',
          evidence: 'COLLECTING',
          allocated_capital: 1000000,
          cumulative_net_pnl: 0,
          current_exposure_inr: 0,
          drawdown_pct: 0,
          open_positions_count: 0,
          exit_pending: 0,
          unresolved_errors: [],
          new_trades_halted: false,
          build_sha: 'abc123',
        }),
      }),
    );
    await page.route('**/api/v1/snapback/family/stop-new-trades', (route) => {
      stopCalls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"halted":true}' });
    });

    await openFamilyTab(page);

    // Stopping is the safe direction, so it is deliberately one click.
    await page.getByTestId('stop-new-trades').click();
    await expect.poll(() => stopCalls.length).toBe(1);
  });
});
