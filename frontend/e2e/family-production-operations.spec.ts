import { expect, test } from '@playwright/test';

/**
 * The production operations dashboard, end to end.
 *
 * This is the screen a non-technical operator is expected to read instead of a
 * terminal, so the properties under test are the ones that would mislead a
 * person rather than a developer: an unreachable backend must never look
 * healthy, a broker position Sterling did not open must be unmistakable and
 * un-actionable, and every UNKNOWN must stay visible instead of resolving into
 * a comfortable zero.
 */

const PATH = '**/api/v1/operations/family';

const RELEASE_SHA = '03ee3d367a31390bcede7eb856527e026e16954c';

function payload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    runtime_sha: RELEASE_SHA,
    release_tag: 'sterling-family-runtime-1.0',
    environment: 'production',
    system_status: 'HEALTHY',
    production_mode: 'PRODUCTION_SHADOW',
    new_risk_allowed: true,
    new_risk_blockers: [],
    next_safe_action: 'Nothing. The system is collecting evidence.',
    release: {
      runtime_sha: RELEASE_SHA,
      release_tag: 'sterling-family-runtime-1.0',
      manifest_frozen: true,
      manifest_state: 'PASS',
      source_identity: 'PASS',
      remote_ci: 'PASS',
      ci_passed: 9,
      ci_required: 9,
      live_acceptance: 'PASS',
      reconnect: 'PASS',
      persistence: 'PASS',
      test_suites: 'PASS',
    },
    safety: {
      safety_state: 'NORMAL',
      execution_control_state: 'CLEAN',
      new_risk_allowed: true,
      family_stop_engaged: false,
      live_execution_enabled: false,
      blockers: [],
    },
    broker: {
      account_id: 'AA0595',
      binding_id: 'binding-1',
      connected: true,
      is_paper: false,
      broker_flatness: 'PASS',
      broker_state_readable: true,
      broker_state_detail: '',
      broker_observed_at: new Date().toISOString(),
      managed_positions: [],
      external_positions: [],
      unresolved_intents: 0,
      open_exposure: 'PASS',
    },
    deployment: {
      static_egress: 'PASS',
      expected_egress_ip: '203.0.113.7',
      observed_egress_ip: '203.0.113.7',
      deployment_identity: 'PASS',
      lake_mount: 'PASS',
      network_path: 'PASS',
      market_freshness: 'PASS',
      last_tick_at: new Date().toISOString(),
      backend_reachable: true,
    },
    certification: {
      gates: {
        source_identity: { status: 'PASS', detail: '' },
        remote_ci: { status: 'PASS', detail: 'all nine required contexts succeeded' },
      },
      release_ready: true,
    },
    drills: { required: 12, passed: 12, failed: 0, unknown: 0, drills: [] },
    security: {
      environment: 'production',
      production_security: 'PASS',
      stored_secret_count: 4,
      dev_fallback_in_use: false,
      last_migration_at: null,
    },
    evidence: {
      authoritative_start: 'PASS',
      sessions: 0,
      trades: 0,
      unresolved_exposure: 0,
      identity_drift: 'PASS',
      release_tag: 'sterling-family-runtime-1.0',
      runtime_sha: RELEASE_SHA,
      regimes: [
        { regime: 'PAPER', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
        { regime: 'SHADOW', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
        { regime: 'BROKER', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
      ],
    },
    lanes: [
      'snapback:ultra_scalping',
      'snapback:scalping',
      'snapback:intraday',
      'snapback:overnight',
      'snapback:swing',
      'supertrend:ultra_scalping',
      'supertrend:scalping',
      'supertrend:intraday',
      'supertrend:overnight',
      'supertrend:swing',
    ].map((lane_key) => ({
      lane_key,
      family: lane_key.split(':')[0],
      mode: lane_key.split(':')[1],
      lifecycle_state: 'RESEARCH',
      identity_verdict: 'UNKNOWN',
      shadow_verdict: 'UNKNOWN',
      economic_verdict: 'UNKNOWN',
      live_minimum_eligible: false,
      blockers: ['LANE_NOT_LIVE_STATE'],
      execution_vehicle: 'OPTIONS_LONG',
    })),
    operator: { stop_available: true, resume_available: false, stop_engaged: false },
    ...overrides,
  };
}

function serve(page: any, body: Record<string, unknown>) {
  return page.route(PATH, (route: any) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    }),
  );
}

async function openFamilyTab(page: any) {
  await page.goto('/');

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

test.describe('Family production operations', () => {
  test('an unreachable backend never shows a healthy screen', async ({ page }) => {
    await page.route(PATH, (route: any) =>
      route.fulfill({ status: 503, body: '{"detail":"unavailable"}' }),
    );
    await openFamilyTab(page);

    await expect(page.getByTestId('family-unreachable')).toBeVisible();
    await expect(page.getByLabel('production mode')).toContainText('UNREACHABLE');
    await expect(page.getByText('REAL ENTRY ORDERS DISABLED')).toHaveCount(0);
  });

  test('production shadow is stated, with entry orders explicitly disabled', async ({ page }) => {
    await serve(page, payload());
    await openFamilyTab(page);

    await expect(page.getByLabel('production mode')).toContainText(
      'PRODUCTION SHADOW — REAL ENTRY ORDERS DISABLED',
    );
    await expect(page.getByText(RELEASE_SHA.slice(0, 12))).toBeVisible();
  });

  test('an external position is visible, unmanaged, and has no action control', async ({ page }) => {
    await serve(
      page,
      payload({
        new_risk_allowed: false,
        safety: {
          safety_state: 'NORMAL',
          execution_control_state: 'CLEAN',
          new_risk_allowed: false,
          family_stop_engaged: false,
          live_execution_enabled: false,
          blockers: [
            {
              code: 'external_broker_exposure',
              severity: 'BLOCK',
              message: 'the broker holds 1 position Sterling did not open',
              source: 'safety_supervisor',
            },
          ],
        },
        broker: {
          ...(payload().broker as Record<string, unknown>),
          broker_flatness: 'FAIL',
          open_exposure: 'FAIL',
          external_positions: [
            {
              source: 'BROKER_EXTERNAL',
              managed_by_sterling: false,
              account: 'AA0595',
              instrument: 'CDSL26SEP1500CE',
              exchange: 'NFO',
              product: 'NRML',
              quantity: 16625,
              broker_avg_price: 3.271429,
              last_price: 3.35,
              unrealised_pnl: 1306.24,
              discovery_reason: 'RECONCILIATION_UNKNOWN_POSITION',
              protection_known: false,
              sterling_intent: null,
              sterling_fill: null,
              observed_at: new Date().toISOString(),
            },
          ],
        },
      }),
    );
    await openFamilyTab(page);

    const unmanaged = page.getByLabel('Positions not managed by Sterling');
    await expect(unmanaged).toBeVisible();
    await expect(unmanaged).toContainText('CDSL26SEP1500CE');
    await expect(unmanaged).toContainText('Sterling will not manage this position');
    // No Protect/Exit affordance: Sterling does not manage it.
    await expect(unmanaged.getByRole('button')).toHaveCount(0);

    await expect(page.getByText('NEW RISK BLOCKED')).toBeVisible();
    await expect(page.getByText('external_broker_exposure')).toBeVisible();
  });

  test('eight of nine CI contexts is not a release-ready state', async ({ page }) => {
    const base = payload();
    await serve(page, {
      ...base,
      release: { ...(base.release as Record<string, unknown>), remote_ci: 'UNKNOWN', ci_passed: 8 },
      certification: { gates: {}, release_ready: false },
    });
    await openFamilyTab(page);

    await expect(page.getByText('8/9')).toBeVisible();
    await expect(page.getByLabel('release ready: FAIL')).toBeVisible();
  });

  test('an acceptance from another SHA reads UNKNOWN, not PASS', async ({ page }) => {
    const base = payload();
    await serve(page, {
      ...base,
      release: { ...(base.release as Record<string, unknown>), live_acceptance: 'UNKNOWN' },
    });
    await openFamilyTab(page);

    await expect(page.getByText('no acceptance for this SHA')).toBeVisible();
  });

  test('no drill records block certification rather than reading as passed', async ({ page }) => {
    await serve(
      page,
      payload({ drills: { required: 12, passed: 0, failed: 0, unknown: 12, drills: [] } }),
    );
    await openFamilyTab(page);

    await expect(page.getByLabel('failure drills: UNKNOWN')).toBeVisible();
    await expect(page.getByText(/not a drill that passed/)).toBeVisible();
  });

  test('the development fallback key is shown as a blocker', async ({ page }) => {
    await serve(
      page,
      payload({
        security: {
          environment: 'development',
          production_security: 'UNKNOWN',
          stored_secret_count: 4,
          dev_fallback_in_use: true,
          last_migration_at: null,
        },
      }),
    );
    await openFamilyTab(page);

    await expect(page.getByText('DEV FALLBACK KEY IN USE — release blocker')).toBeVisible();
  });

  test('all ten lanes appear with three independent verdicts', async ({ page }) => {
    await serve(page, payload());
    await openFamilyTab(page);

    const lanes = page.getByLabel('Lanes (10)');
    await expect(lanes).toBeVisible();
    for (const lane of ['snapback:swing', 'supertrend:swing', 'snapback:ultra_scalping']) {
      await expect(lanes.getByRole('button', { name: lane })).toBeVisible();
    }
    // Economics unknown must read UNKNOWN, never a zero score.
    await expect(lanes.getByLabel('snapback:swing economics: UNKNOWN')).toBeVisible();
  });

  test('stop all new trades reaches the backend', async ({ page }) => {
    const stopCalls: string[] = [];
    await page.route('**/api/v1/snapback/family/stop-new-trades*', (route: any) => {
      stopCalls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"halted":true}' });
    });
    await serve(page, payload());
    await openFamilyTab(page);

    await page.getByRole('button', { name: /STOP ALL NEW TRADES/ }).click();
    await expect.poll(() => stopCalls.length).toBeGreaterThan(0);
  });

  test('a refused resume shows the refusal', async ({ page }) => {
    await page.route('**/api/v1/snapback/family/resume-new-trades*', (route: any) =>
      route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"RECOVERY_REQUIRED"}' }),
    );
    await serve(
      page,
      payload({ operator: { stop_available: true, resume_available: true, stop_engaged: true } }),
    );
    await openFamilyTab(page);

    await page.getByRole('button', { name: /Resume new trades/ }).click();
    await page.getByRole('button', { name: /Confirm resume/ }).click();
    await expect(page.getByRole('alert').filter({ hasText: /RECOVERY_REQUIRED|refus|failed/i }).first())
      .toBeVisible({ timeout: 10_000 });
  });
});
