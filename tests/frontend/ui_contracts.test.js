const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

const root = resolve(__dirname, '../..');
const read = (relativePath) => readFileSync(resolve(root, relativePath), 'utf8');

test('shared responsive rules assign horizontal overflow only to table regions', () => {
  const foundation = read('static/app/css/foundation.css');
  const operations = read('static/app/css/operations.css');
  const combined = `${foundation}\n${operations}`;

  assert.match(combined, /\.table-responsive\s*\{[^}]*overflow-x:\s*auto/s);
  assert.doesNotMatch(combined, /(?:body|\.app-main)\s*\{[^}]*overflow-x:\s*auto/s);
});

test('shared action and pagination groups wrap instead of overflowing', () => {
  const foundation = read('static/app/css/foundation.css');
  const operations = read('static/app/css/operations.css');
  const combined = `${foundation}\n${operations}`;

  assert.match(combined, /\.table-filter-actions\s*\{[^}]*flex-wrap:\s*wrap/s);
  assert.match(combined, /\.page-heading__actions[^\{]*\{[^}]*flex-wrap:\s*wrap/s);
  assert.match(combined, /\.page-pagination \.pagination\s*\{[^}]*flex-wrap:\s*wrap/s);
});

test('mobile and reduced-motion contracts use shared breakpoints and touch token', () => {
  const tokens = read('static/app/css/tokens.css');
  const foundation = read('static/app/css/foundation.css');
  const modal = read('static/app/css/modal-workflows.css');
  const combined = `${foundation}\n${modal}`;

  assert.match(tokens, /--control-touch-size:\s*2\.75rem/);
  assert.match(combined, /@media \(max-width:\s*767\.98px\)/);
  assert.match(combined, /@media \(max-width:\s*575\.98px\)/);
  assert.match(combined, /@media \(prefers-reduced-motion:\s*reduce\)/);
  assert.match(combined, /min-height:\s*var\(--control-touch-size\)/);
});

test('table and modal components own scrolling without assigning it to modal content', () => {
  const modal = read('static/app/css/modal-workflows.css');
  const templates = [
    'index/templates/inspections/profile_modal.html',
    'index/templates/devices/pc/config_modal.html',
    'index/templates/common/import_modal.html',
    'index/templates/domain/group_members.html',
  ].map(read).join('\n');

  assert.match(modal, /\.modal-body--scroll\s*\{[^}]*overflow-y:\s*auto/s);
  assert.doesNotMatch(modal, /\.modal-content\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(modal, /max-height:\s*calc\(100dvh - 1rem\)/);
  assert.match(modal, /\.modal-shell \.modal-header,[\s\S]*?flex:\s*0 0 auto/);
  assert.match(modal, /@media \(max-width:\s*575\.98px\)[\s\S]*?\.modal-footer--sticky/);
  assert.equal((templates.match(/modal-dialog-scrollable/g) || []).length >= 4, true);
  assert.equal((templates.match(/modal-footer--sticky/g) || []).length >= 4, true);
});

test('decorative workspace glows stay inside the page canvas', () => {
  const foundation = read('static/app/css/foundation.css');
  const glowRule = foundation.match(/\.execution-workspace::before,[\s\S]*?\{([^}]+)\}/);

  assert.ok(glowRule, 'shared execution glow rule should exist');
  assert.match(glowRule[1], /right:\s*0/);
  assert.doesNotMatch(glowRule[1], /right:\s*-[\d.]+(?:rem|px)/);
});
