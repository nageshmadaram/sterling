import { expect, test } from '@playwright/test';

async function requireBackend(request: any) {
  const health = await request.get('http://localhost:8000/health').catch(() => null);
  test.skip(!health || !health.ok(), 'backend is not running on localhost:8000');
  return health!.json();
}

test.describe('replay browser-to-backend acceptance', () => {
  test('browser reaches the replay contract through the frontend origin', async ({ page, request }) => {
    test.setTimeout(60_000);
    await requireBackend(request);
    await page.goto('/e2e/fixtures/backend-probe.html');

    const status = await page.evaluate(async () => {
      const res = await fetch('/api/v1/simulation/status', { headers: { 'X-User-Id': 'default' } });
      return { ok: res.ok, status: res.status, body: await res.json() };
    });

    expect(status.ok, JSON.stringify(status.body)).toBe(true);
    expect(status.body).toMatchObject({
      capabilities: expect.objectContaining({
        absolute_seek: true,
        delta_status: true,
        stream: true,
      }),
    });
    expect(status.body).toHaveProperty('run_id');
    expect(status.body).toHaveProperty('revision');
    expect(status.body).toHaveProperty('session_complete');
    expect(status.body).toHaveProperty('stats');
    expect(status.body.stats).toHaveProperty('events');
    expect(status.body.stats).toHaveProperty('trades');

    const dates = await page.evaluate(async () => {
      const res = await fetch('/api/v1/simulation/available-dates?instrument=NIFTY', {
        headers: { 'X-User-Id': 'default' },
      });
      return { ok: res.ok, status: res.status, body: await res.json() };
    });

    expect(dates.ok, JSON.stringify(dates.body)).toBe(true);
    expect(dates.body).toMatchObject({
      instrument: 'NIFTY',
      holidays_filtered: true,
      source: expect.stringMatching(/^(store|sample)$/),
    });
    expect(Array.isArray(dates.body.dates)).toBe(true);
  });

  test('browser reaches the read-only Kite order and protection diagnostics', async ({ page, request }) => {
    test.setTimeout(60_000);
    await requireBackend(request);
    await page.goto('/e2e/fixtures/backend-probe.html');

    const kiteStatus = await page.evaluate(async () => {
      const res = await fetch('/api/v1/kite/status', { headers: { 'X-User-Id': 'default' } });
      return { ok: res.ok, status: res.status, body: await res.json() };
    });

    expect(kiteStatus.ok, JSON.stringify(kiteStatus.body)).toBe(true);
    expect(kiteStatus.body).toHaveProperty('connected');
    expect(kiteStatus.body).toHaveProperty('is_paper');

    const diagnostics = await page.evaluate(async () => {
      const res = await fetch('/api/v1/kite/diagnostics/summary', { headers: { 'X-User-Id': 'default' } });
      return { ok: res.ok, status: res.status, body: await res.json() };
    });

    expect(diagnostics.ok, JSON.stringify(diagnostics.body)).toBe(true);
    expect(diagnostics.body).toHaveProperty('authenticated');
    expect(diagnostics.body).toHaveProperty('is_paper');
  });
});
