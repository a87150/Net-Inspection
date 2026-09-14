'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/access/source_modal.js');
} catch (_error) {
    controller = {};
}

test('access source page reloads only after a saved editor closes', () => {
    assert.equal(typeof controller.installSourceModalReload, 'function');
    const listeners = new Map();
    const document = {addEventListener(type, listener) { listeners.set(type, listener); }};
    const browserWindow = {location: {reloads: 0, reload() { this.reloads += 1; }}};
    const modal = {
        id: 'accessSourceModal',
        querySelector(selector) { return selector === '[data-source-saved]' ? {} : null; },
    };

    controller.installSourceModalReload(document, browserWindow);
    listeners.get('app:modal-updated')({detail: {modal}});
    listeners.get('hidden.bs.modal')({target: modal});

    assert.equal(browserWindow.location.reloads, 1);
});
