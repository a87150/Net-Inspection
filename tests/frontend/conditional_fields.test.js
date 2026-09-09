'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/common/conditional_fields.js');
} catch (_error) {
    controller = {};
}

function group() {
    const controls = [{disabled: false}, {disabled: false}];
    return {
        hidden: false,
        controls,
        querySelectorAll: () => controls,
    };
}

test('daily schedule exposes only daily controls and disables hidden interval values', () => {
    assert.equal(typeof controller.updateScheduleFields, 'function');
    const interval = group();
    const daily = group();
    const form = {
        querySelectorAll(selector) {
            return selector === '[data-schedule-interval]' ? [interval] : [daily];
        },
    };
    const select = {value: 'daily', closest: () => form};

    controller.updateScheduleFields(select);

    assert.equal(interval.hidden, true);
    assert.deepEqual(interval.controls.map(control => control.disabled), [true, true]);
    assert.equal(daily.hidden, false);
    assert.deepEqual(daily.controls.map(control => control.disabled), [false, false]);
});

test('interval schedule exposes only interval controls', () => {
    assert.equal(typeof controller.updateScheduleFields, 'function');
    const interval = group();
    const daily = group();
    const form = {
        querySelectorAll(selector) {
            return selector === '[data-schedule-interval]' ? [interval] : [daily];
        },
    };

    controller.updateScheduleFields({value: 'interval', closest: () => form});

    assert.equal(interval.hidden, false);
    assert.equal(daily.hidden, true);
    assert.deepEqual(daily.controls.map(control => control.disabled), [true, true]);
});

test('restoring a daily schedule enables its time without dispatching a navigation change', () => {
    const {restoreFormDraft} = require('../../static/app/js/common/modal_feedback.js');
    const form = new EventTarget();
    const select = new EventTarget();
    Object.assign(select, {name: 'schedule_kind', type: 'select-one', value: 'interval', dataset: {}, closest: () => form});
    const time = {name: 'daily_time', type: 'time', value: ''};
    const interval = {name: 'interval_value', type: 'number', value: '30'};
    const dailyGroup = {hidden: false, querySelectorAll: () => [time]};
    const intervalGroup = {hidden: false, querySelectorAll: () => [interval]};
    form.elements = [select, time, interval];
    form.ownerDocument = {defaultView: {Event}};
    form.querySelectorAll = selector => selector === '[data-schedule-interval]' ? [intervalGroup] : [dailyGroup];
    controller.bindScheduleFields({querySelectorAll: selector => selector === '[data-schedule-kind]' ? [select] : []});
    let changes = 0;
    select.addEventListener('change', () => { changes += 1; });
    restoreFormDraft(form, [
        {name: 'schedule_kind', type: 'select-one', value: 'daily'},
        {name: 'daily_time', type: 'time', value: '03:15'},
    ]);
    assert.equal(time.disabled, false);
    assert.equal(dailyGroup.hidden, false);
    assert.equal(time.value, '03:15');
    assert.equal(interval.disabled, true);
    assert.equal(changes, 0);
});


test('server type exposes only applicable parameters and disables the others', () => {
    const linux = group(); linux.dataset = {serverFields: 'linux'};
    const windows = group(); windows.dataset = {serverFields: 'windows'};
    const select = {value: 'windows', closest: () => ({querySelectorAll: () => [linux, windows]})};
    controller.updateServerFields(select);
    assert.equal(linux.hidden, true);
    assert.ok(linux.controls.every(control => control.disabled));
    assert.equal(windows.hidden, false);
    select.value = 'linux';
    controller.updateServerFields(select);
    assert.equal(linux.hidden, false);
    assert.ok(windows.controls.every(control => control.disabled));
});
