/**
 * E2E: Valles "idea de moneda" chart bug reproduction
 *
 * Reproduces the blank-canvas bug in IdeaChart.tsx.
 * Logs a decisive EVIDENCE BLOCK so the root cause is provable.
 *
 * Auth strategy:
 *   The E2E backend is launched with:
 *     - AUTH_TEST_BYPASS_ALLOWED=1
 *     - AUTH_TEST_BYPASS_ROLE=admin
 *     - pytest injected into sys.modules
 *   This activates the triple-guarded middleware bypass so GET requests
 *   pass through without credentials — no login needed.
 *
 * localStorage key (ValleysFlow.tsx line 10):
 *   vw_sym — the selected symbol; preset via addInitScript so IdeaView
 *   loads immediately without going through PickScreen.
 */

import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SYMBOL = 'TRXUSDT';
const ARTIFACTS = path.join(__dirname, 'artifacts');

test('Valles IdeaChart renders candles — EVIDENCE BLOCK', async ({ page }) => {

  // ── Capture network / console / page errors (attach BEFORE navigation) ──
  const networkLogs: string[] = [];
  const consoleLogs: string[] = [];
  const pageErrors: string[] = [];

  let levelsStatus  = -1;
  let levelsUrl     = '';
  let levelsCandleCount = -1;
  let levelsHasCandles  = false;

  page.on('console', (msg) => {
    consoleLogs.push(`[${msg.type()}] ${msg.text()}`);
  });

  page.on('pageerror', (err) => {
    pageErrors.push(err.message);
  });

  page.on('response', async (res) => {
    const url = res.url();
    const status = res.status();
    networkLogs.push(`${status} ${res.request().method()} ${url}`);

    if (url.includes('/levels')) {
      levelsStatus = status;
      levelsUrl = url;
      try {
        const body = await res.json();
        const candles = body?.candles;
        levelsHasCandles = Array.isArray(candles) && candles.length > 0;
        levelsCandleCount = Array.isArray(candles) ? candles.length : -1;
      } catch {
        levelsCandleCount = -2;
      }
    }
  });

  // ── Step 1: Set localStorage BEFORE first navigation ────────────────────
  // addInitScript runs in the page context before any script — guaranteed to
  // fire before React mounts.
  await page.addInitScript((symbol: string) => {
    localStorage.setItem('vw_sym', symbol);
  }, SYMBOL);

  // ── Step 2: Navigate to the app ──────────────────────────────────────────
  await page.goto('http://localhost:5174');
  await page.waitForLoadState('networkidle');

  const landingUrl = page.url();
  console.log(`Landing URL: ${landingUrl}`);

  // Handle login page redirect — the bypass only works for GET requests
  // through the proxy. The frontend's useAuth hook does GET /api/auth/me
  // which returns 401, causing a redirect to /login. We need to spoof
  // the auth state. Try evaluating a fetch to /api/auth/me and see the status.
  const meStatus = await page.evaluate(async () => {
    const r = await fetch('/api/auth/me', { credentials: 'include' });
    return r.status;
  });
  console.log(`/api/auth/me status: ${meStatus}`);

  if (page.url().includes('/login') || meStatus === 401) {
    console.log('Auth redirect detected — attempting login via form or fetch');

    // Try the login form if it exists
    const emailInput = page.locator('input[type="email"]');
    const passwordInput = page.locator('input[type="password"]');
    const submitBtn = page.locator('button[type="submit"], button:has-text("Sign in"), button:has-text("Iniciar")');

    if (await emailInput.count() > 0 && await passwordInput.count() > 0) {
      // Fill with the bypass test admin that was created if it exists,
      // or try known admin email. If it fails, we'll note it.
      await emailInput.fill('admin@example.com');
      await passwordInput.fill('admin');
      if (await submitBtn.count() > 0) {
        await submitBtn.first().click();
      } else {
        await page.keyboard.press('Enter');
      }
      await page.waitForLoadState('networkidle');
      console.log(`After login attempt, URL: ${page.url()}`);
    }
  }

  // ── Step 3: Navigate to the Valles tab ────────────────────────────────────
  // LeftRail (desktop layout) renders navigation buttons by tab name.
  // From App.tsx: tab keys are 'mercado','posiciones','kill-switch','historial','autotune','valles'
  // LeftRail renders with aria-labels or text content matching the tab.
  const vallesSelectors = [
    'button[aria-label*="alles"]',
    'button[aria-label*="valles"]',
    '[data-tab="valles"]',
    'a[href*="valles"]',
    'nav button:has-text("V")',   // LeftRail shows single letter or label
    'text=Valles',
    '[class*="rail"] >> text=/[Vv]/i',
  ];

  let clickedValles = false;
  for (const sel of vallesSelectors) {
    const el = page.locator(sel).first();
    if (await el.isVisible().catch(() => false)) {
      await el.click();
      clickedValles = true;
      console.log(`Clicked Valles via selector: ${sel}`);
      break;
    }
  }

  if (!clickedValles) {
    console.log('No Valles tab button found via common selectors — checking page structure');
    // Dump all button text content for debugging
    const buttons = await page.locator('button, a').all();
    const btnTexts: string[] = [];
    for (const b of buttons.slice(0, 20)) {
      const txt = await b.textContent().catch(() => '');
      if (txt?.trim()) btnTexts.push(txt.trim().slice(0, 30));
    }
    console.log('Visible buttons/links:', btnTexts.join(' | '));
  }

  await page.waitForLoadState('networkidle');

  // ── Step 4: Wait for IdeaView to mount and render candles from /levels ──────
  // IdeaChart now consumes levels.candles from the prop (no separate /ohlcv fetch).
  // Wait for: (a) the chart div or (b) IdeaView + extra time.
  const chartContainerSel = '[class*="ju-chart"]';
  const ideaViewSel = '[class*="idea-view"], [class*="ideaView"]';
  const placeholderSel = '[aria-busy="true"]';

  // First wait for IdeaView to appear (max 10s)
  await page.waitForSelector(ideaViewSel, { timeout: 10000 }).catch(() => null);

  // Wait for the loading placeholder to disappear — means bundle.niveles resolved
  const placeholderGone = await page.waitForSelector(placeholderSel, { state: 'hidden', timeout: 10000 })
    .then(() => true)
    .catch(() => false);
  console.log(`Placeholder gone (niveles resolved): ${placeholderGone}`);

  // Now wait for the chart canvas div to appear (means IdeaChart mounted)
  const chartAppeared = await page.waitForSelector(chartContainerSel, { timeout: 10000 })
    .then(() => true)
    .catch(() => false);
  console.log(`Chart div appeared: ${chartAppeared}`);

  // Wait for levels fetch and candle rendering to complete
  await page.waitForTimeout(5000);

  // ── Step 5: Measure canvas and chart container ────────────────────────────
  const chartMetrics = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    const chartDiv = document.querySelector('[class*="ju-chart"]') as HTMLElement | null;
    const ideaView = document.querySelector('[class*="idea-view"]') as HTMLElement | null;

    return {
      canvasFound: !!canvas,
      canvasW: canvas ? Math.round(canvas.getBoundingClientRect().width) : 0,
      canvasH: canvas ? Math.round(canvas.getBoundingClientRect().height) : 0,
      chartDivFound: !!chartDiv,
      chartDivW: chartDiv ? Math.round(chartDiv.getBoundingClientRect().width) : 0,
      chartDivH: chartDiv ? Math.round(chartDiv.getBoundingClientRect().height) : 0,
      ideaViewFound: !!ideaView,
      // Count all canvases (LW can create >1)
      canvasCount: document.querySelectorAll('canvas').length,
    };
  });

  // ── Step 6: Capture screenshots ──────────────────────────────────────────
  const fullPagePath = path.join(ARTIFACTS, 'full-page.png');
  const chartPath = path.join(ARTIFACTS, 'chart-zoom.png');

  await page.screenshot({ path: fullPagePath, fullPage: true });

  const chartEl = page.locator('[class*="ju-chart"]').first();
  if (await chartEl.count() > 0) {
    await chartEl.screenshot({ path: chartPath }).catch(async () => {
      await page.screenshot({ path: chartPath });
    });
  } else {
    await page.screenshot({ path: chartPath });
  }

  // ── Pixel count del canvas ───────────────────────────────────────────────
  const pixelCount = await page.evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement | null;
    if (!canvas) return 0;
    const ctx = canvas.getContext('2d');
    if (!ctx) return 0;
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let nonTransparent = 0;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 10) nonTransparent++;
    }
    return nonTransparent;
  });

  // ── Screenshot nombrado ───────────────────────────────────────────────────
  const ideaFixedPath = path.join(ARTIFACTS, 'idea-trx-fixed.png');
  await page.screenshot({ path: ideaFixedPath, fullPage: false });

  // ── EVIDENCE BLOCK ────────────────────────────────────────────────────────
  console.log('\n========== EVIDENCE BLOCK ==========');
  console.log(`Final URL: ${page.url()}`);
  console.log(`\n--- /levels ---`);
  console.log(`levels URL:         ${levelsUrl || '(no request)'}`);
  console.log(`levels status:      ${levelsStatus}`);
  console.log(`levels hasCandles:  ${levelsHasCandles}`);
  console.log(`levels candleCount: ${levelsCandleCount}`);
  console.log(`Canvas non-transparent pixels: ${pixelCount}`);
  console.log(`\n--- canvas / chart ---`);
  console.log(`canvas count:    ${chartMetrics.canvasCount}`);
  console.log(`canvas found:    ${chartMetrics.canvasFound}`);
  console.log(`canvas dims:     ${chartMetrics.canvasW}x${chartMetrics.canvasH}px`);
  console.log(`chartDiv found:  ${chartMetrics.chartDivFound}`);
  console.log(`chartDiv dims:   ${chartMetrics.chartDivW}x${chartMetrics.chartDivH}px`);
  console.log(`ideaView found:  ${chartMetrics.ideaViewFound}`);
  console.log(`\n--- console messages ---`);
  consoleLogs.forEach((m) => console.log(`  ${m}`));
  console.log(`\n--- page errors ---`);
  if (pageErrors.length === 0) {
    console.log('  (none)');
  } else {
    pageErrors.forEach((e) => console.log(`  PAGEERROR: ${e}`));
  }
  console.log(`\n--- network (API calls only) ---`);
  networkLogs.filter((l) => l.includes('/api/')).forEach((l) => console.log(`  ${l}`));
  console.log(`\n--- screenshots ---`);
  console.log(`full-page:     ${fullPagePath}`);
  console.log(`chart:         ${chartPath}`);
  console.log(`idea-trx-fixed: ${ideaFixedPath}`);
  console.log('=====================================\n');

  // ── Step 7: Assertions ────────────────────────────────────────────────────
  expect(
    page.url(),
    'Should have loaded the app, not stayed on /login',
  ).not.toContain('/login');

  expect(
    levelsUrl,
    'IdeaChart debería haber disparado un /levels request',
  ).not.toBe('');

  expect(
    levelsHasCandles,
    `/levels debe retornar candles cuando TRXUSDT está disponible en Binance`,
  ).toBe(true);

  expect(
    pixelCount,
    'El canvas debe tener velas reales (>500 pixels no-transparentes)',
  ).toBeGreaterThan(500);
});
