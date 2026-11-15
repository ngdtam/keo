import fs from 'node:fs';
import { expect, test } from '@playwright/test';

function readToken(): string {
  if (process.env.KEO_TOKEN) return process.env.KEO_TOKEN;
  const envText = fs.readFileSync('.env', 'utf8');
  const match = envText.match(/^KEO_TOKEN=(.+)$/m);
  if (!match) throw new Error('KEO_TOKEN not found in environment or .env');
  return match[1].trim();
}

test('logs in and renders every deployed route without errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console.error: ${msg.text()}`);
  });

  await page.goto('/');
  await expect(page).toHaveURL(/\/login$/);
  await page.locator('input[name="token"]').fill(readToken());
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/$/);

  const routes = [
    '/',
    '/library',
    '/library/trash',
    '/library/organize/preview',
    '/search',
    '/charts',
    '/moods',
    '/review',
    '/activity',
    '/settings',
  ];

  for (const route of routes) {
    const res = await page.goto(route);
    expect(res?.status(), `HTTP status for ${route}`).toBeLessThan(400);
    await expect(page.locator('body')).not.toBeEmpty();
  }

  expect(errors).toEqual([]);
});
