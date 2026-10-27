import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const css = fs.readFileSync(new URL('../src/tokens.css', import.meta.url), 'utf8');
const blocks = [...css.matchAll(/:root\s*\{([^}]+)\}/g)].map(match => Object.fromEntries(
  [...match[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]),
));
const light = blocks[0], dark = { ...light, ...blocks[1] };
function luminance(hex) {
  assert.match(hex, /^#[\da-f]{6}$/i);
  return hex.slice(1).match(/../g).map(x => parseInt(x, 16) / 255)
    .map(x => x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
    .reduce((sum, x, i) => sum + x * [0.2126, 0.7152, 0.0722][i], 0);
}
function ratio(a, b) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }

for (const [name, theme] of Object.entries({ light, dark })) {
  test(`${name} theme keeps text and status labels readable on their surfaces`, () => {
    const pairs = [
      ['text', 'canvas'], ['text', 'surface'], ['text-muted', 'canvas'], ['text-muted', 'surface'],
      ['text-subtle', 'canvas'], ['text-subtle', 'surface'], ['text-muted', 'surface-soft'],
      ['primary', 'surface'], ['on-primary', 'primary'], ['primary', 'primary-soft'],
      ['normal-text', 'normal-surface'], ['normal-muted', 'normal-surface'],
      ['neutral-text', 'neutral-surface'], ['check-text', 'check-surface'], ['check-text', 'check-icon'],
      ['on-alert', 'alert'], ['on-alert', 'alert-hover'], ['on-alert', 'alert-icon'],
      ['alert-text', 'alert-soft'], ['chart-axis', 'surface'],
      ['on-alert-panel', 'alert-panel'], ['on-alert-panel', 'alert-panel-end'],
      ['on-alert-medallion', 'alert-medallion'], ['primary-strong', 'connection-surface'],
      ['on-alert', 'alert-end'], ['on-alert-medallion', 'alert-medallion-end'],
      ['text-subtle', 'shell'],
    ];
    for (const [foreground, background] of pairs) {
      assert.ok(ratio(theme[foreground], theme[background]) >= 4.5, `${foreground}/${background}: ${ratio(theme[foreground], theme[background]).toFixed(2)}`);
    }
  });
  test(`${name} theme keeps waveform, unavailable trace and keyboard focus distinguishable`, () => {
    for (const foreground of ['chart-wave', 'chart-alert', 'chart-idle', 'focus', 'border-strong']) {
      assert.ok(ratio(theme[foreground], theme.surface) >= 3, `${foreground} contrast`);
    }
    assert.notEqual(theme.alert, theme.primary);
    assert.notEqual(theme.alert, theme['check-surface']);
  });
}
