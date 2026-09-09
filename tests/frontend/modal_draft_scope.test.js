'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const feedback = require('../../static/app/js/common/modal_feedback.js');

function openForm(storage, {id = 'A', path = '/assets/servers/', action = '/save/', success = false,
                           identityType = 'hidden', name = 'server value'} = {}) {
    let ready;
    const modal = {id: 'profileConfigModal', querySelector: () => null};
    const form = new EventTarget();
    const identity = {name: 'profile_id', type: identityType, value: id};
    const field = {name: 'name', type: 'text', value: name};
    form.id = 'config-form';
    form.elements = [identity, field];
    form.closest = () => modal;
    form.getAttribute = key => key === 'action' ? action : null;
    const messages = success ? [{classList: {contains: value => value === 'alert-success'}}] : [];
    const document = {
        location: {pathname: path}, defaultView: {Event, sessionStorage: {
            getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key),
        }},
        addEventListener: (event, callback) => { ready = callback; },
        querySelector: () => modal,
        querySelectorAll: selector => selector === '.modal form' ? [form] : messages,
    };
    form.ownerDocument = document;
    modal.contains = value => value === form;
    feedback.installModalFeedback(document);
    ready();
    return {identity, field, submit: () => form.dispatchEvent(new Event('submit'))};
}

for (const identityType of ['hidden', 'select-one']) {
    test(`draft cannot cross ${identityType} configuration identity`, () => {
        const storage = new Map();
        openForm(storage, {identityType, name: 'edited A'}).submit();
        const next = openForm(storage, {id: 'B', identityType, name: 'saved B'});
        assert.equal(next.identity.value, 'B');
        assert.equal(next.field.value, 'saved B');
    });
}

for (const change of [{path: '/assets/networks/'}, {action: '/another-save/'}]) {
    test(`draft is isolated by ${Object.keys(change)[0]}`, () => {
        const storage = new Map();
        openForm(storage, {name: 'edited A'}).submit();
        assert.equal(openForm(storage, {...change, name: 'different context'}).field.value, 'different context');
    });
}

test('same-object failed save retains input once', () => {
    const storage = new Map();
    openForm(storage, {name: 'unsaved input'}).submit();
    assert.equal(openForm(storage).field.value, 'unsaved input');
    assert.equal(openForm(storage).field.value, 'server value');
});

test('successful save keeps canonical server values instead of replaying input', () => {
    const storage = new Map();
    openForm(storage, {name: '  untrimmed  '}).submit();
    assert.equal(openForm(storage, {success: true, name: 'untrimmed'}).field.value, 'untrimmed');
});

test('legacy unscoped drafts are not applied to a different object', () => {
    const storage = new Map([['net-modal-form:profileConfigModal:config-form', JSON.stringify([
        {name: 'name', type: 'text', value: 'legacy object'},
    ])]]);
    assert.equal(openForm(storage).field.value, 'server value');
});

test('removed checkbox value cannot select a different channel', () => {
    const channel = {name: 'channels', type: 'checkbox', value: 'B', checked: false};
    feedback.restoreFormDraft({elements: [channel]}, [
        {name: 'channels', type: 'checkbox', value: 'A', checked: true},
    ]);
    assert.equal(channel.checked, false);
});

test('removed radio value cannot select a different operation', () => {
    const operation = {name: 'operation', type: 'radio', value: 'enable', checked: false};
    feedback.restoreFormDraft({elements: [operation]}, [
        {name: 'operation', type: 'radio', value: 'disable', checked: true},
    ]);
    assert.equal(operation.checked, false);
});

test('editable device type is retained rather than treated as a hidden routing discriminator', () => {
    const field = {name: 'device_type', type: 'select-one', value: 'access_control'};
    const draft = feedback.captureFormDraft({elements: [field]});
    field.value = 'camera';
    feedback.restoreFormDraft({elements: [field]}, draft);
    assert.equal(field.value, 'access_control');
});
