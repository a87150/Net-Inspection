'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/common/confirm_dialog.js');
} catch (_error) {
    controller = {};
}

test('dangerous form waits for the shared confirmation dialog', () => {
    assert.equal(typeof controller.installConfirmDialog, 'function');
    const listeners = new Map();
    const message = {textContent: ''};
    const confirmButton = {
        listener: null,
        addEventListener(_type, listener) { this.listener = listener; },
    };
    const modal = {
        querySelector(selector) {
            return selector === '[data-confirm-text]' ? message
                : selector === '[data-confirm-submit]' ? confirmButton
                    : null;
        },
    };
    const modalApi = {
        shown: 0,
        hidden: 0,
        show() { this.shown += 1; },
        hide() { this.hidden += 1; },
    };
    const document = {
        getElementById(id) { return id === 'appConfirmModal' ? modal : null; },
        addEventListener(type, listener) { listeners.set(type, listener); },
    };
    const bootstrap = {
        Modal: {getOrCreateInstance(value) { assert.equal(value, modal); return modalApi; }},
    };
    const submitter = {name: 'action'};
    const form = {
        dataset: {confirmMessage: '确定结束这个任务吗？'},
        submissions: [],
        requestSubmit(value) { this.submissions.push(value); },
    };
    const event = {
        target: form,
        submitter,
        prevented: false,
        preventDefault() { this.prevented = true; },
    };

    controller.installConfirmDialog(document, bootstrap);
    listeners.get('submit')(event);

    assert.equal(event.prevented, true);
    assert.equal(message.textContent, '确定结束这个任务吗？');
    assert.equal(modalApi.shown, 1);

    confirmButton.listener();

    assert.deepEqual(form.submissions, [submitter]);
    assert.equal(modalApi.hidden, 1);
});
