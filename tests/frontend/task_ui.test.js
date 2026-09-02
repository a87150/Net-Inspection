const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = {module: {exports: {}}, document: {addEventListener() {}}, URL};
vm.runInNewContext(fs.readFileSync(require.resolve('../../static/app/js/inspections/task_ui.js'), 'utf8'), context);
const ui = context.module.exports;

test('profile switch reloads selected full state and retains row scope', () => {
    assert.equal(typeof ui.switchProfile, 'function');
    const location = {href:'http://localhost/assets/servers/?filter_server_type=linux', assign(url) {this.next=url;}};
    const form = {querySelectorAll: () => []};
    const select = {value:'memory-profile', closest: (selector) => selector === 'form' ? form : {id:'runTaskModal'}};
    const doc = {querySelectorAll: () => [{value:'row-b'}], querySelector: () => ({value:'selected'})};
    ui.switchProfile(select, location, doc);
    const url = new URL(location.next);
    assert.equal(url.searchParams.get('task_profile'), 'memory-profile');
    assert.equal(url.searchParams.get('task_modal'), 'run');
    assert.equal(url.searchParams.get('task_targets'), 'row-b');
    assert.equal(url.searchParams.get('filter_server_type'), 'linux');
});

test('row action clears previous selection and chooses exactly clicked row', () => {
    assert.equal(typeof ui.selectRow, 'function');
    const boxes = [{value:'a',checked:true},{value:'b',checked:false}];
    const modes = [{value:'all',checked:true},{value:'selected',checked:false}];
    const doc = {querySelectorAll: selector => selector.includes('target_ids') ? boxes : modes};
    ui.selectRow(doc, 'b');
    assert.deepEqual(boxes.map(box=>box.checked), [false,true]);
    assert.deepEqual(modes.map(mode=>mode.checked), [false,true]);
});
