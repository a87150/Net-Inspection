const test = require('node:test');
const assert = require('node:assert/strict');

const modal = require('../../static/app/js/domain/operation_modal.js');

function eventTarget(overrides = {}) {
    const listeners = new Map();
    return {
        value: '',
        textContent: '',
        hidden: false,
        dataset: {},
        addEventListener(name, callback) {
            if (!listeners.has(name)) listeners.set(name, []);
            listeners.get(name).push(callback);
        },
        dispatch(name) {
            (listeners.get(name) || []).forEach((callback) => callback({target: this}));
        },
        listenerNames() {
            return Array.from(listeners.keys()).sort();
        },
        ...overrides,
    };
}

function fixture(initialAction = 'move_ou') {
    const actionValue = eventTarget();
    const actionText = eventTarget();
    const countText = eventTarget();
    const scopeRow = eventTarget({hidden: true});
    const scopeLabel = eventTarget();
    const scopeText = eventTarget();
    const holder = eventTarget({replaceChildren() {}});
    const destination = eventTarget({dataset: {domainSummaryInput: 'destination_dn'}});
    const group = eventTarget({dataset: {domainSummaryInput: 'group_dn'}});
    const parameterFields = [
        eventTarget({dataset: {domainParameterFor: 'move_ou'}, querySelectorAll: () => [destination]}),
        eventTarget({dataset: {domainParameterFor: 'add_group'}, querySelectorAll: () => [group]}),
    ];
    const formMap = new Map([
        ['[data-domain-operation-action-value]', actionValue],
        ['[data-domain-confirm-action]', actionText],
        ['[data-domain-confirm-target-count]', countText],
        ['[data-domain-confirm-scope-row]', scopeRow],
        ['[data-domain-confirm-scope-label]', scopeLabel],
        ['[data-domain-confirm-scope]', scopeText],
        ['[data-domain-selected-targets]', holder],
    ]);
    const form = eventTarget({
        querySelector(selector) {
            return formMap.get(selector) || null;
        },
        querySelectorAll(selector) {
            if (selector === '[data-domain-parameter-for]') return parameterFields;
            if (selector === '[data-domain-summary-input]') return [destination, group];
            return [];
        },
    });
    const options = [
        {value: 'move_ou', textContent: '移动到 OU'},
        {value: 'add_group', textContent: '加入安全组'},
    ];
    const actionSelect = eventTarget({options});
    Object.defineProperty(actionSelect, 'selectedIndex', {
        get() {
            return options.findIndex((option) => option.value === this.value);
        },
    });
    actionSelect.value = initialAction;
    const checkedTarget = eventTarget({value: 'target-1', checked: true});
    const doc = {
        querySelector(selector) {
            if (selector === '[data-domain-operation-action]') return actionSelect;
            if (selector === '[data-domain-operation-form]') return form;
            return null;
        },
        querySelectorAll(selector) {
            if (selector === '[data-domain-target-checkbox]:checked') return [checkedTarget];
            if (selector === '[data-domain-target-checkbox]') return [checkedTarget];
            return [];
        },
        createElement() {
            return eventTarget();
        },
    };
    return {
        doc, actionSelect, destination, group,
        actionValue, actionText, countText, scopeRow, scopeLabel, scopeText,
    };
}

test('destination OU input and change refresh the visible impact summary immediately', () => {
    const view = fixture('move_ou');
    modal.bindDomainOperationModal(view.doc);

    assert.deepEqual(view.destination.listenerNames(), ['change', 'input']);
    view.destination.value = 'OU=Managed,DC=example,DC=test';
    view.destination.dispatch('input');
    assert.equal(view.scopeRow.hidden, false);
    assert.equal(view.scopeLabel.textContent, '目标 OU');
    assert.equal(view.scopeText.textContent, 'OU=Managed,DC=example,DC=test');

    view.destination.value = 'OU=Archive,DC=example,DC=test';
    view.destination.dispatch('change');
    assert.equal(view.scopeText.textContent, 'OU=Archive,DC=example,DC=test');
});

test('group DN input and change refresh add group scope without submit', () => {
    const view = fixture('add_group');
    modal.bindDomainOperationModal(view.doc);

    assert.deepEqual(view.group.listenerNames(), ['change', 'input']);
    view.group.value = 'CN=Operators,OU=Groups,DC=example,DC=test';
    view.group.dispatch('input');
    assert.equal(view.scopeRow.hidden, false);
    assert.equal(view.scopeLabel.textContent, '安全组');
    assert.equal(view.scopeText.textContent, 'CN=Operators,OU=Groups,DC=example,DC=test');

    view.group.value = 'CN=Auditors,OU=Groups,DC=example,DC=test';
    view.group.dispatch('change');
    assert.equal(view.scopeText.textContent, 'CN=Auditors,OU=Groups,DC=example,DC=test');
});


test('switching actions disables the other directory selector without losing its choice', () => {
    const view = fixture('move_ou');
    view.destination.value = 'OU=Empty,DC=example,DC=test';
    modal.bindDomainOperationModal(view.doc);
    assert.equal(view.destination.disabled, false);
    assert.equal(view.group.disabled, true);
    view.actionSelect.value = 'add_group';
    view.actionSelect.dispatch('change');
    assert.equal(view.destination.disabled, true);
    assert.equal(view.group.disabled, false);
    assert.equal(view.destination.value, 'OU=Empty,DC=example,DC=test');
});
