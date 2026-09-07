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
