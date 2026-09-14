'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/alerts/template_modal.js');
} catch (_error) {
    controller = {};
}

test('notification template modal loads settings in the current dialog', () => {
    assert.equal(typeof controller.installTemplateModalLoader, 'function');
    const listeners = new Map();
    const document = {addEventListener(type, listener) { listeners.set(type, listener); }};
    const modal = {id: 'alertTemplateModal', dataset: {templateSettingsUrl: '/alerts/template/'}};
    const calls = [];

    controller.installTemplateModalLoader(document, {
        navigate(value, url) { calls.push([value, url]); },
    });
    listeners.get('show.bs.modal')({target: modal});

    assert.deepEqual(calls, [[modal, '/alerts/template/']]);
});
