const DEFAULT_INTERVAL_MS = 30000;
const SAFE_REFRESH_ERROR = '数据刷新失败，正在显示上次成功结果。';

export function createRefreshController({
  initialSnapshot,
  intervalMs = DEFAULT_INTERVAL_MS,
  fetchSnapshot,
  now = () => Date.now(),
  schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
  cancel = (timerId) => globalThis.clearTimeout(timerId),
  isVisible = () => true,
}) {
  if (typeof fetchSnapshot !== 'function') {
    throw new TypeError('fetchSnapshot must be a function');
  }

  const listeners = new Set();
  const state = {
    lastSnapshot: initialSnapshot,
    refreshing: false,
    stale: false,
    error: '',
    lastSuccessfulAt: now(),
    manualPaused: false,
    hidden: !isVisible(),
    running: false,
    view: 'situation',
    filter: 'all',
    viewport: { scale: 1, x: 0, y: 0 },
  };
  let timerId = null;
  let inFlight = null;

  function snapshotState() {
    return {
      ...state,
      viewport: { ...state.viewport },
    };
  }

  function notify() {
    const value = snapshotState();
    listeners.forEach((listener) => listener(value));
  }

  function cancelTimer() {
    if (timerId !== null) {
      cancel(timerId);
      timerId = null;
    }
  }

  function scheduleNext() {
    cancelTimer();
    if (!state.running || state.manualPaused || state.hidden) return;
    timerId = schedule(() => {
      timerId = null;
      return refresh();
    }, intervalMs);
  }

  function refresh() {
    if (inFlight) return inFlight;
    cancelTimer();
    state.refreshing = true;
    notify();

    let fetchResult;
    try {
      fetchResult = fetchSnapshot();
    } catch (error) {
      fetchResult = Promise.reject(error);
    }
    inFlight = Promise.resolve(fetchResult)
      .then((nextSnapshot) => {
        state.lastSnapshot = nextSnapshot;
        state.lastSuccessfulAt = now();
        state.stale = false;
        state.error = '';
        return nextSnapshot;
      })
      .catch(() => {
        state.stale = true;
        state.error = SAFE_REFRESH_ERROR;
        return state.lastSnapshot;
      })
      .finally(() => {
        state.refreshing = false;
        inFlight = null;
        notify();
        scheduleNext();
      });
    return inFlight;
  }

  function start() {
    if (state.running) return;
    state.running = true;
    state.hidden = !isVisible();
    notify();
    scheduleNext();
  }

  function stop() {
    cancelTimer();
    state.running = false;
    notify();
  }

  function setManualPaused(value) {
    state.manualPaused = Boolean(value);
    notify();
    scheduleNext();
  }

  function handleVisibilityChange() {
    const wasHidden = state.hidden;
    state.hidden = !isVisible();
    notify();
    if (state.hidden) {
      cancelTimer();
      return Promise.resolve(state.lastSnapshot);
    }
    if (wasHidden && state.running && !state.manualPaused) return refresh();
    scheduleNext();
    return Promise.resolve(state.lastSnapshot);
  }

  function updateUiState(nextState) {
    if (nextState.view !== undefined) state.view = nextState.view;
    if (nextState.filter !== undefined) state.filter = nextState.filter;
    if (nextState.viewport !== undefined) {
      state.viewport = { ...state.viewport, ...nextState.viewport };
    }
    notify();
  }

  return {
    getState: snapshotState,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start,
    stop,
    refresh,
    setManualPaused,
    handleVisibilityChange,
    updateUiState,
  };
}
