'use strict';
const test = require('node:test');
const assert = require('node:assert');

const {
    fillTargets, refreshTrigger, selectedIds,
} = require('../../static/app/js/devices/maintenance_window.js');

function fakeRoot(checked) {
    const trigger = {disabled: true, textContent: '加入维护窗口'};
    return {
        trigger,
        querySelector: (selector) => (selector === '[data-maintenance-open]' ? trigger : null),
        querySelectorAll: () => checked.map((value) => ({value})),
    };
}

function fakeContainer() {
    const children = [];
    return {
        children,
        ownerDocument: {createElement: () => ({type: '', name: '', value: ''})},
        replaceChildren() { children.length = 0; },
        append(node) { children.push(node); },
    };
}

test('selectedIds keeps the value of every checked row', () => {
    assert.deepEqual(selectedIds(fakeRoot(['a', 'b'])), ['a', 'b']);
});

test('refreshTrigger disables the button until a row is selected', () => {
    const empty = fakeRoot([]);
    assert.equal(refreshTrigger(empty), 0);
    assert.equal(empty.trigger.disabled, true);
    assert.equal(empty.trigger.textContent, '加入维护窗口');

    const chosen = fakeRoot(['x', 'y']);
    assert.equal(refreshTrigger(chosen), 2);
    assert.equal(chosen.trigger.disabled, false);
    assert.equal(chosen.trigger.textContent, '加入维护窗口（2）');
});

test('fillTargets writes one hidden input per asset and clears the previous set', () => {
    const container = fakeContainer();
    assert.equal(fillTargets(container, ['a', 'b']), 2);
    assert.deepEqual(container.children.map((node) => node.value), ['a', 'b']);
    assert.deepEqual(container.children.map((node) => node.name), ['target_ids', 'target_ids']);
    assert.deepEqual(container.children.map((node) => node.type), ['hidden', 'hidden']);

    assert.equal(fillTargets(container, ['c']), 1);
    assert.deepEqual(container.children.map((node) => node.value), ['c']);
});
