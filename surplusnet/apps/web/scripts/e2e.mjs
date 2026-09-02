// End-to-end smoke test against a running server (npm run start -w @surplusnet/api).
// Exercises all four surfaces through the real UI: recipient purchase, the
// courier accept → pickup → temp → dropoff flow (karma mint), supplier
// dashboard, and the ops zone view.
//
//   node scripts/e2e.mjs [baseUrl]
//
// Uses the environment's Playwright install; override with PLAYWRIGHT_MODULE.
const playwrightModule = process.env.PLAYWRIGHT_MODULE ?? 'playwright';
const { chromium } = await import(playwrightModule).catch(() =>
  import('/opt/node22/lib/node_modules/playwright/index.mjs'),
);

const baseUrl = process.argv[2] ?? 'http://localhost:4000';
let failures = 0;
const check = (name, ok) => {
  console.log(`${ok ? '✓' : '✗'} ${name}`);
  if (!ok) failures += 1;
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on('pageerror', (err) => {
  console.log('PAGE ERROR:', err.message);
  failures += 1;
});

await page.goto(baseUrl, { waitUntil: 'networkidle' });

// ── Recipient: feed renders, purchase completes ──
await page.waitForSelector('.itemcard', { timeout: 10_000 });
check('recipient: feed renders item cards', (await page.locator('.itemcard').count()) > 0);
const paidCard = page.locator('.itemcard', { has: page.locator('.badge.price') }).first();
await paidCard.click();
await page.getByRole('button', { name: /Claim now/ }).click();
await page.waitForSelector('.toast', { timeout: 5_000 });
check('recipient: purchase shows fund-contribution toast', (await page.locator('.toast').innerText()).includes('funded'));

// ── Courier: accept → pickup → temp → dropoff mints karma ──
await page.getByRole('button', { name: '🚲 Courier' }).click();
await page.waitForSelector('.tabs', { timeout: 5_000 });
const acceptBtn = page.getByRole('button', { name: /Accept rescue/ }).first();
await acceptBtn.waitFor({ timeout: 10_000 });
check('courier: offer shows surge quote', (await acceptBtn.innerText()).includes('KC'));
await acceptBtn.click();
await page.getByRole('button', { name: /Confirm pickup/ }).click({ timeout: 10_000 });
await page.getByRole('button', { name: /Log bin temperature/ }).click({ timeout: 10_000 });
await page.getByRole('button', { name: /Drop off/ }).click({ timeout: 10_000 });
await page.waitForSelector('.toast', { timeout: 5_000 });
check('courier: dropoff mints karma', (await page.locator('.toast').innerText()).includes('KC minted'));

// ── Supplier: dashboard shows the CFO three numbers ──
await page.getByRole('button', { name: '🏪 Supplier' }).click();
await page.waitForSelector('.stat', { timeout: 10_000 });
check('supplier: three CFO stats render', (await page.locator('.stat').count()) >= 3);

// ── Ops: zone health renders with a status pill ──
await page.getByRole('button', { name: '🗺 Ops' }).click();
await page.waitForSelector('.status-pill', { timeout: 10_000 });
check('ops: zone status pill renders', (await page.locator('.status-pill').count()) > 0);

await browser.close();
console.log(failures === 0 ? '\nAll e2e checks passed.' : `\n${failures} check(s) FAILED.`);
process.exit(failures === 0 ? 0 : 1);
