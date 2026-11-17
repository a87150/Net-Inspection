/** Pure selection helpers for the group member island; no DOM, easy to test. */

export function nextSelection(selected, candidates, action) {
  const chosen = new Set(selected);
  if (action === 'all') return [...candidates];
  if (action === 'none') return [];
  if (action === 'invert') return candidates.filter(value => !chosen.has(value));
  return [...chosen];
}

export function summaryLabel(selectedCount, total) {
  return `已选 ${selectedCount} / 可选 ${total}`;
}

export function submitDisabled(selectedCount, locked) {
  return Boolean(locked) || selectedCount === 0;
}
