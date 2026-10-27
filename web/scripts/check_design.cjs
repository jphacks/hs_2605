// Optional browser verification for #70. Playwright is tooling, not an app dependency.
// All server endpoints are intercepted; no radar, Python server or phone alerts are used.
const { chromium } = require(process.env.IBUKI_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const before = process.argv.includes('--before');
const polish = process.argv.includes('--polish');
const polishStage = process.argv.find(argument => argument.startsWith('--stage='))?.slice(8) || 'final';
if (!/^[a-z0-9-]+$/.test(polishStage)) throw Error('Invalid screenshot stage');
const quick = before || process.argv.includes('--quick') || polish;
const publish = process.argv.includes('--publish');
const phase = before ? 'before' : 'after';
const root = path.resolve(__dirname, '../..');
const baseUrl = process.env.IBUKI_DESIGN_BASE_URL || 'http://127.0.0.1:5178';
const output = polish ? path.join(root, '.venv/ibuki-browser-qa/polish-70', polishStage) : path.join(root, '.venv/ibuki-browser-qa/design-70', phase);
const published = path.join(root, 'web/docs/design/issue-70', ...(polish ? ['polish', polishStage] : []));
fs.mkdirSync(output, { recursive: true });
if (publish) fs.mkdirSync(published, { recursive: true });

const selected = new Set(['1440-light-STILL', '1440-dark-STILL', '390-light-STILL', '390-dark-ALERT']);
const titles = {
  STILL: '眠っています', OK: '起きています', CHECK: '呼吸を確認中',
  ALERT: '呼吸が確認できません', EMPTY: 'ベッドにいません',
};
const report = { phase, scenarios: [], errors: [], screenshots: [] };

async function until(predicate, label) {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw Error('Timed out: ' + label);
}

async function fixture(browser, width, scheme, state) {
  const height = width === 1440 ? 900 : width === 320 ? 700 : 844;
  const page = await browser.newPage({
    viewport: { width, height }, colorScheme: scheme, reducedMotion: 'reduce', locale: 'ja-JP',
  });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  let siren = state === 'ALERT', posts = 0, t = 200000;
  const sockets = [];
  await page.route('**/api/health', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ source: 'FileReplay', brain: 'brain', board_connected: state !== 'sensor-disconnected', siren }),
  }));
  await page.route('**/api/button', route => {
    assert.equal(route.request().method(), 'POST');
    posts++;
    siren = false;
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.routeWebSocket(url => url.pathname === '/ws', route => {
    const socket = { route, closed: false };
    sockets.push(socket);
    route.onClose(() => { socket.closed = true; });
    route.onMessage(() => { errors.push('Unexpected outgoing WebSocket command'); });
  });
  await page.goto(baseUrl + '/', { waitUntil: 'networkidle' });
  const active = () => sockets.filter(socket => !socket.closed).at(-1);
  await until(() => !!active(), 'LIVE connection');
  const send = value => active()?.route.send(JSON.stringify(value));
  const brainState = titles[state] ? state : 'STILL';
  const available = !['EMPTY', 'ALERT', 'unavailable', 'shaking'].includes(state);
  function status(count = 4) {
    // Synthetic 32/min waveform at a stated 60 ms interval, never real sensor data.
    const disp_mm = available ? Array.from({ length: count }, (_, i) =>
      Number((0.9 * Math.sin((t - (count - 1 - i) * 60) / 1000 * 2 * Math.PI * 32 / 60)).toFixed(4))) : [];
    return {
      t, disp_mm, dt_ms: 60, bpm: brainState === 'STILL' && available ? 32 : null,
      motion: brainState === 'OK' ? 120 : 8, present: brainState !== 'EMPTY',
      state: brainState, source: 'brain', shaking: state === 'shaking',
    };
  }
  send({ type: 'board', connected: true });
  // Allow the board-generation reset to commit before sending the cached batch.
  await page.waitForTimeout(80);
  send(status(500));
  await page.waitForTimeout(40);
  t += 240;
  send(status());
  await page.waitForTimeout(80);
  if (state === 'ALERT') send({ type: 'alert', reason: 'no_breath', since_s: 10, t });
  else {
    send({ type: 'info', reason: 'woke_up', since_s: 0, t });
    send({ type: 'info', reason: 'fast_breathing', since_s: 300, bpm: 54, t });
  }
  let timer;
  if (state === 'disconnected') {
    active().closed = true;
    await sockets.at(-1).route.close({ code: 1012, reason: 'Visual fixture disconnect' });
    await until(async () => await page.locator('.connection-status').getAttribute('data-status') === 'disconnected', 'disconnected view');
  } else if (state === 'stale') {
    await page.waitForTimeout(3100);
  } else {
    if (state === 'sensor-disconnected') send({ type: 'board', connected: false });
    timer = setInterval(() => { if (active()) { t += 240; send(status()); } }, 400);
  }
  await page.waitForTimeout(80);
  return {
    page, errors, sockets, getPosts: () => posts, pause: () => clearInterval(timer),
    advance: () => { t += 240; send(status()); return t; },
    stop: async () => { clearInterval(timer); await page.close(); },
  };
}

function luminance(rgb) {
  return rgb.map(channel => {
    channel /= 255;
    return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
  }).reduce((sum, channel, i) => sum + channel * [.2126, .7152, .0722][i], 0);
}
function contrast(foreground, background) {
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}

async function verifyScene(fixture, width, state) {
  const { page } = fixture;
  if (titles[state]) assert.equal(await page.locator('.status-banner h2').innerText(), titles[state]);
  if (state === 'ALERT') assert.equal(await page.locator('.siren-stop').isVisible(), true);
  if (['disconnected', 'unavailable', 'EMPTY', 'shaking', 'stale', 'sensor-disconnected'].includes(state)) {
    assert.match(await page.locator('.bpm-value').innerText(), /—/);
  }
  const metrics = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth, viewport: innerWidth,
    background: getComputedStyle(document.body).backgroundColor,
    overflow: [...document.querySelectorAll('header,.connection-status,.status-banner,.panel,.siren-stop')]
      .filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.left < -1 || rect.right > innerWidth + 1 || element.scrollWidth > element.clientWidth + 2;
      }).map(element => element.className),
  }));
  if (before) return metrics;
  assert.equal(metrics.width, width);
  assert.deepEqual(metrics.overflow, []);
  const samples = await page.evaluate(() => {
    const selectors = [
      '.brand', '.brand small', '.connection-copy strong', '.connection-copy p>span',
      '.connection-provenance', '.connection-meta', '.connection-details summary',
      '.status-banner h2', '.status-banner p', '.chart-description', '.chart-footer',
      '.bpm .muted', '.bpm-value', '.presence', '.metric-tag', '.metrics-note', '.notification-heading span',
      '.notice time', '.notice-level', '.notice strong', '.notice small',
      '.current-warning', '.siren-stop', '.chart-empty span', 'footer p',
    ];
    const rgb = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const background = element => {
      for (let node = element; node; node = node.parentElement) {
        const color = getComputedStyle(node).backgroundColor;
        if (color !== 'rgba(0, 0, 0, 0)' && color !== 'transparent') return rgb(color);
      }
      return [255, 255, 255];
    };
    return selectors.flatMap(selector => [...document.querySelectorAll(selector)]
      .filter(element => element.getClientRects().length && !element.matches(':disabled'))
      .map(element => ({ selector, fg: rgb(getComputedStyle(element).color), bg: background(element) })));
  });
  const contrasts = samples.map(sample => ({ ...sample, ratio: contrast(sample.fg, sample.bg) }));
  assert.deepEqual(contrasts.filter(sample => sample.ratio < 4.5), [], 'text contrast');
  metrics.minTextContrast = Math.min(...contrasts.map(sample => sample.ratio));
  // Navigation uses existing page sections; no speculative API fields or device features.
  assert.equal(await page.locator('.menu-toggle').count(), 1);
  assert.equal(await page.locator('.menu-toggle').isVisible(), width <= 800);
  assert.equal(await page.getByText('ペアリング', { exact: true }).count(), 0);
  const order = await page.locator('main').evaluate(element => [...element.children]
    .filter(child => child.matches('header,.status-overview,.dashboard-grid,.notification-panel'))
    .map(child => child.tagName === 'HEADER' ? 'header' : child.classList[0]));
  assert.deepEqual(order, ['header', 'status-overview', 'dashboard-grid', 'notification-panel']);
  const chartBox = await page.locator('.waveform').boundingBox();
  const metricBox = await page.locator('.metrics').boundingBox();
  if (width <= 800) {
    assert.ok(metricBox.y + metricBox.height <= chartBox.y, 'mobile metrics precede waveform');
    const cards = await page.locator('.metric-card').all();
    const positions = await Promise.all(cards.map(card => card.boundingBox()));
    assert.equal(positions.length, 3);
    assert.ok(positions.every(box => Math.abs(box.y - positions[0].y) < 1), 'three mobile metrics share a row');
  } else assert.ok(chartBox.x + chartBox.width <= metricBox.x, 'desktop metrics sit beside waveform');
  if (['ALERT', 'EMPTY', 'unavailable', 'shaking', 'disconnected', 'stale', 'sensor-disconnected'].includes(state)) {
    assert.equal(await page.locator('.recharts-area-curve').count(), 0, 'unavailable data never fabricates a wave');
  }
  if (state === 'ALERT') {
    const summary = page.locator('.connection-details summary');
    await summary.focus();
    const focus = await summary.evaluate(element => {
      const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
      let background = element;
      while (background && getComputedStyle(background).backgroundColor === 'rgba(0, 0, 0, 0)') background = background.parentElement;
      return { foreground: rgb(getComputedStyle(element).outlineColor), background: rgb(getComputedStyle(background).backgroundColor) };
    });
    assert.ok(contrast(focus.foreground, focus.background) >= 3, 'connection focus remains visible outside mobile alert banner');
    await summary.evaluate(element => element.blur());
  }
  return metrics;
}

async function verifyInteractions(browser) {
  const alarm = await fixture(browser, 390, 'dark', 'ALERT');
  try {
    const summary = alarm.page.locator('.connection-details summary');
    await summary.focus();
    await alarm.page.keyboard.press('Enter');
    assert.equal(await alarm.page.locator('.connection-details').getAttribute('open'), '');
    assert.equal(await summary.evaluate(element => getComputedStyle(element).outlineStyle), 'solid');
    await alarm.page.keyboard.press('Space');
    assert.equal(await alarm.page.locator('.connection-details').getAttribute('open'), null);
    const menu = alarm.page.getByRole('button', { name: 'メニュー', exact: true });
    await menu.click();
    assert.equal(await menu.getAttribute('aria-expanded'), 'true');
    await alarm.page.getByRole('link', { name: '通知履歴', exact: true }).focus();
    await alarm.page.keyboard.press('Escape');
    assert.equal(await menu.getAttribute('aria-expanded'), 'false');
    assert.equal(await menu.evaluate(element => element === document.activeElement), true);
    await menu.click();
    await alarm.page.getByRole('button', { name: '接続詳細', exact: true }).click();
    assert.equal(await alarm.page.locator('.connection-details').getAttribute('open'), '');
    assert.equal(await menu.getAttribute('aria-expanded'), 'false');
    await summary.press('Space');
    const help = alarm.page.getByLabel('波形の説明', { exact: true });
    await help.focus();
    await alarm.page.keyboard.press('Enter');
    assert.equal(await alarm.page.locator('.chart-help').getAttribute('open'), '');
    await alarm.page.keyboard.press('Enter');
    await alarm.page.locator('.siren-stop').click();
    await until(async () => await alarm.page.locator('.siren-stop').count() === 0, 'acknowledged siren');
    assert.equal(alarm.getPosts(), 1);
    assert.deepEqual(alarm.errors, []);
    report.interactions = 'PASS';
  } finally { await alarm.stop(); }

  const motion = await fixture(browser, 390, 'light', 'STILL');
  try {
    motion.pause();
    await motion.page.waitForTimeout(80);
    const originalSockets = motion.sockets.length;
    const start = await motion.page.locator('.chart').getAttribute('data-end');
    await motion.page.waitForTimeout(120);
    assert.equal(await motion.page.locator('.chart').getAttribute('data-end'), start);
    await motion.page.emulateMedia({ reducedMotion: 'no-preference' });
    await motion.page.waitForTimeout(30);
    const latest = motion.advance();
    await motion.page.waitForTimeout(40);
    const first = Number(await motion.page.locator('.chart').getAttribute('data-end'));
    await motion.page.waitForTimeout(90);
    const next = Number(await motion.page.locator('.chart').getAttribute('data-end'));
    assert.ok(next > first && next <= latest / 1000, 'normal preference preserves bounded glide');
    await motion.page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await motion.page.waitForTimeout(80);
    assert.equal(Number(await motion.page.locator('.chart').getAttribute('data-end')), latest / 1000);
    assert.equal(motion.sockets.length, originalSockets, 'display preferences do not reconnect');
    assert.match(await motion.page.locator('.bpm-value').innerText(), /32.0/);
    assert.deepEqual(motion.errors, []);
    report.motionAndThemeChanges = 'PASS';
  } finally { await motion.stop(); }
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.IBUKI_BROWSER_PATH ? { executablePath: process.env.IBUKI_BROWSER_PATH } : {}),
  });
  try {
    report.browser = browser.version();
    const matrix = polish ? [[1440, 'light', 'STILL'], [390, 'light', 'STILL'], [390, 'dark', 'ALERT']] : quick
      ? [[1440, 'light', 'STILL'], [1440, 'dark', 'STILL'], [390, 'light', 'STILL'], [390, 'dark', 'ALERT']]
      : [1440, 390].flatMap(width => ['light', 'dark'].flatMap(scheme =>
        ['STILL', 'OK', 'CHECK', 'ALERT', 'EMPTY', 'disconnected', 'unavailable'].map(state => [width, scheme, state])))
        .concat([[320, 'light', 'ALERT'], [320, 'dark', 'STILL'], [390, 'dark', 'shaking'],
          [390, 'dark', 'stale'], [390, 'light', 'sensor-disconnected']]);
    for (const [width, scheme, state] of matrix) {
      const scene = await fixture(browser, width, scheme, state);
      const name = `${width}-${scheme}-${state}`;
      try {
        const metrics = await verifyScene(scene, width, state);
        const file = path.join(output, name + '.png');
        await scene.page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
        report.screenshots.push(file);
        if (publish && selected.has(name)) {
          // Keep full-page evidence above, and publish the requested exact viewport size.
          await scene.page.screenshot({
            path: path.join(published, `${polish ? '' : phase + '-'}${name.toLowerCase()}.png`),
            fullPage: false, animations: 'disabled',
          });
          if ((!polish || polishStage === 'final') && (name === '1440-light-STILL' || name === '390-dark-ALERT')) {
            fs.copyFileSync(file, path.join(published, `${polish ? '' : phase + '-'}${name.toLowerCase()}-full.png`));
          }
        }
        assert.deepEqual(scene.errors, []);
        report.scenarios.push({ name, result: 'PASS', metrics });
        console.log('PASS ' + phase + ' ' + name);
      } catch (error) {
        report.scenarios.push({ name, result: 'FAIL', error: String(error) });
        await scene.page.screenshot({ path: path.join(output, 'FAIL-' + name + '.png'), fullPage: true });
        console.error('FAIL ' + name + ': ' + error);
      } finally {
        report.errors.push(...scene.errors);
        await scene.stop();
      }
    }
    if (!quick) await verifyInteractions(browser);
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  const failures = report.scenarios.filter(scene => scene.result === 'FAIL');
  console.log(JSON.stringify({ phase, scenarios: report.scenarios.length, failures, errors: report.errors }));
  if (failures.length || report.errors.length) process.exitCode = 1;
})().catch(error => {
  report.fatal = String(error.stack || error);
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.error(error);
  process.exitCode = 1;
});
