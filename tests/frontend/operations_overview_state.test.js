const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

async function loadModule() {
  const source = readFileSync(
    resolve(__dirname, '../../static/app/js/operations-overview/state.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

function deferred() {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

function fakeTimers() {
  let nextId = 1;
  const pending = new Map();
  return {
    schedule(callback, delay) {
      const id = nextId++;
      pending.set(id, { callback, delay });
      return id;
    },
    cancel(id) { pending.delete(id); },
    pending,
    runFirst() {
      const entry = pending.entries().next().value;
      if (!entry) return undefined;
      const [id, timer] = entry;
      pending.delete(id);
      return timer.callback();
    },
  };
}

test('starts with the server snapshot and schedules a visible refresh', async () => {
  const { createRefreshController } = await loadModule();
  const timers = fakeTimers();
  const controller = createRefreshController({
    initialSnapshot: { schema_version: 1 },
    intervalMs: 30000,
    fetchSnapshot: async () => ({ schema_version: 2 }),
    now: () => 100,
    schedule: timers.schedule,
    cancel: timers.cancel,
    isVisible: () => true,
  });

  assert.deepEqual(controller.getState(), {
    lastSnapshot: { schema_version: 1 },
    refreshing: false,
    stale: false,
    error: '',
    lastSuccessfulAt: 100,
    manualPaused: false,
    hidden: false,
    running: false,
    view: 'situation',
    filter: 'all',
    viewport: { scale: 1, x: 0, y: 0 },
  });

  controller.start();
  assert.equal(controller.getState().running, true);
  assert.equal(timers.pending.size, 1);
  assert.equal([...timers.pending.values()][0].delay, 30000);
});

test('successful and failed refreshes preserve UI state and the last good snapshot', async () => {
  const { createRefreshController } = await loadModule();
  const timers = fakeTimers();
  const responses = [
    Promise.resolve({ schema_version: 2 }),
    Promise.reject(new Error('database credentials must not leak')),
  ];
  let clock = 10;
  const controller = createRefreshController({
    initialSnapshot: { schema_version: 1 },
    fetchSnapshot: () => responses.shift(),
    now: () => clock,
    schedule: timers.schedule,
    cancel: timers.cancel,
    isVisible: () => true,
  });
  controller.updateUiState({
    view: 'topology',
    filter: 'abnormal',
    viewport: { scale: 1.5, x: 12, y: -8 },
  });

  clock = 20;
  await controller.refresh();
  assert.equal(controller.getState().lastSnapshot.schema_version, 2);
  assert.equal(controller.getState().lastSuccessfulAt, 20);
  assert.equal(controller.getState().stale, false);

  await controller.refresh();
  const state = controller.getState();
  assert.equal(state.lastSnapshot.schema_version, 2);
  assert.equal(state.stale, true);
  assert.equal(state.error, '数据刷新失败，正在显示上次成功结果。');
  assert.equal(state.view, 'topology');
  assert.equal(state.filter, 'abnormal');
  assert.deepEqual(state.viewport, { scale: 1.5, x: 12, y: -8 });
  assert.doesNotMatch(state.error, /database|credential/i);
});

test('suppresses overlapping requests and notifies subscribers', async () => {
  const { createRefreshController } = await loadModule();
  const request = deferred();
  let calls = 0;
  const controller = createRefreshController({
    initialSnapshot: { value: 1 },
    fetchSnapshot: () => { calls += 1; return request.promise; },
    isVisible: () => true,
  });
  const states = [];
  const unsubscribe = controller.subscribe((state) => states.push(state.refreshing));

  const first = controller.refresh();
  const second = controller.refresh();
  assert.equal(calls, 1);
  assert.equal(first, second);
  request.resolve({ value: 2 });
  await first;

  assert.deepEqual(states, [true, false]);
  unsubscribe();
});

test('manual and visibility pauses control timers without overriding each other', async () => {
  const { createRefreshController } = await loadModule();
  const timers = fakeTimers();
  let visible = true;
  let calls = 0;
  const controller = createRefreshController({
    initialSnapshot: { value: 1 },
    intervalMs: 50,
    fetchSnapshot: async () => { calls += 1; return { value: calls + 1 }; },
    schedule: timers.schedule,
    cancel: timers.cancel,
    isVisible: () => visible,
  });

  controller.start();
  assert.equal(timers.pending.size, 1);
  controller.setManualPaused(true);
  assert.equal(timers.pending.size, 0);

  visible = false;
  await controller.handleVisibilityChange();
  visible = true;
  await controller.handleVisibilityChange();
  assert.equal(calls, 0, 'manual pause survives a hidden/visible cycle');
  assert.equal(timers.pending.size, 0);

  controller.setManualPaused(false);
  assert.equal(timers.pending.size, 1);
  visible = false;
  await controller.handleVisibilityChange();
  assert.equal(timers.pending.size, 0);
  visible = true;
  await controller.handleVisibilityChange();
  assert.equal(calls, 1, 'becoming visible refreshes immediately');
  assert.equal(timers.pending.size, 1);

  controller.stop();
  assert.equal(controller.getState().running, false);
  assert.equal(timers.pending.size, 0);
});
