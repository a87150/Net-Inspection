import test from 'node:test';
import assert from 'node:assert/strict';

import {
  nextSelection, submitDisabled, summaryLabel,
} from '../../static/app/js/domain/member_selection.mjs';

test('select all takes every enabled candidate', () => {
  assert.deepEqual(nextSelection(['a'], ['a', 'b', 'c'], 'all'), ['a', 'b', 'c']);
});

test('clear drops the whole selection', () => {
  assert.deepEqual(nextSelection(['a', 'b'], ['a', 'b'], 'none'), []);
});

test('invert keeps only the candidates that were not chosen', () => {
  assert.deepEqual(nextSelection(['a', 'c'], ['a', 'b', 'c'], 'invert'), ['b']);
});

test('invert never invents values outside the current page', () => {
  assert.deepEqual(nextSelection(['a', 'gone'], ['a', 'b'], 'invert'), ['b']);
});

test('unknown action keeps the current selection', () => {
  assert.deepEqual(nextSelection(['b'], ['a', 'b'], 'noop'), ['b']);
});

test('summary reports chosen against the actionable total', () => {
  assert.equal(summaryLabel(0, 12), '已选 0 / 可选 12');
  assert.equal(summaryLabel(3, 12), '已选 3 / 可选 12');
});

test('submit stays disabled with an empty selection or a locked group', () => {
  assert.equal(submitDisabled(0, false), true);
  assert.equal(submitDisabled(2, false), false);
  assert.equal(submitDisabled(2, true), true);
});
