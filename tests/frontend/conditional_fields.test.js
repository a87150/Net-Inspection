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

test('SNMP version and security show only needed credentials without discarding values', () => {
    const groups=['v2c','v3','auth','priv'].map(kind=>{
        const item=group(); item.dataset={snmpGroup:kind}; return item;
    });
    const version={value:'v2c'},security={value:'authPriv'};
    const form={querySelector:selector=>selector==='[data-snmp-version]'?version:security,querySelectorAll:()=>groups};
    const select={closest:()=>form};
    controller.updateSnmpFields(select);
    assert.deepEqual(groups.map(g=>g.hidden),[false,true,true,true]);
    assert.deepEqual(groups.map(g=>g.controls.every(control=>control.disabled)),[false,true,true,true]);
    version.value='v3';security.value='noAuthNoPriv';controller.updateSnmpFields(select);
    assert.deepEqual(groups.map(g=>g.hidden),[true,false,true,true]);
    security.value='authNoPriv';controller.updateSnmpFields(select);
    assert.deepEqual(groups.map(g=>g.hidden),[true,false,false,true]);
    security.value='authPriv';controller.updateSnmpFields(select);
    assert.deepEqual(groups.map(g=>g.hidden),[true,false,false,false]);
});


test('network API mode exposes only API controls and disables SSH and SNMP controls', () => {
    const api = group(); api.dataset = {networkFields: 'sangfor_api'};
    const standard = group(); standard.dataset = {networkFields: 'standard'};
    const snmp = group(); snmp.dataset = {snmpGroup: 'v2c'};
    const version = {value: 'v2c'};
    const security = {value: 'noAuthNoPriv'};
    const form = {
        querySelector(selector) { return selector === '[data-snmp-version]' ? version : security; },
        querySelectorAll(selector) {
            if (selector === '[data-network-fields]') return [api, standard];
            return [snmp];
        },
    };
    const select = {value: 'sangfor_api', closest: () => form};
    controller.updateNetworkFields(select);
    assert.equal(api.hidden, false);
    assert.ok(api.controls.every(control => !control.disabled));
    assert.equal(standard.hidden, true);
    assert.ok(standard.controls.every(control => control.disabled));

    select.value = 'auto';
    controller.updateNetworkFields(select);
    assert.equal(api.hidden, true);
    assert.ok(api.controls.every(control => control.disabled));
    assert.equal(standard.hidden, false);
    assert.ok(standard.controls.every(control => !control.disabled));
});

test('new Sangfor gateway selects API while editing an existing device does not change mode', () => {
    function candidate(isNew) {
        const connection = new EventTarget();
        Object.assign(connection, {value: 'auto', dataset: {networkNew: isNew ? 'true' : 'false'}});
        let changes = 0;
        connection.addEventListener('change', () => { changes += 1; });
        const vendor = {value: 'sangfor'};
        const deviceType = {value: 'ac_gateway'};
        return {connection, changes: () => changes, form: {querySelector(selector) {
            return selector === '[data-network-connection]' ? connection : selector === '[data-network-vendor]' ? vendor : deviceType;
        }}};
    }
    const fresh = candidate(true);
    controller.chooseSangforApi(fresh.form);
    assert.equal(fresh.connection.value, 'sangfor_api');
    assert.equal(fresh.changes(), 1);
    const edit = candidate(false);
    controller.chooseSangforApi(edit.form);
    assert.equal(edit.connection.value, 'auto');
    assert.equal(edit.changes(), 0);
});

test('draft restoration keeps SNMP controls disabled while API mode remains selected', () => {
    const connection = new EventTarget();
    Object.assign(connection, {value: 'sangfor_api', dataset: {networkNew: 'false'}, closest: () => form});
    const api = group(); api.dataset = {networkFields: 'sangfor_api'};
    const standard = group(); standard.dataset = {networkFields: 'standard', snmpGroup: 'v2c'};
    const snmp = standard;
    const version = {value: 'v2c'};
    const security = {value: 'noAuthNoPriv'};
    const form = new EventTarget();
    form.querySelector = selector => selector === '[data-snmp-version]' ? version : selector === '[data-snmp-security]' ? security : null;
    form.querySelectorAll = selector => {
        if (selector === '[data-network-fields]') return [api, standard];
        if (selector === '[data-snmp-group]') return [snmp];
        return [];
    };
    const root = {querySelectorAll: selector => selector === '[data-network-connection]' ? [connection] : []};
    controller.bindScheduleFields(root);
    form.dispatchEvent(new Event('modal-draft-restored'));
    assert.ok(standard.controls.every(control => control.disabled));
    assert.ok(snmp.controls.every(control => control.disabled));
    assert.ok(api.controls.every(control => !control.disabled));
});
