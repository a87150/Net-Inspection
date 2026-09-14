const test = require('node:test');
const assert = require('node:assert/strict');
const {installModalTransport} = require('../../static/app/js/common/modal_transport.js');

function fixture(href) {
    const listeners = {}, calls = [];
    let shown = 0, prevented = 0;
    const body = {querySelector: () => null, prepend() {}};
    const modal = {id:'collectionTemplateModal', querySelector: () => body,
        querySelectorAll: () => [],setAttribute() {},removeAttribute() {}};
    const link = {target:'', dataset:{modalLoad:'collectionTemplateModal'},closest:()=>null,
        hasAttribute: name => name==='data-modal-load',getAttribute:()=>href};
    const doc = {addEventListener:(name,handler)=>listeners[name]=handler,getElementById:()=>modal,
        createElement:()=>({dataset:{},setAttribute(){}})};
    const win = {location:{href:'http://localhost/assets/networks/',origin:'http://localhost'},
        bootstrap:{Modal:{getOrCreateInstance:()=>({show(){shown++;}})}},
        fetch:async(url)=>{calls.push(url);throw new Error('fixture interrupted request');}};
    installModalTransport(doc,win);
    return {click:()=>listeners.click({target:{closest:()=>link},preventDefault(){prevented++;}}),
        calls, state:()=>({shown,prevented})};
}

test('device list template entry opens a modal and fetches without page navigation', async () => {
    const state=fixture('/assets/networks/templates/');
    await state.click();
    assert.equal(state.calls.length,1);
    assert.deepEqual(state.state(),{shown:1,prevented:1});
});
test('modal loader never fetches a foreign origin', async () => {
    const state=fixture('https://unrelated.example/templates/');
    await state.click();
    assert.equal(state.calls.length,0);
    assert.deepEqual(state.state(),{shown:0,prevented:0});
});

test('per-device inspection settings use the current list modal', async () => {
    const state=fixture('/assets/networks/device-test/collection-settings/');
    await state.click();
    assert.equal(state.calls.length,1);
    assert.deepEqual(state.state(),{shown:1,prevented:1});
});
