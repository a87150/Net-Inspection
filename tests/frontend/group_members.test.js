const test = require('node:test');
const assert = require('node:assert/strict');
const {
    selectMembers,
    selectionState,
    updateMemberSelectionState,
} = require('../../static/app/js/domain/group_members.js');

test('member selection only changes enabled candidates in the current form', () => {
    const rows = [{checked: false}, {checked: false}, {checked: false, disabled: true}];
    const form = {querySelectorAll: () => rows};
    selectMembers(form, 'all');
    assert.deepEqual(rows.map(row => row.checked), [true, true, false]);
    selectMembers(form, 'none');
    assert.deepEqual(rows.map(row => row.checked), [false, false, false]);
});

test('member selection can invert the current page and reports only actionable choices', () => {
    const rows = [
        {checked: true, disabled: false},
        {checked: false, disabled: false},
        {checked: true, disabled: true},
    ];
    const form = {querySelectorAll: () => rows};

    selectMembers(form, 'invert');

    assert.deepEqual(rows.map(row => row.checked), [false, true, true]);
    assert.deepEqual(selectionState(form), {selected: 1, total: 2});
});

test('selection feedback and primary action follow the current checked count', () => {
    const rows = [{checked: false, disabled: false}, {checked: false, disabled: false}];
    const summaries = [{textContent: ''}, {textContent: ''}];
    const submit = {disabled: false};
    const surface = {
        querySelectorAll: selector => selector === '[data-member-selection-summary]' ? summaries : [],
        querySelector: selector => selector === '[data-member-submit]' ? submit : null,
    };
    const form = {
        querySelectorAll: () => rows,
        closest: selector => selector === '.modal-content' ? surface : null,
    };

    updateMemberSelectionState(form);
    assert.deepEqual(summaries.map(item => item.textContent), ['已选 0 / 可选 2', '已选 0 / 可选 2']);
    assert.equal(submit.disabled, true);

    rows[1].checked = true;
    updateMemberSelectionState(form);
    assert.deepEqual(summaries.map(item => item.textContent), ['已选 1 / 可选 2', '已选 1 / 可选 2']);
    assert.equal(submit.disabled, false);
});
