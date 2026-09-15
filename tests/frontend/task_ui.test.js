const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = {module: {exports: {}}, document: {addEventListener() {}}, URL, Event};
vm.runInNewContext(fs.readFileSync(require.resolve('../../static/app/js/inspections/task_ui.js'), 'utf8'), context);
const ui = context.module.exports;

test('profile switch loads dialog state without navigation and retains row scope', () => {
    assert.equal(typeof ui.switchProfile, 'function');
    const location = {href:'http://localhost/assets/servers/?filter_server_type=linux', assign() {throw new Error('must not navigate');}};
    const form = {querySelectorAll: () => []};
    const select = {value:'memory-profile', closest: (selector) => selector === 'form' ? form : {id:'runTaskModal'}};
    const doc = {querySelectorAll: () => [{value:'row-b'}], querySelector: () => ({value:'selected'})};
    let requested;
    ui.switchProfile(select, location, doc, {navigate(modal, url) {requested = url;}});
    const url = new URL(requested);
    assert.equal(url.searchParams.get('task_profile'), 'memory-profile');
    assert.equal(url.searchParams.get('task_modal'), 'run');
    assert.equal(url.searchParams.get('task_targets'), 'row-b');
    assert.equal(url.searchParams.get('filter_server_type'), 'linux');
});

test('row action clears previous selection and chooses exactly clicked row', () => {
    assert.equal(typeof ui.selectRow, 'function');
    const boxes = [{value:'a',checked:true},{value:'b',checked:false}];
    const modes = [{value:'all',checked:true},{value:'selected',checked:false}];
    const single = {value:''};
    const doc = {querySelectorAll: selector => selector.includes('target_ids') ? boxes : modes, querySelector: () => single};
    ui.selectRow(doc, 'b');
    assert.deepEqual(boxes.map(box=>box.checked), [false,true]);
    assert.deepEqual(modes.map(mode=>mode.checked), [false,true]);
    assert.equal(single.value, 'b');
});

test('target device bulk actions affect only visible device choices', () => {
    assert.equal(typeof ui.updateTargetDeviceSelection, 'function');
    const choices = [
        {hidden:false, input:{checked:false}},
        {hidden:true, input:{checked:false}},
        {hidden:false, input:{checked:true}},
    ];
    ui.updateTargetDeviceSelection(choices, 'all');
    assert.deepEqual(choices.map(choice => choice.input.checked), [true,false,true]);
    ui.updateTargetDeviceSelection(choices, 'invert');
    assert.deepEqual(choices.map(choice => choice.input.checked), [false,false,false]);
});

test('target device filtering combines keyword and selected-state filters', () => {
    assert.equal(typeof ui.filterTargetDeviceChoices, 'function');
    const choices = [
        {label:'核心交换机 (192.0.2.11)', hidden:false, input:{checked:true}},
        {label:'接入交换机 (192.0.2.12)', hidden:false, input:{checked:false}},
    ];
    ui.filterTargetDeviceChoices(choices, '核心', 'all');
    assert.deepEqual(choices.map(choice => choice.hidden), [false,true]);
    ui.filterTargetDeviceChoices(choices, '', 'selected');
    assert.deepEqual(choices.map(choice => choice.hidden), [false,true]);
});

test('target device filtering narrows choices by vendor and type', () => {
    const choices = [
        {label:'核心交换机', vendor:'华为', deviceType:'核心', hidden:false, input:{checked:false}},
        {label:'接入交换机', vendor:'思科', deviceType:'接入', hidden:false, input:{checked:false}},
    ];
    ui.filterTargetDeviceChoices(choices, '', 'all', '华为', '核心');
    assert.deepEqual(choices.map(choice => choice.hidden), [false,true]);
});

test('generic checkbox bulk actions skip disabled choices', () => {
    assert.equal(typeof ui.updateCheckboxSelection, 'function');
    const inputs = [{checked:false, disabled:false}, {checked:true, disabled:true}, {checked:false, disabled:false}];
    ui.updateCheckboxSelection(inputs, 'all');
    assert.deepEqual(inputs.map(input => input.checked), [true,true,true]);
    ui.updateCheckboxSelection(inputs, 'invert');
    assert.deepEqual(inputs.map(input => input.checked), [false,true,false]);
});

test('returning to bulk mode clears the previous row-only target', () => {
    const boxes = [{value:'a', checked:true}, {value:'b', checked:false}];
    const mode = {value:'selected', type:'hidden'};
    const single = {value:'a'};
    const doc = {querySelectorAll: selector => selector.includes('target_ids') ? boxes : [mode], querySelector: () => single};
    ui.clearRowTarget(doc);
    assert.deepEqual(boxes.map(box => box.checked), [false, false]);
    assert.equal(mode.value, '');
    assert.equal(single.value, '');
});
test('device item choices follow selected capabilities, independent of search visibility', () => {
    const choices=[{input:{checked:true},items:['status_data'],hidden:false},
        {input:{checked:false},items:['channel_status','status_data'],hidden:true}];
    assert.deepEqual(Array.from(ui.applicableTargetItems(choices)),['status_data']);
    choices[1].input.checked=true;
    assert.deepEqual(Array.from(ui.applicableTargetItems(choices)).sort(),['channel_status','status_data']);
});

test('inherited inspection rules stay closed and custom rules show only the selected protocol', () => {
    const ctx={window:{},document:{addEventListener(){}},URL};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/app/js/inspections/task_ui.js'),'utf8'),ctx);
    const handlers={};
    const method={value:'snmp',addEventListener:(event,fn)=>handlers.method=fn};
    const mode={value:'inherit',addEventListener:(event,fn)=>handlers.mode=fn};
    const controls=[{disabled:false,value:'saved-command'}];
    const editor={hidden:false,querySelectorAll:()=>controls};
    const groups=['ssh','snmp'].map(value=>({dataset:{ruleProtocol:value},hidden:false}));
    const rule={dataset:{},querySelector:s=>s==='[data-rule-method] select'?method:s==='[data-rule-mode] select'?mode:editor,querySelectorAll:()=>groups,closest:()=>({addEventListener(){}})};
    ctx.window.AppTaskUI.bind({querySelectorAll:s=>s==='[data-inspection-rule]'?[rule]:[]});
    assert.equal(editor.hidden,true);assert.equal(controls[0].disabled,true);
    mode.value='custom';handlers.mode();
    assert.equal(editor.hidden,false);assert.equal(controls[0].disabled,false);
    assert.deepEqual(groups.map(g=>g.hidden),[true,false]);
    method.value='ssh';handlers.method();
    assert.deepEqual(groups.map(g=>g.hidden),[false,true]);
    mode.value='inherit';handlers.mode();assert.equal(editor.hidden,true);
    assert.equal(controls[0].value,'saved-command');
});


test('manual inspection selection changes notify the configuration download controls', () => {
    const changes = [];
    const boxes = ['a', 'b'].map(value => ({
        value, checked: false,
        dispatchEvent(event) { changes.push({value, checked: this.checked, type: event.type}); },
    }));
    const doc = {
        querySelectorAll: selector => selector.includes('target_ids') ? boxes : [],
        querySelector: () => null,
    };
    ui.selectRow(doc, 'b');
    assert.deepEqual(changes, [
        {value: 'a', checked: false, type: 'change'},
        {value: 'b', checked: true, type: 'change'},
    ]);
    changes.length = 0;
    ui.clearRowTarget(doc);
    assert.deepEqual(changes, [
        {value: 'a', checked: false, type: 'change'},
        {value: 'b', checked: false, type: 'change'},
    ]);
});


test('rule filters preserve hidden project values and reveal invalid fields', () => {
    const search = {value: ''};
    const scope = {value: 'all'};
    const count = {textContent: ''};
    const listeners = {};
    const cpuMode = {value: 'custom'};
    const memoryMode = {value: 'inherit'};
    const rules = [
        {dataset: {ruleLabel: 'CPU'}, querySelector: () => cpuMode, savedValue: 65, disabled: false},
        {dataset: {ruleLabel: '内存'}, querySelector: () => memoryMode, savedValue: 80, disabled: false},
    ];
    const container = {
        querySelector: selector => ({'[data-rule-search]': search, '[data-rule-scope]': scope, '[data-rule-count]': count}[selector]),
        querySelectorAll: () => rules,
        addEventListener: (event, callback) => {listeners[event] = callback;},
        closest: () => ({addEventListener() {}}),
    };
    context.bindInspectionRuleFilter(container);
    scope.value = 'custom'; listeners.change();
    assert.deepEqual(rules.map(row => row.hidden), [false, true]);
    search.value = 'missing'; listeners.input();
    assert.ok(rules.every(row => row.hidden));
    assert.deepEqual(rules.map(row => row.savedValue), [65, 80]);
    assert.ok(rules.every(row => row.disabled === false));
    assert.match(count.textContent, /0 \/ 2/);
    assert.equal(typeof listeners.invalid, 'function');
    listeners.invalid({target: {closest: () => rules[1]}});
    assert.deepEqual(rules.map(row => row.hidden), [false, false]);
    assert.equal(rules[1].open, true);
});


test('alert inheritance displays default channels without submitting override choices', () => {
    const events = {};
    const mode = {value: 'inherit'};
    const editor = {hidden: false, disabled: false};
    const inherited = {hidden: true};
    const form = {
        querySelector(selector) {
            return selector.includes('mode') ? mode : selector.includes('override') ? editor : inherited;
        },
        addEventListener(event, fn) { events[event] = fn; },
    };
    ui.bindAlertPolicy(form);
    assert.equal(editor.hidden, true);
    assert.equal(editor.disabled, true);
    assert.equal(inherited.hidden, false);
    mode.value = 'override'; events.change();
    assert.equal(editor.disabled, false);
    assert.equal(inherited.hidden, true);
    mode.value = 'inherit'; events['modal-draft-restored']();
    assert.equal(editor.disabled, true);
});
