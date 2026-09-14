'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let feedback = {};
try {
    feedback = require('../../static/app/js/common/modal_feedback.js');
} catch (_error) {
    feedback = {};
}

function fixture({autoOpen = true} = {}) {
    const messages = [{textContent: '保存成功'}, {textContent: '配置失败'}];
    const body = {
        stack: null,
        querySelector(selector) {
            return selector === '[data-modal-feedback]' ? this.stack : null;
        },
        prepend(element) {
            this.stack = element;
        },
    };
    const modal = {querySelector: selector => selector === '.modal-body' ? body : null};
    const listeners = new Map();
    const document = {
        addEventListener(name, callback) {
            listeners.set(name, callback);
        },
        dispatch(name) {
            listeners.get(name)?.();
        },
        querySelector(selector) {
            if (selector === '.modal[data-auto-open="true"]') return autoOpen ? modal : null;
            return null;
        },
        querySelectorAll(selector) {
            return selector === '[data-flash-message]' ? messages : [];
        },
        createElement(tagName) {
            assert.equal(tagName, 'div');
            return {
                children: [],
                className: '',
                attributes: {},
                setAttribute(name, value) { this.attributes[name] = value; },
                append(element) { this.children.push(element); },
            };
        },
    };
    return {document, modal, body, messages};
}

test('draft capture excludes server-owned hidden configuration identity', () => {
    const draft = feedback.captureFormDraft({elements: [
        {name: 'profile_id', type: 'hidden', value: ''},
        {name: 'next', type: 'hidden', value: '/old-page/'},
        {name: 'name', type: 'text', value: 'PC analysis'},
    ]});
    assert.deepEqual(draft.map(entry => entry.name), ['name']);
});

test('legacy create draft cannot clear the newly saved profile id on reopening', () => {
    const identity = {name: 'profile_id', type: 'hidden', value: 'saved-profile-id'};
    const name = {name: 'name', type: 'text', value: 'PC analysis'};
    feedback.restoreFormDraft({elements: [identity, name]}, [
        {name: 'profile_id', type: 'hidden', value: ''},
        {name: 'name', type: 'text', value: 'Edited analysis'},
    ]);
    assert.equal(identity.value, 'saved-profile-id');
    assert.equal(name.value, 'Edited analysis');
});

test('moves page flash messages into the automatically reopened modal', () => {
    assert.equal(typeof feedback.installModalFeedback, 'function');
    const view = fixture();

    feedback.installModalFeedback(view.document);
    view.document.dispatch('DOMContentLoaded');

    assert.deepEqual(view.body.stack.children, view.messages);
    assert.equal(view.body.stack.attributes['aria-live'], 'polite');
    assert.equal(view.body.stack.attributes.role, 'region');
});

test('keeps flash messages at page level when no modal is automatically reopened', () => {
    assert.equal(typeof feedback.installModalFeedback, 'function');
    const view = fixture({autoOpen: false});

    feedback.installModalFeedback(view.document);
    view.document.dispatch('DOMContentLoaded');

    assert.equal(view.body.stack, null);
});

test('renders transport errors through the shared modal feedback surface', () => {
    assert.equal(typeof feedback.showModalFeedback, 'function');
    const view = fixture();

    const message = feedback.showModalFeedback(
        view.document,
        view.modal,
        '连接失败，请重试。',
        'danger',
    );

    assert.equal(message.className, 'alert alert-danger');
    assert.equal(message.textContent, '连接失败，请重试。');
    assert.equal(message.attributes.role, 'status');
    assert.equal(view.body.stack, message);
});
test('keeps valid modal values while excluding passwords and uploaded files', () => {
    assert.equal(typeof feedback.captureFormDraft, 'function');
    const fields = [
        {name: 'name', type: 'text', value: '巡检配置 A', disabled: false},
        {name: 'vendor', type: 'select-one', value: 'Cisco', disabled: false},
        {name: 'enabled', type: 'checkbox', value: 'on', checked: true, disabled: false},
        {name: 'devices', type: 'select-multiple', value: '1', options: [{value: '1', selected: true}, {value: '2', selected: true}, {value: '3', selected: false}], disabled: false},
        {name: 'password', type: 'password', value: 'secret', disabled: false},
        {name: 'file', type: 'file', value: 'devices.csv', disabled: false},
        {name: 'csrfmiddlewaretoken', type: 'hidden', value: 'token', disabled: false},
    ];

    const draft = feedback.captureFormDraft({elements: fields});

    assert.deepEqual(draft, [
        {name: 'name', type: 'text', value: '巡检配置 A', checked: false},
        {name: 'vendor', type: 'select-one', value: 'Cisco', checked: false},
        {name: 'enabled', type: 'checkbox', value: 'on', checked: true},
        {name: 'devices', type: 'select-multiple', value: '1', checked: false, values: ['1', '2']},
    ]);
});
test('restores retained values without clearing unrelated correct fields', () => {
    assert.equal(typeof feedback.restoreFormDraft, 'function');
    const fields = [
        {name: 'name', type: 'text', value: ''},
        {name: 'vendor', type: 'select-one', value: ''},
        {name: 'enabled', type: 'checkbox', value: 'on', checked: false},
        {name: 'timeout', type: 'number', value: ''},
        {name: 'devices', type: 'select-multiple', value: '', options: [{value: '1', selected: false}, {value: '2', selected: false}, {value: '3', selected: true}]},
    ];
    const draft = [
        {name: 'name', type: 'text', value: '巡检配置 A', checked: false},
        {name: 'vendor', type: 'select-one', value: 'Cisco', checked: false},
        {name: 'enabled', type: 'checkbox', value: 'on', checked: true},
        {name: 'timeout', type: 'number', value: '30', checked: false},
        {name: 'devices', type: 'select-multiple', value: '1', checked: false, values: ['1', '2']},
    ];

    feedback.restoreFormDraft({elements: fields}, draft);

    assert.equal(fields[0].value, '巡检配置 A');
    assert.equal(fields[1].value, 'Cisco');
    assert.equal(fields[2].checked, true);
    assert.equal(fields[3].value, '30');
    assert.deepEqual(fields[4].options.map(option => option.selected), [true, true, false]);
});
